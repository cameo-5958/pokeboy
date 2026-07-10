import fs from "node:fs/promises";
import path from "node:path";

import { paths } from "./config.js";

export type CartridgeRecord = {
  id: string;
  title: string;
  /** ROM filename within storage/roms. */
  file: string;
  /** Label image filename within storage/labels, if any. */
  img?: string;
};

/** Reads the cartridge metadata catalog. Returns [] if none exists yet. */
export async function readCartridges(): Promise<CartridgeRecord[]> {
  try {
    const raw = await fs.readFile(paths.metadata(), "utf8");
    return JSON.parse(raw) as CartridgeRecord[];
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === "ENOENT") return [];
    throw e;
  }
}

export async function findCartridge(id: string): Promise<CartridgeRecord | undefined> {
  const carts = await readCartridges();
  return carts.find((c) => c.id === id);
}

/** Absolute path to a cartridge's ROM file. */
export function romPath(record: CartridgeRecord): string {
  return path.join(paths.roms(), record.file);
}
