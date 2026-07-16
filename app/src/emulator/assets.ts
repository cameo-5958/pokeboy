import { Asset } from "expo-asset";
import * as FileSystem from "expo-file-system";

import documentModule from "../../assets/emulator/embed.html";
import coreModule from "../../assets/emulator/gbcore.bin";
import wasmModule from "../../assets/emulator/gbcore.wasm";
import modCoreModule from "../../assets/emulator/mod-core.bin";

export type EmulatorAssets = {
  documentUri: string;
  gbcoreUri: string;
  wasmUri: string;
  // WKWebView cannot fetch() file:// URIs, so the Emscripten glue gets the
  // wasm bytes handed in (Module.wasmBinary) instead of fetching wasmUri.
  wasmBase64: string;
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
    .then(async ([document, gbcore, wasm, modCore]) => {
      const documentUri = localUri(document, "embed.html");
      const wasmUri = localUri(wasm, "gbcore.wasm");
      const wasmBase64 = await FileSystem.readAsStringAsync(wasmUri, {
        encoding: FileSystem.EncodingType.Base64,
      });
      cached = {
        documentUri,
        gbcoreUri: localUri(gbcore, "gbcore.bin"),
        wasmUri,
        wasmBase64,
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
