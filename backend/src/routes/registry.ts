import { Router } from "express";

import { contentChecksum } from "../content.js";
import {
  fileChecksum,
  readRegistry,
  refs,
  romPath,
} from "../storage.js";

export const registryRouter = Router();

/**
 * Combined ROM + mod registry the app diffs against when pulling the latest.
 * Each entry carries a catalog `version` plus a live content `checksum` (null
 * when the payload is missing), so a client can detect both version bumps and
 * silent content drift. Checksums are taken from the same source the payload
 * routes serve, so they stay honest under any `CONTENT_SOURCE`.
 */
registryRouter.get("/", async (req, res, next) => {
  try {
    const origin = `${req.protocol}://${req.get("host")}`;
    const { roms, mods, host } = await readRegistry();

    const romDtos = await Promise.all(
      roms.map(async (r) => ({
        id: r.id,
        title: r.title,
        version: r.version,
        // ROMs are not redistributable and never leave local disk.
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
        checksum: await contentChecksum(refs.mod(m.file)),
      })),
    );

    // The emulator host core (mod-core JS) ships over the air like a mod;
    // clients cache it by version and fall back to their bundled copy.
    const hostDto = host
      ? {
          id: host.id,
          version: host.version,
          checksum: await contentChecksum(refs.hostCore()),
        }
      : null;

    res.json({ roms: romDtos, mods: modDtos, host: hostDto });
  } catch (e) {
    next(e);
  }
});
