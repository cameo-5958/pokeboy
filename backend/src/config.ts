import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export const config = {
  port: Number(process.env.PORT ?? 4000),
  /** Storage root holding cartridges.json, roms/, mods/, and labels/. */
  storageDir: path.resolve(
    process.env.STORAGE_DIR ?? path.join(__dirname, "..", "storage"),
  ),
};

export const paths = {
  metadata: () => path.join(config.storageDir, "cartridges.json"),
  roms: () => path.join(config.storageDir, "roms"),
  mods: () => path.join(config.storageDir, "mods"),
  labels: () => path.join(config.storageDir, "labels"),
};
