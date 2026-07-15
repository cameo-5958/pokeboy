import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";

import { config, paths } from "./config.js";
import { readContent, type ContentRef } from "./content.js";

export type CartridgeRecord = {
  id: string;
  title: string;
  /** ROM filename within the roms dir. */
  file: string;
  /** Label image filename within the labels dir, if any. */
  img?: string;
  /** Catalog version, bumped when the ROM content changes. */
  version: string;
};

export type ModRecord = {
  id: string;
  name: string;
  /** One-line description. */
  desc?: string;
  /** Payload filename within the mods dir. */
  file: string;
  /** Catalog version, bumped when the mod payload changes. */
  version: string;
};

export type HostRecord = {
  id: string;
  /** Catalog version, bumped when the host core content changes. */
  version: string;
};

type Registry = {
  roms: CartridgeRecord[];
  mods: ModRecord[];
  /** Emulator host core (mod-core JS), served over the air. */
  host?: HostRecord;
};

/**
 * Where each piece of tracked content lives, on disk and in the repo. ROMs are
 * absent by design — `*.gb` is git-ignored, so `romPath` is their only source.
 */
export const refs = {
  registry: (): ContentRef => ({
    localPath: paths.registry(),
    repoPath: "backend/data/registry.json",
  }),
  /** `file` is a registry `mods[].file`, e.g. `tradeback-npc.gbmod`. */
  mod: (file: string): ContentRef => {
    const name = path.basename(file);
    return { localPath: path.join(paths.mods(), name), repoPath: `backend/mods/${name}` };
  },
  symbols: (cartridgeId: string): ContentRef => {
    const name = `${path.basename(cartridgeId)}.sym`;
    return { localPath: path.join(paths.mods(), name), repoPath: `backend/mods/${name}` };
  },
  hostCore: (): ContentRef => ({
    localPath: config.hostCoreFile,
    repoPath: "app/assets/emulator/mod-core.bin",
  }),
};

/** Reads the combined registry catalog. Returns empty lists if none exists. */
export async function readRegistry(): Promise<Registry> {
  const raw = await readContent(refs.registry());
  if (raw === null) return { roms: [], mods: [] };
  const parsed = JSON.parse(raw.toString("utf8")) as Partial<Registry>;
  return { roms: parsed.roms ?? [], mods: parsed.mods ?? [], host: parsed.host };
}

export async function readCartridges(): Promise<CartridgeRecord[]> {
  return (await readRegistry()).roms;
}

export async function readMods(): Promise<ModRecord[]> {
  return (await readRegistry()).mods;
}

export async function findCartridge(id: string): Promise<CartridgeRecord | undefined> {
  return (await readCartridges()).find((c) => c.id === id);
}

/** Absolute path to a cartridge's ROM file. */
export function romPath(record: CartridgeRecord): string {
  return path.join(paths.roms(), record.file);
}

/** Absolute path to a mod's payload file. */
export function modPath(record: ModRecord): string {
  return path.join(paths.mods(), record.file);
}

/**
 * Short content checksum for an on-disk file, or null when the file is missing.
 * Lets clients detect content drift even when the catalog version is unchanged.
 */
export async function fileChecksum(absPath: string): Promise<string | null> {
  try {
    const bytes = await fs.readFile(absPath);
    return createHash("sha256").update(bytes).digest("hex").slice(0, 16);
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === "ENOENT") return null;
    throw e;
  }
}
