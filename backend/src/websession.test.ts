// Runs WITHOUT WEB_* environment configured: verifies the auth layer fails
// closed. The configured happy paths are covered over HTTP in routes/web.test.ts.
import assert from "node:assert/strict";
import { before, test } from "node:test";
import type { Request } from "express";

let ws: typeof import("./websession.js");

before(async () => {
  delete process.env.WEB_USERNAME;
  delete process.env.WEB_PASSWORD;
  delete process.env.WEB_SESSION_SECRET;
  ws = await import("./websession.js");
});

function fakeReq(cookie: string): Request {
  return { headers: { cookie } } as unknown as Request;
}

test("auth fails closed while credentials are unconfigured", () => {
  assert.equal(ws.checkCredentials("anything", "anything"), false);
  assert.equal(ws.checkCredentials("", ""), false);
  const token = ws.mintToken(Date.now() + 60_000);
  assert.equal(ws.hasWebSession(fakeReq(`${ws.SESSION_COOKIE}=${token}`)), false);
});

test("malformed cookies never authenticate", () => {
  assert.equal(ws.hasWebSession(fakeReq("")), false);
  assert.equal(ws.hasWebSession(fakeReq(`${ws.SESSION_COOKIE}=nodot`)), false);
  assert.equal(ws.hasWebSession(fakeReq(`${ws.SESSION_COOKIE}=123.`)), false);
});

test("login rate limiter blocks after 10 failures, per ip", () => {
  for (let i = 0; i < 10; i++) ws.noteLoginFailure("10.0.0.1");
  assert.equal(ws.loginBlocked("10.0.0.1"), true);
  assert.equal(ws.loginBlocked("10.0.0.2"), false);
});
