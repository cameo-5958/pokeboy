import { Router } from "express";

import { readMods } from "../storage.js";

export const modsRouter = Router();

// Lists the mods catalog from the registry. Payload files live under the mods
// dir; the registry is the source of truth for what's available and its version.
modsRouter.get("/", async (_req, res, next) => {
  try {
    const mods = await readMods();
    res.json(
      mods.map((m) => ({
        id: m.id,
        name: m.name,
        desc: m.desc ?? null,
        version: m.version,
      })),
    );
  } catch (e) {
    next(e);
  }
});
