import { createHash } from "node:crypto";
import fs from "node:fs/promises";

import { config } from "./config.js";

/**
 * A piece of catalog content, named in both of the places it can live: this
 * server's disk, and the repo. Every reader goes through `readContent`, so
 * flipping `CONTENT_SOURCE` moves the whole catalog without touching routes.
 *
 * ROM binaries deliberately have no ref: `*.gb` is git-ignored (not
 * redistributable), so a ROM can only ever come off local disk.
 */
export type ContentRef = {
  /** Absolute path on this server (the `local` source, and the fallback). */
  localPath: string;
  /** Repo-relative path, e.g. `backend/data/registry.json` (the `github` source). */
  repoPath: string;
};

type CacheEntry = { bytes: Buffer | null; expires: number };

/** Only used by the `github` source; `local` always reads live off disk. */
const cache = new Map<string, CacheEntry>();

async function readLocal(ref: ContentRef): Promise<Buffer | null> {
  try {
    return await fs.readFile(ref.localPath);
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === "ENOENT") return null;
    throw e;
  }
}

/**
 * Fetches one file from the raw base URL. Throws on any non-2xx - including
 * 404, which a private repo returns for every path. Absence is decided by the
 * local fallback instead, so pointing at a private (or misspelled) base URL
 * degrades to serving the local copy rather than to an empty catalog.
 */
async function fetchRemote(ref: ContentRef): Promise<Buffer> {
  const url = `${config.contentBaseUrl}/${ref.repoPath}`;
  const headers: Record<string, string> = { accept: "application/vnd.github.raw" };
  // Read from the environment only, and never echoed to a client: the app talks
  // to this backend, so no token ever reaches an IPA.
  if (config.githubToken) headers.authorization = `Bearer ${config.githubToken}`;

  const response = await fetch(url, {
    headers,
    signal: AbortSignal.timeout(config.contentTimeoutMs),
  });
  if (!response.ok) throw new Error(`GET ${url} -> ${response.status} ${response.statusText}`);
  return Buffer.from(await response.arrayBuffer());
}

/**
 * Reads a content file from the configured source, or null when it exists in
 * neither place. In `github` mode the result is cached for `CONTENT_TTL_MS`
 * (fallbacks included, so an unreachable base URL costs one request per TTL,
 * not one per client request).
 */
export async function readContent(ref: ContentRef): Promise<Buffer | null> {
  if (config.contentSource === "local") return readLocal(ref);

  const hit = cache.get(ref.repoPath);
  if (hit && hit.expires > Date.now()) return hit.bytes;

  const bytes = await fetchRemote(ref).catch(async (e: unknown) => {
    console.warn(`content: ${ref.repoPath} unavailable (${String(e)}); using local copy`);
    return readLocal(ref);
  });
  cache.set(ref.repoPath, { bytes, expires: Date.now() + config.contentTtlMs });
  return bytes;
}

/** Drops cached content. Exposed for tests and for a future refresh hook. */
export function clearContentCache(): void {
  cache.clear();
}

/**
 * Short checksum of the bytes this server would actually serve for `ref`, or
 * null when it has none. Sourcing the checksum from the same read as the
 * payload keeps `/api/registry` honest in `github` mode, where the local file
 * may differ from what is served.
 */
export async function contentChecksum(ref: ContentRef): Promise<string | null> {
  const bytes = await readContent(ref);
  return bytes ? createHash("sha256").update(bytes).digest("hex").slice(0, 16) : null;
}
