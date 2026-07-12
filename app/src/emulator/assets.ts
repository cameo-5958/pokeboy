import { Asset } from "expo-asset";

import documentModule from "../../assets/emulator/embed.html";
import coreModule from "../../assets/emulator/gbcore.bin";
import wasmModule from "../../assets/emulator/gbcore.wasm";
import modCoreModule from "../../assets/emulator/mod-core.bin";

export type EmulatorAssets = {
  documentUri: string;
  gbcoreUri: string;
  wasmUri: string;
  modCoreUri: string;
  readAccessUri: string;
};

function localUri(asset: Asset, name: string): string {
  if (!asset.localUri) throw new Error(`Bundled emulator asset ${name} has no local URI`);
  return asset.localUri;
}

let cached: EmulatorAssets | null = null;
let pending: Promise<EmulatorAssets> | null = null;

export function loadEmulatorAssets(): Promise<EmulatorAssets> {
  if (cached) return Promise.resolve(cached);
  if (pending) return pending;
  pending = Asset.loadAsync([documentModule, coreModule, wasmModule, modCoreModule])
    .then(([document, gbcore, wasm, modCore]) => {
      const documentUri = localUri(document, "embed.html");
      cached = {
        documentUri,
        gbcoreUri: localUri(gbcore, "gbcore.bin"),
        wasmUri: localUri(wasm, "gbcore.wasm"),
        modCoreUri: localUri(modCore, "mod-core.bin"),
        readAccessUri: documentUri.slice(0, documentUri.lastIndexOf("/") + 1),
      };
      return cached;
    })
    .catch((error) => {
      pending = null;
      throw error;
    });
  return pending;
}
