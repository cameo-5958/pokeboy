/**
 * Browser player (emulator.cameo.moe): session auth, the static shell in
 * web/, the bundled emulator runtime from app/assets/emulator, and per-user
 * persistence (battery saves + save-state slots) under data/web/.
 *
 * Mounted at /web BEFORE the app-level express.json — save states are far
 * larger than the global 1mb body limit, so every route parses its own body.
 */
import { createHash } from "node:crypto";
import { existsSync } from "node:fs";
import fs from "node:fs/promises";
import path from "node:path";
import express, { Router, type Request, type Response, type NextFunction } from "express";

import { config } from "../config.js";
import {
  checkCredentials,
  clearSessionCookie,
  hasWebSession,
  issueSessionCookie,
  loginBlocked,
  noteLoginFailure,
} from "../websession.js";

export const webRouter = Router();

const CART_ID = /^[a-z0-9][a-z0-9-]*$/;
const SLOTS = new Set(["1", "2", "3"]);

function requireSession(req: Request, res: Response, next: NextFunction) {
  if (!hasWebSession(req)) return res.status(401).json({ error: "Not signed in" });
  next();
}

// ---- auth ----------------------------------------------------------------

webRouter.post("/login", express.json({ limit: "4kb" }), (req, res) => {
  const ip = req.ip ?? "unknown";
  if (loginBlocked(ip)) {
    return res.status(429).json({ error: "Too many attempts; try again later" });
  }
  const { username, password } = (req.body ?? {}) as Record<string, unknown>;
  if (!checkCredentials(username, password)) {
    noteLoginFailure(ip);
    return res.status(401).json({ error: "Invalid credentials" });
  }
  issueSessionCookie(req, res);
  res.json({ ok: true });
});

webRouter.post("/logout", (_req, res) => {
  clearSessionCookie(res);
  res.json({ ok: true });
});

webRouter.get("/session", (req, res) => {
  if (!hasWebSession(req)) return res.status(401).json({ error: "Not signed in" });
  res.json({ ok: true });
});

// ---- runtime metadata ----------------------------------------------------

// Save states only restore cleanly into the exact core build that produced
// them, so states are stamped with the wasm's hash and the shell compares
// against this endpoint before loading one.
let coreHashPromise: Promise<string> | null = null;
function coreHash(): Promise<string> {
  coreHashPromise ??= fs
    .readFile(path.join(config.emulatorAssetsDir, "gbcore.wasm"))
    .then((bytes) => createHash("sha256").update(bytes).digest("hex"))
    .catch((e) => {
      coreHashPromise = null;
      throw e;
    });
  return coreHashPromise;
}

webRouter.get("/meta", requireSession, async (_req, res, next) => {
  try {
    res.json({ coreHash: await coreHash() });
  } catch (e) {
    next(e);
  }
});

// ---- save-state slots ----------------------------------------------------

function stateFile(cartridgeId: string, slot: string): string {
  return path.join(config.webDataDir, "states", cartridgeId, `${slot}.json`);
}

type StateRecord = {
  slot: number;
  ts: number;
  coreHash: string;
  cartridgeId: string;
  cartridgeVersion: string;
  mods: string[];
  gz: boolean;
  heapLen: number;
  base64: string;
};

function checkStateParams(req: Request, res: Response): boolean {
  if (!CART_ID.test(req.params.cartridgeId)) {
    res.status(400).json({ error: "Invalid cartridge id" });
    return false;
  }
  if (req.params.slot !== undefined && !SLOTS.has(req.params.slot)) {
    res.status(400).json({ error: "Invalid slot" });
    return false;
  }
  return true;
}

async function readState(cartridgeId: string, slot: string): Promise<StateRecord | null> {
  try {
    return JSON.parse(await fs.readFile(stateFile(cartridgeId, slot), "utf8")) as StateRecord;
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === "ENOENT") return null;
    throw e;
  }
}

webRouter.get("/states/:cartridgeId", requireSession, async (req, res, next) => {
  try {
    if (!checkStateParams(req, res)) return;
    const slots = [];
    for (const slot of SLOTS) {
      const record = await readState(req.params.cartridgeId, slot);
      if (record) {
        slots.push({
          slot: Number(slot),
          ts: record.ts,
          coreHash: record.coreHash,
          size: record.base64.length,
        });
      }
    }
    res.json(slots);
  } catch (e) {
    next(e);
  }
});

webRouter.get("/states/:cartridgeId/:slot", requireSession, async (req, res, next) => {
  try {
    if (!checkStateParams(req, res)) return;
    const record = await readState(req.params.cartridgeId, req.params.slot);
    if (!record) return res.status(404).json({ error: "Empty slot" });
    res.json(record);
  } catch (e) {
    next(e);
  }
});

// 48mb: a gzip'd heap snapshot is well under 1mb, but the no-CompressionStream
// fallback ships the raw heap (~16mb) as base64 (~22mb).
webRouter.put(
  "/states/:cartridgeId/:slot",
  requireSession,
  express.json({ limit: "48mb" }),
  async (req, res, next) => {
    try {
      if (!checkStateParams(req, res)) return;
      const body = (req.body ?? {}) as Record<string, unknown>;
      if (typeof body.base64 !== "string" || !body.base64) {
        return res.status(400).json({ error: "Missing state payload" });
      }
      const record: StateRecord = {
        slot: Number(req.params.slot),
        ts: Date.now(),
        coreHash: await coreHash(),
        cartridgeId: req.params.cartridgeId,
        cartridgeVersion: typeof body.cartridgeVersion === "string" ? body.cartridgeVersion : "0",
        mods: Array.isArray(body.mods) ? body.mods.filter((m): m is string => typeof m === "string") : [],
        gz: Boolean(body.gz),
        heapLen: Number(body.heapLen) || 0,
        base64: body.base64,
      };
      const file = stateFile(req.params.cartridgeId, req.params.slot);
      await fs.mkdir(path.dirname(file), { recursive: true });
      await fs.writeFile(file, JSON.stringify(record));
      res.json({ slot: record.slot, ts: record.ts, coreHash: record.coreHash, size: record.base64.length });
    } catch (e) {
      next(e);
    }
  },
);

webRouter.delete("/states/:cartridgeId/:slot", requireSession, async (req, res, next) => {
  try {
    if (!checkStateParams(req, res)) return;
    await fs.rm(stateFile(req.params.cartridgeId, req.params.slot), { force: true });
    res.status(204).end();
  } catch (e) {
    next(e);
  }
});

// ---- battery saves -------------------------------------------------------

function batteryFile(cartridgeId: string): string {
  return path.join(config.webDataDir, "battery", `${cartridgeId}.json`);
}

webRouter.get("/battery/:cartridgeId", requireSession, async (req, res, next) => {
  try {
    if (!CART_ID.test(req.params.cartridgeId)) {
      return res.status(400).json({ error: "Invalid cartridge id" });
    }
    try {
      const raw = JSON.parse(await fs.readFile(batteryFile(req.params.cartridgeId), "utf8"));
      res.json({ ts: Number(raw.ts) || 0, data: typeof raw.data === "string" ? raw.data : null });
    } catch (e) {
      if ((e as NodeJS.ErrnoException).code !== "ENOENT") throw e;
      res.json({ ts: 0, data: null });
    }
  } catch (e) {
    next(e);
  }
});

webRouter.put(
  "/battery/:cartridgeId",
  requireSession,
  express.json({ limit: "1mb" }),
  async (req, res, next) => {
    try {
      if (!CART_ID.test(req.params.cartridgeId)) {
        return res.status(400).json({ error: "Invalid cartridge id" });
      }
      const body = (req.body ?? {}) as Record<string, unknown>;
      if (typeof body.data !== "string" || !body.data) {
        return res.status(400).json({ error: "Missing save data" });
      }
      const file = batteryFile(req.params.cartridgeId);
      await fs.mkdir(path.dirname(file), { recursive: true });
      await fs.writeFile(file, JSON.stringify({ ts: Number(body.ts) || Date.now(), data: body.data }));
      res.json({ ok: true });
    } catch (e) {
      next(e);
    }
  },
);

// ---- emulator runtime + static shell -------------------------------------

// The exact files the phone app bundles, served to the browser. .bin files
// are emscripten glue JS (renamed so Metro treats them as assets), so they
// need an explicit script MIME type.
const EMULATOR_FILES: Record<string, string> = {
  "embed.html": "text/html; charset=utf-8",
  "gbcore.bin": "text/javascript",
  "mod-core.bin": "text/javascript",
  "gbcore.wasm": "application/wasm",
};

webRouter.get("/emulator/:file", requireSession, (req, res) => {
  const type = EMULATOR_FILES[req.params.file];
  const file = path.join(config.emulatorAssetsDir, req.params.file);
  if (!type || !existsSync(file)) return res.status(404).json({ error: "Not found" });
  res.type(type).sendFile(file);
});

webRouter.get(["/", "/index.html"], (req, res) => {
  if (!hasWebSession(req)) return res.redirect("/web/login.html");
  res.sendFile(path.join(config.webDir, "index.html"));
});

webRouter.use(express.static(config.webDir, { index: false }));
