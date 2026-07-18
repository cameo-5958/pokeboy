import path from "node:path";
import { fileURLToPath } from "node:url";

import { loadDotEnv } from "./env.js";

loadDotEnv();

const __dirname = path.dirname(fileURLToPath(import.meta.url));
// Repo-relative backend root (this file lives in backend/src).
const root = path.join(__dirname, "..");

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
