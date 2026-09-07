import path from "node:path";
import { fileURLToPath } from "node:url";

import { loadDotEnv } from "./env.js";

loadDotEnv();

const __dirname = path.dirname(fileURLToPath(import.meta.url));
// Repo-relative backend root (this file lives in backend/src).
const root = path.join(__dirname, "..");

/** Fail fast on a typo rather than silently serving the wrong source. */
function contentSource(): "local" | "github" {
  const value = process.env.CONTENT_SOURCE ?? "local";
  if (value !== "local" && value !== "github") {
    throw new Error(`CONTENT_SOURCE must be "local" or "github" (got "${value}")`);
  }
  return value;
}

/**
 * Backend configuration. Every filesystem location the server touches is
 * declared here (overridable by env var) so nothing hard-codes a path.
 */
export const config = {
  port: Number(process.env.PORT ?? 4000),

  /** ROM binaries (.gb / .gbc). */
  romsDir: path.resolve(process.env.ROMS_DIR ?? path.join(root, "roms")),
  /** Mod payloads. */
  modsDir: path.resolve(process.env.MODS_DIR ?? path.join(root, "mods")),
  /** Cartridge label images (served statically). */
  labelsDir: path.resolve(process.env.LABELS_DIR ?? path.join(root, "labels")),
  /** Combined ROM + mod registry catalog. */
  registryFile: path.resolve(
    process.env.REGISTRY_FILE ?? path.join(root, "data", "registry.json"),
  ),
  /** Issued device API keys (git-ignored — secrets). */
  keysFile: path.resolve(
    process.env.KEYS_FILE ?? path.join(root, "data", "keys.json"),
  ),

  /**
   * Emulator host core (mod-core JS) served over the air via the registry.
   * Defaults to the tracked app asset so a `git pull` on the server is the
   * whole deploy; the app bundles the same file as its offline fallback.
   */
  hostCoreFile: path.resolve(
    process.env.HOST_CORE_FILE ??
      path.join(root, "..", "app", "assets", "emulator", "mod-core.bin"),
  ),

  /**
   * Where catalog content — the registry, `.sym` symbols, `.gbmod` packages and
   * the host core — is read from. See `content.ts`.
   * - `local` (default): read the paths above straight off disk.
   * - `github`: fetch the tracked copies from `contentBaseUrl`, TTL-cached,
   *   falling back to the local path whenever the fetch fails.
   * ROM binaries are never redistributable, are not in git, and so always come
   * from `romsDir` regardless of this setting.
   */
  contentSource: contentSource(),

  /**
   * Raw base URL for `contentSource: "github"`, with repo-relative paths
   * appended. Consulted only in `github` mode. NOTE: `cameo-5958/pokeboy` is
   * currently private, and raw.githubusercontent 404s every path on a private
   * repo unless `githubToken` is set — which is why the default is `local`.
   */
  contentBaseUrl: (
    process.env.CONTENT_BASE_URL ??
      "https://raw.githubusercontent.com/cameo-5958/pokeboy/main"
  ).replace(/\/+$/, ""),

  /** How long fetched content is cached in memory (`github` mode only). */
  contentTtlMs: Number(process.env.CONTENT_TTL_MS ?? 60_000),
  /** Per-request timeout for content fetches, so a hang cannot stall a route. */
  contentTimeoutMs: Number(process.env.CONTENT_TIMEOUT_MS ?? 5_000),

  /**
   * Optional PAT for fetching content from a private repo. Environment only —
   * never committed, and never sent to a client: devices talk to this backend,
   * which is the whole reason the app cannot leak it.
   */
  githubToken: process.env.GITHUB_TOKEN ?? "",

  /** Battle Link Discord bot token (secret; prefer the git-ignored file). */
  discordToken: process.env.DISCORD_BOT_TOKEN ?? "",
  discordTokenFile: path.resolve(
    process.env.DISCORD_TOKEN_FILE ?? path.join(root, "data", "discord.json"),
  ),

  /**
   * Legacy single shared secret. When set it is always accepted alongside any
   * issued device keys. Prefer generating keys (see `keys.ts`).
   */
  apiKey: process.env.API_KEY ?? "",

  /**
   * Admin secret that guards key management over HTTP (`/api/keys`). Unset
   * (default) disables the HTTP mint endpoint — keys can still be minted
   * locally with `npm run key:new`.
   */
  adminToken: process.env.ADMIN_TOKEN ?? "",

  /** Hostname whose root serves the browser player shell. */
  webHost: process.env.WEB_HOST ?? "emulator.cameo.moe",
  /**
   * Browser player credentials + cookie secret (backend/.env). Web login is
   * disabled (every attempt rejected) until all three are set.
   */
  webUsername: process.env.WEB_USERNAME ?? "",
  webPassword: process.env.WEB_PASSWORD ?? "",
  webSessionSecret: process.env.WEB_SESSION_SECRET ?? "",
  /** Static shell (login/player pages). */
  webDir: path.resolve(process.env.WEB_DIR ?? path.join(root, "..", "web")),
  /** Bundled emulator runtime served to the browser — the same files the app ships. */
  emulatorAssetsDir: path.resolve(
    process.env.EMULATOR_ASSETS_DIR ?? path.join(root, "..", "app", "assets", "emulator"),
  ),
  /** Web player persistence (battery saves, save-state slots). */
  webDataDir: path.resolve(process.env.WEB_DATA_DIR ?? path.join(root, "data", "web")),
};

// Back-compat accessors used across the routes/storage layer.
export const paths = {
  registry: () => config.registryFile,
  keys: () => config.keysFile,
  roms: () => config.romsDir,
  mods: () => config.modsDir,
  labels: () => config.labelsDir,
};
