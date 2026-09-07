import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { after, before, test } from "node:test";
import express from "express";
import type { AddressInfo } from "node:net";
import type { Server } from "node:http";

let server: Server;
let baseUrl = "";
let cookie = "";
let wasmHash = "";
let mintToken: (exp: number) => string;

before(async () => {
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), "pokeboy-web-"));
  const webDir = path.join(tmp, "web");
  const assetsDir = path.join(tmp, "assets");
  await fs.mkdir(webDir, { recursive: true });
  await fs.mkdir(assetsDir, { recursive: true });
  await fs.writeFile(path.join(webDir, "index.html"), "PLAYER PAGE");
  await fs.writeFile(path.join(webDir, "login.html"), "LOGIN PAGE");
  await fs.writeFile(path.join(webDir, "shell.css"), "body{}");
  await fs.writeFile(path.join(assetsDir, "gbcore.wasm"), "fake wasm bytes");
  await fs.writeFile(path.join(assetsDir, "gbcore.bin"), "// glue");
  await fs.writeFile(path.join(assetsDir, "embed.html"), "<html>EMBED</html>");
  wasmHash = createHash("sha256").update("fake wasm bytes").digest("hex");

  process.env.WEB_USERNAME = "tester";
  process.env.WEB_PASSWORD = "sekrit";
  process.env.WEB_SESSION_SECRET = "hush";
  process.env.WEB_DIR = webDir;
  process.env.EMULATOR_ASSETS_DIR = assetsDir;
  process.env.WEB_DATA_DIR = path.join(tmp, "data");

  const { webRouter } = await import("./web.js");
  const session = await import("../websession.js");
  mintToken = session.mintToken;

  const app = express();
  app.use("/web", webRouter);
  // Replica of the /api guard's session acceptance (the real one is inline in index.ts).
  app.get("/api/registry", (req, res) => {
    if (session.hasWebSession(req)) return res.json({ roms: [] });
    res.status(401).json({ error: "Invalid or missing API key" });
  });
  await new Promise<void>((resolve) => {
    server = app.listen(0, "127.0.0.1", resolve);
  });
  baseUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});

after(async () =>
  new Promise<void>((resolve, reject) => {
    server.close((error) => (error ? reject(error) : resolve()));
  }),
);

function postJson(url: string, body: unknown, headers: Record<string, string> = {}) {
  return fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json", ...headers },
    body: JSON.stringify(body),
  });
}

test("bad credentials are rejected", async () => {
  const res = await postJson(`${baseUrl}/web/login`, { username: "tester", password: "wrong" });
  assert.equal(res.status, 401);
});

test("login issues an HttpOnly session cookie", async () => {
  const res = await postJson(`${baseUrl}/web/login`, { username: "tester", password: "sekrit" });
  assert.equal(res.status, 200);
  const setCookie = res.headers.get("set-cookie") ?? "";
  assert.match(setCookie, /pokeboy_web=/);
  assert.match(setCookie, /HttpOnly/i);
  cookie = setCookie.split(";")[0];
});

test("session gates the api, player page, and login page stays public", async () => {
  assert.equal((await fetch(`${baseUrl}/api/registry`)).status, 401);
  assert.equal((await fetch(`${baseUrl}/api/registry`, { headers: { cookie } })).status, 200);

  const anon = await fetch(`${baseUrl}/web/`, { redirect: "manual" });
  assert.equal(anon.status, 302);
  assert.equal(anon.headers.get("location"), "/web/login.html");
  const page = await fetch(`${baseUrl}/web/`, { headers: { cookie } });
  assert.equal(await page.text(), "PLAYER PAGE");

  const login = await fetch(`${baseUrl}/web/login.html`);
  assert.equal(login.status, 200);
  assert.equal(await login.text(), "LOGIN PAGE");
});

test("expired session tokens are rejected", async () => {
  const stale = `pokeboy_web=${mintToken(Date.now() - 1000)}`;
  assert.equal((await fetch(`${baseUrl}/web/session`, { headers: { cookie: stale } })).status, 401);
  assert.equal((await fetch(`${baseUrl}/web/session`, { headers: { cookie } })).status, 200);
});

test("meta reports the wasm hash", async () => {
  assert.equal((await fetch(`${baseUrl}/web/meta`)).status, 401);
  const meta = (await (await fetch(`${baseUrl}/web/meta`, { headers: { cookie } })).json()) as {
    coreHash: string;
  };
  assert.equal(meta.coreHash, wasmHash);
});

test("emulator runtime files are gated and correctly typed", async () => {
  assert.equal((await fetch(`${baseUrl}/web/emulator/gbcore.bin`)).status, 401);
  const glue = await fetch(`${baseUrl}/web/emulator/gbcore.bin`, { headers: { cookie } });
  assert.equal(glue.status, 200);
  assert.match(glue.headers.get("content-type") ?? "", /text\/javascript/);
  const wasm = await fetch(`${baseUrl}/web/emulator/gbcore.wasm`, { headers: { cookie } });
  assert.match(wasm.headers.get("content-type") ?? "", /application\/wasm/);
  assert.equal((await fetch(`${baseUrl}/web/emulator/nope.txt`, { headers: { cookie } })).status, 404);
});

test("save state slots roundtrip with server stamping", async () => {
  const put = await fetch(`${baseUrl}/web/states/pokemon-red/2`, {
    method: "PUT",
    headers: { "content-type": "application/json", cookie },
    body: JSON.stringify({ base64: "AAAA", gz: true, heapLen: 4, cartridgeVersion: "1.0.0", mods: [] }),
  });
  assert.equal(put.status, 200);
  const stamped = (await put.json()) as { ts: number; coreHash: string };
  assert.equal(stamped.coreHash, wasmHash);
  assert.ok(stamped.ts > 0);

  const list = (await (
    await fetch(`${baseUrl}/web/states/pokemon-red`, { headers: { cookie } })
  ).json()) as Array<{ slot: number }>;
  assert.deepEqual(list.map((s) => s.slot), [2]);

  const record = (await (
    await fetch(`${baseUrl}/web/states/pokemon-red/2`, { headers: { cookie } })
  ).json()) as { base64: string; coreHash: string; cartridgeVersion: string };
  assert.equal(record.base64, "AAAA");
  assert.equal(record.coreHash, wasmHash);
  assert.equal(record.cartridgeVersion, "1.0.0");

  const del = await fetch(`${baseUrl}/web/states/pokemon-red/2`, { method: "DELETE", headers: { cookie } });
  assert.equal(del.status, 204);
  assert.equal((await fetch(`${baseUrl}/web/states/pokemon-red/2`, { headers: { cookie } })).status, 404);
});

test("state params are validated", async () => {
  assert.equal((await fetch(`${baseUrl}/web/states/pokemon-red/9`, { headers: { cookie } })).status, 400);
  assert.equal((await fetch(`${baseUrl}/web/states/..%2Fetc/1`, { headers: { cookie } })).status, 400);
});

test("battery saves roundtrip", async () => {
  const empty = await (await fetch(`${baseUrl}/web/battery/pokemon-red`, { headers: { cookie } })).json();
  assert.deepEqual(empty, { ts: 0, data: null });
  const put = await fetch(`${baseUrl}/web/battery/pokemon-red`, {
    method: "PUT",
    headers: { "content-type": "application/json", cookie },
    body: JSON.stringify({ ts: 123, data: "U0FWRQ==" }),
  });
  assert.equal(put.status, 200);
  const back = await (await fetch(`${baseUrl}/web/battery/pokemon-red`, { headers: { cookie } })).json();
  assert.deepEqual(back, { ts: 123, data: "U0FWRQ==" });
});

// Last: poisons the limiter for this process's shared client ip.
test("login rate limiter kicks in after repeated failures", async () => {
  for (let i = 0; i < 10; i++) {
    await postJson(`${baseUrl}/web/login`, { username: "x", password: "y" });
  }
  const blocked = await postJson(`${baseUrl}/web/login`, { username: "tester", password: "sekrit" });
  assert.equal(blocked.status, 429);
});
