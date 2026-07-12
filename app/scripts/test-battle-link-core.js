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
      console.log("Battle Link browser host tests passed");
    }, 150);
  });
});
