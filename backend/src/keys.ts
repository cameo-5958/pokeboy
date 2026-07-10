/**
 * Device API key store. Keys are minted on request (CLI `npm run key:new` or
 * the admin-guarded `POST /api/keys` endpoint), persisted to a JSON file, and
 * used by the app on your phone via the `x-api-key` header.
 *
 * Auth turns on automatically once at least one key exists (or the legacy
 * `API_KEY` env is set): before that the API is open for local development.
 */

import { randomBytes } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";

import { config, paths } from "./config.js";

export type ApiKey = {
  /** The secret token the client sends. */
  key: string;
  /** Human label, e.g. the device name. */
  label: string;
  createdAt: string;
};

async function read(): Promise<ApiKey[]> {
  try {
    const raw = await fs.readFile(paths.keys(), "utf8");
    const parsed = JSON.parse(raw) as ApiKey[];
    return Array.isArray(parsed) ? parsed : [];
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === "ENOENT") return [];
    throw e;
  }
}

async function write(keys: ApiKey[]): Promise<void> {
  await fs.mkdir(path.dirname(paths.keys()), { recursive: true });
  await fs.writeFile(paths.keys(), JSON.stringify(keys, null, 2) + "\n");
}

export async function listKeys(): Promise<ApiKey[]> {
  return read();
}

/** Mints and persists a new random key. */
export async function generateKey(label = "device"): Promise<ApiKey> {
  const keys = await read();
  const record: ApiKey = {
    key: `pk_${randomBytes(24).toString("base64url")}`,
    label,
    createdAt: new Date().toISOString(),
  };
  keys.push(record);
  await write(keys);
  return record;
}

/** Removes a key by its full value. Returns true if one was removed. */
export async function revokeKey(key: string): Promise<boolean> {
  const keys = await read();
  const next = keys.filter((k) => k.key !== key);
  if (next.length === keys.length) return false;
  await write(next);
  return true;
}

/** Whether any keys have been issued (i.e. auth is active). */
export async function hasKeys(): Promise<boolean> {
  return (await read()).length > 0;
}

/** Validates a presented key against the store and the legacy env secret. */
export async function isValidKey(presented: string | undefined): Promise<boolean> {
  if (!presented) return false;
  if (config.apiKey && presented === config.apiKey) return true;
  const keys = await read();
  return keys.some((k) => k.key === presented);
}

/** Masks a key for display: keeps the prefix and last 4 chars. */
export function maskKey(key: string): string {
  if (key.length <= 8) return "••••";
  return `${key.slice(0, 3)}…${key.slice(-4)}`;
}
