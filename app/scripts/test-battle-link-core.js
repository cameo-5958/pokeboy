const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const { webcrypto } = require("node:crypto");
const { btoa } = require("node:buffer");
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

    // A committed local action is independent from Game Boy Back input. Even
    // an old ROM-side cancellation flag must neither abort nor duplicate the
    // one request already in flight for this turn.
    let resolveCommittedRequest;
    context.fetch = async (url) => {
      requests += 1;
      const encoded = new URL(String(url)).searchParams.get("state");
      lastState = JSON.parse(Buffer.from(encoded, "base64url").toString("utf8"));
      return new Promise((resolve) => { resolveCommittedRequest = resolve; });
    };
    context.PokeboyRuntime.battleLinkMaxTimeTillRandomMs = 5000;
    cpu.a = 0;
    decide(cpu, memory);
    assert.equal(cpu.a, 0, "committed request starts pending");
    const committedRequestCount = requests;
    ram[0xcee9 + 2] = 1; // legacy B-cancel flag
    decide(cpu, memory);
    assert.equal(cpu.a, 0, "Back cannot cancel a committed request");
    assert.equal(requests, committedRequestCount, "Back cannot duplicate a committed request");
    resolveCommittedRequest({ ok: true, json: async () => ({ action: 1 }) });
    setImmediate(() => {
      cpu.a = 0;
      decide(cpu, memory);
      assert.equal(cpu.a, 1, "the original committed request still resolves");
      assert.equal(requests, committedRequestCount, "one request resolves the committed turn");
      ram[0xcee9 + 2] = 0;

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
  // in its high nibble.
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
  });
}

// Discord mode: decisions travel over the host bridge (native Discord bot)
// instead of the GET endpoint; the deadline fallback stays authoritative.
function runDiscordTests() {
  const hostCalls = [];
  context.PokeboyBattleLinkHost = {
    request: (detail) => hostCalls.push(["request", detail]),
    cancel: (detail) => hostCalls.push(["cancel", detail]),
    resolved: (detail) => hostCalls.push(["resolved", detail]),
    battleEnd: (detail) => hostCalls.push(["battleEnd", detail]),
  };
  context.PokeboyRuntime.battleLinkMode = "discord";
  context.PokeboyRuntime.battleLinkMaxTimeTillRandomMs = 5000;
  context.fetch = () => { throw new Error("discord mode must never fetch"); };

  const cpu = { a: 4 };
  decide(cpu, memory);
  assert.equal(cpu.a, 0, "battle-start trap resets the host state");

  cpu.a = 0;
  decide(cpu, memory);
  assert.equal(cpu.a, 0, "discord decision stays pending");
  const request = hostCalls.find((call) => call[0] === "request");
  assert.ok(request, "discord mode hands the snapshot to the host bridge");
  const snapshot = request[1];
  assert.equal(snapshot.timeoutMs, 5000, "snapshot advertises the configured deadline");
  assert.ok(Array.isArray(snapshot.legalActions) && snapshot.legalActions.length >= 2);

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
  const resolved = hostCalls.find((call) => call[0] === "resolved");
  assert.ok(resolved, "resolution is reported back to the host");
  assert.equal(resolved[1].source, "discord");
  assert.equal(resolved[1].code, 1);

  // A forced turn cancels the outstanding request and must tell the host so
  // the Discord embed can be closed.
  cpu.a = 0;
  decide(cpu, memory);
  assert.equal(cpu.a, 0, "next turn opens a new pending decision");
  ram[0xd068] = 0x20; // Hyper Beam recharge forces the action
  cpu.a = 0;
  decide(cpu, memory);
  assert.equal(cpu.a, 3, "forced turns keep their native contract");
  ram[0xd068] = 0;
  const cancel = hostCalls.find((call) => call[0] === "cancel");
  assert.ok(cancel, "cancellation is reported to the host");
  assert.equal(cancel[1].reason, "native-action");

  // Battle watch: once the battle leaves trainer-battle state the host gets
  // a battle-end so the Discord session can auto-disconnect.
  ram[0xd057] = 0;
  setTimeout(() => {
    const end = hostCalls.find((call) => call[0] === "battleEnd");
    assert.ok(end, "battle watch reports the battle end");
    assert.equal(end[1].reason, "battle-over");
    console.log("Battle Link browser host tests passed");
  }, 1300);
}
