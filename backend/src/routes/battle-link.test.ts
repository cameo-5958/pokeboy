import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import express from "express";
import type { AddressInfo } from "node:net";
import type { Server } from "node:http";

import { battleLinkRouter } from "./battle-link.js";

let server: Server;
let baseUrl: string;

before(async () => {
  const app = express();
  app.use(express.json());
  app.use("/battle-link", battleLinkRouter);
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
