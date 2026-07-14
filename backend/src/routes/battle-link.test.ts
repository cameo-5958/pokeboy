import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { after, before, test } from "node:test";
import express from "express";
import type { AddressInfo } from "node:net";
import type { Server } from "node:http";

import { config } from "../config.js";
import { battleLinkRouter } from "./battle-link.js";
import { modsRouter } from "./mods.js";

let server: Server;
let baseUrl: string;

before(async () => {
  const app = express();
  app.use(express.json());
  app.use("/battle-link", battleLinkRouter);
  app.use("/api/mods", modsRouter);
  await new Promise<void>((resolve) => { server = app.listen(0, "127.0.0.1", resolve); });
  baseUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});

after(async () => new Promise<void>((resolve, reject) => {
  server.close((error) => error ? reject(error) : resolve());
}));

test("console and health endpoints are public", async () => {
  const page = await fetch(`${baseUrl}/battle-link`);
  assert.equal(page.status, 200);
  assert.match(await page.text(), /BREADWINNER: BATTLE LINK/);
  const health = await fetch(`${baseUrl}/battle-link/health`);
  assert.deepEqual(await health.json(), { ok: true, pending: 0 });
});

test("long poll exposes state and returns a legal command", async () => {
  const state = {
    protocol: 1,
    battleId: "12345678-1234-4123-8123-123456789abc",
    turn: 2,
    attempt: 0,
    legalActions: [{ code: 0, type: "move", slot: 0, move: 33 }],
  };
  const encoded = Buffer.from(JSON.stringify(state)).toString("base64url");
  const decision = fetch(`${baseUrl}/battle-link/decision?state=${encoded}`);
  await new Promise((resolve) => setTimeout(resolve, 20));

  const pending = await fetch(`${baseUrl}/battle-link/pending`).then((response) => response.json()) as {
    requests: Array<{ key: string }>;
  };
  assert.equal(pending.requests.length, 1);
  const command = await fetch(`${baseUrl}/battle-link/command`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ key: pending.requests[0].key, action: 0 }),
  });
  assert.equal(command.status, 200);
  assert.deepEqual(await (await decision).json(), { action: 0 });
});

test("illegal commands are rejected", async () => {
  const response = await fetch(`${baseUrl}/battle-link/command`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ key: "missing", action: 99 }),
  });
  assert.equal(response.status, 404);
});

test("a cancel event closes the pending decision", async () => {
  const state = {
    protocol: 1,
    battleId: "cancel-me-1234-4123-8123-123456789abc",
    turn: 0,
    attempt: 0,
    legalActions: [{ code: 0, type: "move", slot: 0, move: 33 }],
  };
  const encoded = Buffer.from(JSON.stringify(state)).toString("base64url");
  const decision = fetch(`${baseUrl}/battle-link/decision?state=${encoded}`);
  await new Promise((resolve) => setTimeout(resolve, 20));

  const cancel = await fetch(`${baseUrl}/battle-link/event`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      type: "cancel",
      detail: { battleId: state.battleId, turn: 0, attempt: 0, reason: "player-cancelled" },
    }),
  });
  assert.equal(cancel.status, 200);
  assert.equal((await decision).status, 204, "the open long-poll is released empty");
  const pending = await fetch(`${baseUrl}/battle-link/pending`).then((r) => r.json()) as {
    requests: unknown[];
  };
  assert.equal(pending.requests.length, 0, "a cancelled decision leaves the console");
});

test("resolved and battle-end events are accepted; unknown types are not", async () => {
  for (const type of ["resolved", "battle-end", "emulator-reset"]) {
    const response = await fetch(`${baseUrl}/battle-link/event`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ type, detail: { battleId: "b", turn: 0, attempt: 0 } }),
    });
    assert.equal(response.status, 200, `${type} events are accepted`);
  }
  const bad = await fetch(`${baseUrl}/battle-link/event`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ type: "nonsense" }),
  });
  assert.equal(bad.status, 400);
});

test("the host core is served from the registry entry", async () => {
  const response = await fetch(`${baseUrl}/api/mods/host/mod-core`);
  assert.equal(response.status, 200);
  const served = Buffer.from(await response.arrayBuffer());
  const bundled = await fs.readFile(config.hostCoreFile);
  assert.ok(served.equals(bundled), "served host core is byte-identical to the bundled app asset");
  assert.match(served.toString("utf8"), /PokeboyModCore/, "host core installs the mod host imports");

  const unknown = await fetch(`${baseUrl}/api/mods/host/unknown`);
  assert.equal(unknown.status, 404, "ids not in the registry are rejected");
});
