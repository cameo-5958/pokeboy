const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const { webcrypto } = require("node:crypto");
const { Buffer, btoa } = require("node:buffer");
const { clearTimeout, setTimeout } = require("node:timers");
const { TextEncoder } = require("node:util");
const { URL } = require("node:url");

const source = fs.readFileSync("assets/emulator/mod-core.bin", "utf8");
let requests = 0;
let lastState = null;
const context = {
  AbortController: globalThis.AbortController,
  URL,
  TextEncoder,
  Uint8Array,
  Uint32Array,
  btoa,
  clearTimeout,
  console,
  crypto: webcrypto,
  fetch: async (url) => {
    requests += 1;
    const encoded = new URL(String(url)).searchParams.get("state");
    lastState = JSON.parse(Buffer.from(encoded, "base64url").toString("utf8"));
    return { ok: true, json: async () => ({ action: 1 }) };
  },
  setTimeout,
  PokeboyRuntime: { battleLinkEndpoint: "https://example.test/decision" },
};
vm.createContext(context);
vm.runInContext(source, context, { filename: "mod-core.bin" });

const ram = new Uint8Array(0x10000);
const memory = {
  read8: (address) => ram[address & 0xffff],
  write8: (address, value) => { ram[address & 0xffff] = value & 0xff; },
};
const write16 = (address, value) => {
  ram[address] = value >>> 8;
  ram[address + 1] = value & 0xff;
};

ram[0xd057] = 2; // trainer battle
ram[0xd89c] = 1;
ram[0xd8a4] = 1;
write16(0xd8a5, 30);
write16(0xd8c6, 30);
ram[0xd8ac] = 10;
ram[0xd8ad] = 20;
ram[0xd8c1] = 10;
ram[0xd8c2] = 10;
ram[0xcfe5] = 1;
write16(0xcfe6, 30);
ram[0xcfe8] = 0;
ram[0xcfed] = 10;
ram[0xcfee] = 20;
write16(0xcff4, 30);
ram[0xcffe] = 10;
ram[0xcfff] = 10;
ram[0xd163] = 1;
ram[0xd16b] = 4;
write16(0xd16c, 40);
write16(0xd18d, 40);
ram[0xd014] = 4;
write16(0xd015, 40);
write16(0xd023, 40);

const decide = context.PokeboyModCore["battle-link.decide"];
const cpu = { a: 4 };
decide(cpu, memory);
assert.equal(cpu.a, 0, "battle-start trap resets the host state");

cpu.a = 0;
decide(cpu, memory);
assert.equal(cpu.a, 0, "first decision poll remains pending");

setImmediate(() => {
  decide(cpu, memory);
  assert.equal(cpu.a, 1, "completed request resumes the ROM");
  assert.equal(ram[0xccdd], 20, "response selects the requested move");
  assert.equal(ram[0xcce2], 1, "response writes the requested move slot");
  assert.equal(requests, 1);

  ram[0xd068] = 0x20; // Hyper Beam recharge
  cpu.a = 0;
  decide(cpu, memory);
  assert.equal(cpu.a, 3, "forced turns bypass the public API");
  assert.equal(requests, 1);

  // maxTimeTillRandom: 0 resolves immediately with a random legal action and
  // never touches the network. Random resolutions report 4 (REJECTED).
  ram[0xd068] = 0;
  context.PokeboyRuntime.battleLinkMaxTimeTillRandomMs = 0;
  cpu.a = 0;
  decide(cpu, memory);
  assert.equal(cpu.a, 4, "zero maxTimeTillRandom resumes the ROM immediately as random");
  assert.equal(requests, 1, "zero maxTimeTillRandom skips the endpoint");
  assert.ok([10, 20].includes(ram[0xccdd]), "random fallback selects a legal move");

  // A finite value still opens the request and carries the configured
  // deadline in the snapshot.
  context.PokeboyRuntime.battleLinkMaxTimeTillRandomMs = 5000;
  cpu.a = 0;
  decide(cpu, memory);
  assert.equal(cpu.a, 0, "finite maxTimeTillRandom long-polls as before");
  setImmediate(() => {
    assert.equal(requests, 2);
    assert.equal(lastState.timeoutMs, 5000, "snapshot advertises the configured deadline");
    cpu.a = 0;
    decide(cpu, memory);
    assert.equal(cpu.a, 1, "remote decision resumes the ROM");

    // B while the turn is still open: the ROM sets the cancel flag (it only
    // does so while the turn can be undone), the host aborts the in-flight
    // request, and the re-entered turn opens attempt + 1.
    const resolvers = [];
    context.fetch = async (url) => {
      requests += 1;
      const encoded = new URL(String(url)).searchParams.get("state");
      lastState = JSON.parse(Buffer.from(encoded, "base64url").toString("utf8"));
      return new Promise((resolve) => { resolvers.push(resolve); });
    };
    context.PokeboyRuntime.battleLinkMaxTimeTillRandomMs = 5000;
    cpu.a = 0;
    decide(cpu, memory);
    assert.equal(cpu.a, 0, "cancellable request starts pending");
    const cancelAttempt = lastState.attempt;
    ram[0xcee9 + 2] = 1; // the ROM's B-cancel flag
    cpu.a = 0;
    decide(cpu, memory);
    assert.equal(cpu.a, 2, "B cancels the open turn");
    ram[0xcee9 + 2] = 0; // the ROM cancel path clears the flag
    cpu.a = 0;
    decide(cpu, memory);
    assert.equal(cpu.a, 0, "re-entered turn opens a fresh request");
    assert.equal(lastState.attempt, cancelAttempt + 1, "attempt advances after a cancel");
    resolvers[1]({ ok: true, json: async () => ({ action: 1 }) });
    setImmediate(() => {
      cpu.a = 0;
      decide(cpu, memory);
      assert.equal(cpu.a, 1, "the re-entered request resolves as remote");

      // Frame-driven failsafe: even if fetch never settles (wedged WebView
      // network path), the per-frame decide poll enforces the deadline.
      context.fetch = () => new Promise(() => {});
      context.PokeboyRuntime.battleLinkMaxTimeTillRandomMs = 80;
      cpu.a = 0;
      decide(cpu, memory);
      assert.equal(cpu.a, 0, "wedged fetch leaves the request pending");
      setTimeout(() => {
        cpu.a = 0;
        decide(cpu, memory);
        assert.equal(cpu.a, 4, "frame poll enforces the deadline as a random rejection");
        assert.ok([10, 20].includes(ram[0xccdd]), "failsafe selects a legal move");
        runLegalityTests();
      }, 150);
    });
  });
});

// GET-mode legality and response handling: wAICount seeding, disabled-move
// exclusion, invalid responses rejecting immediately, and 204 re-polling.
function runLegalityTests() {
  // wAICount seeding + disabled-move legality. Brock (0x22) has 5 AI uses
  // in TrainerAIPointers; wEnemyDisabledMove keeps the disabled move number
  // in its high nibble. A second healthy benched mon must be switchable
  // regardless of trainer class.
  ram[0xd89c] = 2;
  ram[0xd8d0] = 5; // slot 1: species
  write16(0xd8d1, 25); // slot 1: hp
  write16(0xd8f2, 25); // slot 1: max hp
  ram[0xd8f1] = 9; // slot 1: level
  context.fetch = async (url) => {
    requests += 1;
    const encoded = new URL(String(url)).searchParams.get("state");
    lastState = JSON.parse(Buffer.from(encoded, "base64url").toString("utf8"));
    return { ok: true, status: 200, json: async () => ({ action: 0 }) };
  };
  context.PokeboyRuntime.battleLinkMaxTimeTillRandomMs = 5000;
  ram[0xccdf] = 0xff;
  ram[0xd031] = 0x22;
  ram[0xd072] = 0x21; // move 2 disabled, one turn left
  cpu.a = 0;
  decide(cpu, memory);
  assert.equal(ram[0xccdf], 5, "wAICount is seeded from the class table");
  assert.equal(cpu.a, 0, "seeded snapshot long-polls as usual");
  setImmediate(() => {
    cpu.a = 0;
    decide(cpu, memory);
    assert.equal(cpu.a, 1, "remote decision after seeding resumes the ROM");
    const moves = lastState.legalActions.filter((action) => action.type === "move");
    assert.deepEqual(moves.map((move) => move.slot), [0], "disabled move slot is excluded");
    assert.equal(ram[0xccdd], 10, "remaining legal move is selected");
    const switches = lastState.legalActions.filter((action) => action.type === "switch");
    assert.deepEqual(switches.map((action) => action.partySlot), [1], "benched healthy mon is switchable for any class");
    assert.equal(switches[0].code, 17, "switch codes offset by 16");

    // Trainer battles never write wEnemyMonNicks (only _AddPartyMon's player
    // branch fills nick arrays), so trainer-side names must be derived from
    // the species id — the unwritten RAM here would decode as "???????????".
    assert.equal(lastState.trainer.active.nickname, "RHYDON", "trainer active is named by its species");
    assert.deepEqual(
      lastState.trainer.party.map((mon) => mon.nickname),
      ["RHYDON", "SPEAROW"],
      "trainer party names come from the species table",
    );
    assert.equal(lastState.mod, "battle-link@1.3.0", "snapshot change bumps the mod version");

    // Switching is class-agnostic and survives item exhaustion (aiCount 0);
    // items stay gated behind aiCount and the class table.
    ram[0xd072] = 0;
    ram[0xccdf] = 0;
    ram[0xd031] = 0x27; // Rocker: unconditional SUPER POTION while aiCount > 0
    cpu.a = 0;
    decide(cpu, memory);
    setImmediate(() => {
      cpu.a = 0;
      decide(cpu, memory);
      assert.equal(cpu.a, 1, "aiCount-0 turn resolves as remote");
      const kinds = new Set(lastState.legalActions.map((action) => action.type));
      assert.ok(kinds.has("switch"), "aiCount 0 keeps switching legal");
      assert.ok(!kinds.has("item"), "aiCount 0 exhausts items");
      ram[0xccdf] = 3;
      cpu.a = 0;
      decide(cpu, memory);
      setImmediate(() => {
        cpu.a = 0;
        decide(cpu, memory);
        assert.equal(cpu.a, 1, "restocked turn resolves as remote");
        assert.ok(
          lastState.legalActions.some((action) => action.itemName === "superPotion"),
          "aiCount > 0 restores class items",
        );
        runResponseTests();
      });
    });
  });
}

// GET-mode response handling: invalid responses rejecting immediately and
// 204 re-polling.
function runResponseTests() {
    // An endpoint response that is not a legal decision rejects the turn
    // immediately with a random action — no retries.
    ram[0xd072] = 0;
    const before = requests;
    context.fetch = async () => {
      requests += 1;
      return { ok: true, status: 200, json: async () => ({ action: 99 }) };
    };
    cpu.a = 0;
    decide(cpu, memory);
    assert.equal(cpu.a, 0, "invalid response test opens a request");
    setImmediate(() => {
      cpu.a = 0;
      decide(cpu, memory);
      assert.equal(cpu.a, 4, "illegal decision resolves as an immediate random rejection");
      assert.equal(requests, before + 1, "an invalid response is not retried");
      assert.ok([10, 20].includes(ram[0xccdd]), "random rejection selects a legal move");

      // 204 marks an upstream long-poll cycle: reopen immediately, no
      // backoff, and accept the eventual decision as remote.
      let calls = 0;
      context.fetch = async () => {
        requests += 1;
        calls += 1;
        if (calls < 3) return { ok: true, status: 204, json: async () => { throw new Error("no body"); } };
        return { ok: true, status: 200, json: async () => ({ action: 0 }) };
      };
      cpu.a = 0;
      decide(cpu, memory);
      setImmediate(() => {
        cpu.a = 0;
        decide(cpu, memory);
        assert.equal(calls, 3, "204 responses re-poll without backoff");
        assert.equal(cpu.a, 1, "decision after 204 cycles is a normal remote resolution");
        runDiscordTests();
      });
    });
}

// Discord mode: the bot runs on the backend. The mod core long-polls its own
// backend's decision endpoint (which mirrors the snapshot to the bot) and
// posts lifecycle events; the deadline fallback stays authoritative.
function runDiscordTests() {
  const events = [];
  context.PokeboyRuntime.battleLinkMode = "discord";
  context.PokeboyRuntime.backendUrl = "https://backend.test/"; // trailing slash on purpose
  context.PokeboyRuntime.battleLinkMaxTimeTillRandomMs = 5000;
  context.fetch = async (url, options) => {
    const target = String(url);
    if (target === "https://backend.test/battle-link/event") {
      events.push(JSON.parse(options.body));
      return { ok: true, json: async () => ({ ok: true }) };
    }
    assert.ok(
      target.startsWith("https://backend.test/battle-link/decision?"),
      "discord mode polls its own backend's decision endpoint",
    );
    requests += 1;
    const encoded = new URL(target).searchParams.get("state");
    lastState = JSON.parse(Buffer.from(encoded, "base64url").toString("utf8"));
    // The backend long-poll never settles in this test; decisions arrive
    // through the injection path instead.
    return new Promise(() => {});
  };

  const cpu = { a: 4 };
  decide(cpu, memory);
  assert.equal(cpu.a, 0, "battle-start trap resets the host state");

  cpu.a = 0;
  decide(cpu, memory);
  assert.equal(cpu.a, 0, "discord decision stays pending");
  const snapshot = lastState;
  assert.ok(snapshot, "discord mode sends the snapshot to the backend");
  assert.equal(snapshot.timeoutMs, 5000, "snapshot advertises the configured deadline");
  assert.ok(Array.isArray(snapshot.legalActions) && snapshot.legalActions.length >= 2);
  assert.ok(String(snapshot.core || "").startsWith("mod-core@"), "snapshot names the host core version");

  const key = { battleId: snapshot.battleId, turn: snapshot.turn, attempt: snapshot.attempt };
  assert.equal(
    context.PokeboyBattleLinkDeliver({ ...key, action: 999 }),
    false,
    "illegal actions are rejected",
  );
  assert.equal(
    context.PokeboyBattleLinkDeliver({ ...key, battleId: "someone-else", action: 1 }),
    false,
    "stale battle ids are rejected",
  );
  assert.equal(
    context.PokeboyBattleLinkDeliver({ ...key, action: 1 }),
    true,
    "legal deliveries are accepted",
  );
  cpu.a = 0;
  decide(cpu, memory);
  assert.equal(cpu.a, 1, "discord decision resumes the ROM");
  assert.equal(ram[0xccdd], 20, "discord decision selects the delivered move");
  const resolved = events.find((event) => event.type === "resolved");
  assert.ok(resolved, "resolution is posted to the backend");
  assert.equal(resolved.detail.source, "discord");
  assert.equal(resolved.detail.code, 1);

  // A forced turn cancels the outstanding request and must tell the backend
  // so the Discord widget can be closed.
  cpu.a = 0;
  decide(cpu, memory);
  assert.equal(cpu.a, 0, "next turn opens a new pending decision");
  ram[0xd068] = 0x20; // Hyper Beam recharge forces the action
  cpu.a = 0;
  decide(cpu, memory);
  assert.equal(cpu.a, 3, "forced turns keep their native contract");
  ram[0xd068] = 0;
  const cancel = events.find((event) => event.type === "cancel");
  assert.ok(cancel, "cancellation is posted to the backend");
  assert.equal(cancel.detail.reason, "native-action");

  // Battle watch: once the battle leaves trainer-battle state the backend
  // gets a battle-end so the Discord session can auto-disconnect.
  ram[0xd057] = 0;
  setTimeout(() => {
    const end = events.find((event) => event.type === "battle-end");
    assert.ok(end, "battle watch reports the battle end");
    assert.equal(end.detail.reason, "battle-over");
    runPrimeAndFaintTests();
  }, 1300);
}

// Turn-start priming (trap status 5) and forced faint switch-ins (status 6).
function runPrimeAndFaintTests() {
  context.PokeboyRuntime.battleLinkMode = "get";
  context.PokeboyRuntime.battleLinkMaxTimeTillRandomMs = 5000;
  ram[0xd057] = 2;
  ram[0xd068] = 0;
  ram[0xd89c] = 2;
  write16(0xd8a5, 30); // roster slot 0 restored
  write16(0xd8d1, 25); // roster slot 1 alive
  ram[0xcfe8] = 0;
  write16(0xcfe6, 12); // live battle-struct HP diverges from the stale roster

  const cpu = { a: 4 };
  decide(cpu, memory);
  assert.equal(cpu.a, 0, "battle-start trap resets the host state");

  // The battle-opening send-out reaches the faint trap before any decision
  // has resolved: it must stay native.
  cpu.a = 6;
  decide(cpu, memory);
  assert.equal(cpu.a, 3, "battle-opening send-out bypasses to the native scan");

  context.fetch = async (url) => {
    requests += 1;
    const encoded = new URL(String(url)).searchParams.get("state");
    lastState = JSON.parse(Buffer.from(encoded, "base64url").toString("utf8"));
    return { ok: true, status: 200, json: async () => ({ action: 0 }) };
  };
  const before = requests;
  cpu.a = 5;
  decide(cpu, memory);
  assert.equal(cpu.a, 0, "prime returns immediately");
  assert.equal(requests, before + 1, "prime opens the turn request");
  setImmediate(() => {
    cpu.a = 5;
    decide(cpu, memory);
    assert.equal(cpu.a, 0, "prime never consumes a resolved request");
    cpu.a = 0;
    decide(cpu, memory);
    assert.equal(cpu.a, 1, "the later poll consumes the primed request");
    assert.equal(lastState.phase, "turn", "turn snapshots carry their phase");
    assert.equal(lastState.trainer.party[0].hp, 12,
      "the on-field roster row shows live battle-struct HP");

    // Forced switch with a single healthy replacement: resolved on the spot,
    // no round trip.
    const noChoice = requests;
    write16(0xcfe6, 0); // active fainted
    write16(0xd8a5, 0); // the faint handler zeroed its roster HP
    cpu.a = 6;
    decide(cpu, memory);
    assert.equal(cpu.a, 1, "single-option faint switch resolves immediately");
    assert.equal(requests, noChoice, "no request is opened for a forced-only switch");
    assert.equal(ram[0xcee9], 1, "faint resolution marks a switch");
    assert.equal(ram[0xcee9 + 1], 1, "faint resolution carries the party slot");

    // Forced switch with a real choice goes to the decision source with
    // switch-only legal actions.
    ram[0xd89c] = 3;
    ram[0xd8fc] = 7; // roster slot 2: species
    write16(0xd8fd, 20); // roster slot 2: hp
    write16(0xd91e, 20); // roster slot 2: max hp
    ram[0xd91d] = 8; // roster slot 2: level
    context.fetch = async (url) => {
      requests += 1;
      const encoded = new URL(String(url)).searchParams.get("state");
      lastState = JSON.parse(Buffer.from(encoded, "base64url").toString("utf8"));
      return { ok: true, status: 200, json: async () => ({ action: 18 }) };
    };
    cpu.a = 6;
    decide(cpu, memory);
    assert.equal(cpu.a, 0, "multi-option faint switch opens a request");
    setImmediate(() => {
      cpu.a = 6;
      decide(cpu, memory);
      assert.equal(cpu.a, 1, "remote faint decision resumes the ROM");
      assert.equal(lastState.phase, "faint-switch", "faint snapshots carry their phase");
      assert.ok(lastState.legalActions.every((action) => action.type === "switch"),
        "faint snapshots offer only switches");
      assert.deepEqual(lastState.legalActions.map((action) => action.code), [17, 18],
        "fainted and on-field mons are excluded");
      assert.equal(ram[0xcee9 + 1], 2, "the chosen replacement slot reaches the ROM");
      assert.equal(lastState.mod, "battle-link@1.3.0", "snapshot change bumps the mod version");
      console.log("Battle Link browser host tests passed");
    });
  });
}
