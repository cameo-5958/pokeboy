import fs from "node:fs/promises";
import path from "node:path";
import { Router } from "express";

import { paths } from "../config.js";

export const modsRouter = Router();

// Lists available mod payloads. Mods are stored as files under storage/mods.
// This is a stub over the filesystem; a real catalog/metadata store lands later.
modsRouter.get("/", async (_req, res, next) => {
  try {
    let entries: string[] = [];
    try {
      entries = await fs.readdir(paths.mods());
    } catch (e) {
      if ((e as NodeJS.ErrnoException).code !== "ENOENT") throw e;
    }
    res.json(
      entries
        .filter((name) => name.toLowerCase().endsWith(".gbmod"))
        .map((file) => ({ id: path.basename(file, path.extname(file)), name: path.basename(file, path.extname(file)), file })),
    );
  } catch (e) {
    next(e);
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
