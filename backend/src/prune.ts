import fsSync, { type Dirent } from "node:fs";
import fs from "node:fs/promises";
import path from "node:path";

import { paths } from "./config.js";
import { readRegistry } from "./storage.js";

type ManagedDirectory = {
  name: "roms" | "mods" | "labels";
  path: string;
  liveFiles: Set<string>;
};

async function pruneDirectory(directory: ManagedDirectory): Promise<number> {
  let entries: Dirent[];
  try {
    entries = await fs.readdir(directory.path, { withFileTypes: true });
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === "ENOENT") return 0;
    throw e;
  }

  let count = 0;
  for (const entry of entries) {
    if (!entry.isFile() || entry.name.startsWith(".") || directory.liveFiles.has(entry.name)) {
      continue;
    }
    await fs.unlink(path.join(directory.path, entry.name));
    console.log(`pruned orphan: ${directory.name}/${entry.name}`);
    count++;
  }
  return count;
}

/** Removes managed files that are no longer referenced by a valid registry. */
export async function pruneOrphans(): Promise<void> {
  let registry;
  try {
    registry = await readRegistry();
  } catch (e) {
    console.warn(`orphan prune skipped: registry could not be read (${String(e)})`);
    return;
  }

  if (!Array.isArray(registry.roms) || !Array.isArray(registry.mods)) {
    console.warn("orphan prune skipped: registry roms or mods is invalid");
    return;
  }

  if (registry.roms.length === 0 && registry.mods.length === 0) {
    console.warn("orphan prune skipped: registry contains no roms or mods");
    return;
  }

  const directories: ManagedDirectory[] = [
    {
      name: "roms",
      path: paths.roms(),
      liveFiles: new Set(registry.roms.map((rom) => rom.file)),
    },
    {
      name: "mods",
      path: paths.mods(),
      liveFiles: new Set([
        ...registry.mods.map((mod) => mod.file),
        ...registry.roms.map((rom) => `${rom.id}.sym`),
      ]),
    },
    {
      name: "labels",
      path: paths.labels(),
      liveFiles: new Set(registry.roms.flatMap((rom) => rom.img ? [rom.img] : [])),
    },
  ];

  let count = 0;
  for (const directory of directories) count += await pruneDirectory(directory);
  console.log(`pruned orphans: ${count}`);
}

/** Re-prunes whenever the registry file's mtime changes. */
export function watchRegistryForPrune(): void {
  fsSync.watchFile(paths.registry(), { interval: 1_000 }, (current, previous) => {
    if (current.mtimeMs === previous.mtimeMs) return;
    void pruneOrphans().catch((e) => console.warn(`orphan prune failed: ${String(e)}`));
  });
}
