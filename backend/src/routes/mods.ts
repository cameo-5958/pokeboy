import fs from "node:fs/promises";
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
        .filter((name) => !name.startsWith("."))
        .map((name) => ({ id: name, name })),
    );
  } catch (e) {
    next(e);
  }
});
