/**
 * Minimal .env loader (no dependency). config.ts calls loadDotEnv() before
 * reading process.env, so backend/.env works for plain `npm run dev` runs.
 * Real environment variables (e.g. the systemd EnvironmentFile) always win
 * over file values.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/** Parses KEY=VALUE lines; supports #-comments and single/double quotes. */
export function parseDotEnv(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq <= 0) continue;
    const key = line.slice(0, eq).trim();
    let value = line.slice(eq + 1).trim();
    if (
      value.length >= 2 &&
      ((value.startsWith('"') && value.endsWith('"')) ||
        (value.startsWith("'") && value.endsWith("'")))
    ) {
      value = value.slice(1, -1);
    }
    out[key] = value;
  }
  return out;
}

/** Loads `file` into process.env without overriding existing variables. */
export function loadDotEnv(file = path.join(__dirname, "..", ".env")): void {
  let text: string;
  try {
    text = fs.readFileSync(file, "utf8");
  } catch {
    return; // no .env is fine - env vars / systemd EnvironmentFile still apply
  }
  for (const [key, value] of Object.entries(parseDotEnv(text))) {
    if (process.env[key] === undefined) process.env[key] = value;
  }
}
