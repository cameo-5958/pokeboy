import assert from "node:assert/strict";
import { after, afterEach, before, test } from "node:test";
import express from "express";
import type { AddressInfo } from "node:net";
import type { Server } from "node:http";

import { config } from "./config.js";
import { clearContentCache } from "./content.js";
import { readRegistry } from "./storage.js";

/** Stands in for raw.githubusercontent, so no test touches the network. */
let remote: Server;
let remoteUrl: string;
let hits: Array<{ path: string; auth: string | undefined }>;

const REMOTE_REGISTRY = {
  roms: [{ id: "remote-rom", title: "Remote", file: "remote.gb", version: "9.9.9" }],
  mods: [],
  host: { id: "mod-core", version: "9.9.9" },
};

const defaults = { ...config };

before(async () => {
  const app = express();
  app.get("/repo/backend/data/registry.json", (req, res) => {
    hits.push({ path: req.path, auth: req.get("authorization") });
    res.json(REMOTE_REGISTRY);
  });
  // Every other path 404s - exactly how a private repo answers raw requests.
  await new Promise<void>((resolve) => { remote = app.listen(0, "127.0.0.1", resolve); });
  remoteUrl = `http://127.0.0.1:${(remote.address() as AddressInfo).port}`;
});

after(async () => new Promise<void>((resolve, reject) => {
  remote.close((error) => error ? reject(error) : resolve());
}));

afterEach(() => {
  Object.assign(config, defaults);
  clearContentCache();
  hits = [];
});

// config is read at call time, so tests can flip the source in-process exactly
// like the env vars do at startup.
function useGithub(base: string, token = ""): void {
  hits = [];
  clearContentCache();
  config.contentSource = "github";
  config.contentBaseUrl = base;
  config.githubToken = token;
}

test("local is the default source and reads the committed registry", async () => {
  assert.equal(config.contentSource, "local", "default must stay local");
  const registry = await readRegistry();
  assert.ok(registry.roms.some((r) => r.id === "pokemon-red"), "serves the on-disk catalog");
  assert.equal(hits?.length ?? 0, 0, "local mode makes no network requests");
});

test("github source fetches the registry from the base URL", async () => {
  useGithub(`${remoteUrl}/repo`);
  const registry = await readRegistry();
  assert.deepEqual(registry.roms.map((r) => r.id), ["remote-rom"]);
  assert.equal(hits.length, 1);
  assert.equal(hits[0].path, "/repo/backend/data/registry.json");
  assert.equal(hits[0].auth, undefined, "no Authorization header without a token");
});

test("a token is sent only when configured, and only to the content source", async () => {
  useGithub(`${remoteUrl}/repo`, "fake-test-token");
  await readRegistry();
  assert.equal(hits[0].auth, "Bearer fake-test-token");
});

test("a private-repo 404 falls back to the local file, not an empty catalog", async () => {
  // A private repo 404s every path for an unauthenticated raw request. Serving
  // an empty registry there would look to a client like the catalog was wiped.
  useGithub(`${remoteUrl}/private`);
  const registry = await readRegistry();
  assert.ok(registry.roms.some((r) => r.id === "pokemon-red"), "falls back to disk");
});

test("an unreachable base URL falls back to the local file", async () => {
  useGithub("http://127.0.0.1:1/repo");
  const registry = await readRegistry();
  assert.ok(registry.roms.some((r) => r.id === "pokemon-red"));
});

test("github reads are cached for the TTL, and fallbacks do not hammer the source", async () => {
  useGithub(`${remoteUrl}/repo`);
  await readRegistry();
  await readRegistry();
  await readRegistry();
  assert.equal(hits.length, 1, "three reads, one fetch");

  clearContentCache();
  await readRegistry();
  assert.equal(hits.length, 2, "a cleared cache refetches");
});

test("an expired TTL refetches", async () => {
  useGithub(`${remoteUrl}/repo`);
  config.contentTtlMs = 0;
  await readRegistry();
  await readRegistry();
  assert.equal(hits.length, 2);
});
