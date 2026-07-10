import { Router } from "express";

import {
  fileChecksum,
  modPath,
  readRegistry,
  romPath,
} from "../storage.js";

export const registryRouter = Router();

/**
 * Combined ROM + mod registry the app diffs against when pulling the latest.
 * Each entry carries a catalog `version` plus a live content `checksum` (null
 * when the payload file is missing from disk), so a client can detect both
 * version bumps and silent content drift.
 */
registryRouter.get("/", async (req, res, next) => {
  try {
    const origin = `${req.protocol}://${req.get("host")}`;
    const { roms, mods } = await readRegistry();

    const romDtos = await Promise.all(
      roms.map(async (r) => ({
        id: r.id,
        title: r.title,
        version: r.version,
        checksum: await fileChecksum(romPath(r)),
        img: r.img ? `${origin}/labels/${r.img}` : null,
        rom: `${origin}/api/cartridges/${r.id}/rom`,
      })),
    );

    const modDtos = await Promise.all(
      mods.map(async (m) => ({
        id: m.id,
        name: m.name,
        desc: m.desc ?? null,
        version: m.version,
        checksum: await fileChecksum(modPath(m)),
      })),
    );

    res.json({ roms: romDtos, mods: modDtos });
  } catch (e) {
    next(e);
  }
});
