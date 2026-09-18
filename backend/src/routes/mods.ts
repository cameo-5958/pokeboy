import fs from "node:fs/promises";
import path from "node:path";
import { Router } from "express";

import { paths } from "../config.js";
import { readContent } from "../content.js";
import { readRegistry, refs } from "../storage.js";

export const modsRouter = Router();

// Lists installed mod packages (`.gbmod` files) under the mods dir. The registry
// (`/api/registry`) carries the mod *catalog* with versions; this endpoint
// reports which package payloads are actually present on disk - this listing is
// deliberately local-only, unlike the payload routes below.
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

// Emulator host core (mod-core JS) named by the registry's host entry. Tracked
// in git as an app asset, so updating the source (a checkout, or the configured
// content source) updates what every device runs - no app build involved.
modsRouter.get("/host/:id", async (req, res, next) => {
  try {
    const host = (await readRegistry()).host;
    if (!host || host.id !== req.params.id) {
      res.status(404).json({ error: "Host core not in registry" });
      return;
    }
    const bytes = await readContent(refs.hostCore());
    if (!bytes) {
      res.status(404).json({ error: "Host core file not installed" });
      return;
    }
    res.type("text/javascript").send(bytes);
  } catch (e) {
    next(e);
  }
});

// Optional RGBDS symbols are shared by every mod targeting this cartridge.
modsRouter.get("/symbols/:cartridge", async (req, res, next) => {
  try {
    const bytes = await readContent(refs.symbols(req.params.cartridge));
    if (!bytes) {
      res.status(404).json({ error: "Symbol file not installed" });
      return;
    }
    res.type("text/plain").send(bytes);
  } catch (e) {
    next(e);
  }
});

// Streams a package by catalog id. `refs` basenames the id and appends the
// fixed extension, so a request cannot escape the mod directory or the repo
// path prefix.
modsRouter.get("/:id", async (req, res, next) => {
  try {
    const bytes = await readContent(refs.mod(`${path.basename(req.params.id)}.gbmod`));
    if (!bytes) {
      res.status(404).json({ error: "Mod package not found" });
      return;
    }
    res.type("application/octet-stream").send(bytes);
  } catch (e) {
    next(e);
  }
});
