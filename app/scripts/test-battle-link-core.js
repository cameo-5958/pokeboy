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
  // never touches the network.
  ram[0xd068] = 0;
  context.PokeboyRuntime.battleLinkMaxTimeTillRandomMs = 0;
  cpu.a = 0;
  decide(cpu, memory);
  assert.equal(cpu.a, 1, "zero maxTimeTillRandom resumes the ROM immediately");
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
      assert.equal(cpu.a, 1, "frame poll enforces the deadline despite a wedged fetch");
      assert.ok([10, 20].includes(ram[0xccdd]), "failsafe selects a legal move");
      runDiscordTests();
    }, 150);
  });
});

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

  // The player backing out (B) must tell the host so the embed can be closed.
  cpu.a = 0;
  decide(cpu, memory);
  assert.equal(cpu.a, 0, "next turn opens a new pending decision");
  ram[0xcee9 + 2] = 1;
  cpu.a = 0;
  decide(cpu, memory);
  assert.equal(cpu.a, 2, "player cancel keeps its native contract");
  ram[0xcee9 + 2] = 0;
  const cancel = hostCalls.find((call) => call[0] === "cancel");
  assert.ok(cancel, "cancellation is reported to the host");
  assert.equal(cancel[1].reason, "player-cancelled");

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
