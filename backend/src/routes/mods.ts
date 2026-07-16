import fs from "node:fs/promises";
import path from "node:path";
import { Router } from "express";

import { config, paths } from "../config.js";
import { readRegistry } from "../storage.js";

export const modsRouter = Router();

// Lists installed mod packages (`.gbmod` files) under the mods dir. The registry
// (`/api/registry`) carries the mod *catalog* with versions; this endpoint
// reports which package payloads are actually present on disk.
modsRouter.get("/", async (_req, res, next) => {
  try {
    const entries = await fs.readdir(paths.mods()).catch((e) => {
      if ((e as NodeJS.ErrnoException).code === "ENOENT") return [] as string[];
      throw e;
    });
    res.json(
      entries
        .filter((name) => name.toLowerCase().endsWith(".gbmod"))
        .map((file) => {
          const id = path.basename(file, path.extname(file));
          return { id, name: id, file };
        }),
    );
  } catch (e) {
    next(e);
  }
});

// Emulator host core (mod-core JS) named by the registry's host entry. Served
// from the tracked app asset (config.hostCoreFile), so updating the checkout
// updates what every device runs — no app build involved.
modsRouter.get("/host/:id", async (req, res) => {
  const host = (await readRegistry()).host;
  if (!host || host.id !== req.params.id) {
    res.status(404).json({ error: "Host core not in registry" });
    return;
  }
  try {
    await fs.access(config.hostCoreFile);
    res.type("text/javascript").sendFile(config.hostCoreFile);
  } catch {
    res.status(404).json({ error: "Host core file not installed" });
  }
});

// Optional RGBDS symbols are shared by every mod targeting this cartridge.
modsRouter.get("/symbols/:cartridge", async (req, res) => {
  const id = path.basename(req.params.cartridge);
  const file = path.join(paths.mods(), `${id}.sym`);
  try {
    await fs.access(file);
    res.type("text/plain").sendFile(file);
  } catch {
    res.status(404).json({ error: "Symbol file not installed" });
  }
});

// Streams a package by catalog id. basename plus the fixed extension prevents
// a request from escaping the configured mod directory.
modsRouter.get("/:id", async (req, res) => {
  const id = path.basename(req.params.id);
  const file = path.join(paths.mods(), `${id}.gbmod`);
  try {
    await fs.access(file);
    res.type("application/octet-stream").sendFile(file);
  } catch {
    res.status(404).json({ error: "Mod package not found" });
  }
});
