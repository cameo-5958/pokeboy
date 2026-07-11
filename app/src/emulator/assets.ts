import resolveAssetSource from "react-native/Libraries/Image/resolveAssetSource";

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

function resolveUri(moduleId: number, name: string): string {
  const source = resolveAssetSource(moduleId);
  if (!source) throw new Error(`Bundled emulator asset ${name} is not in the asset registry`);
  const uri = source.uri;
  if (!uri) throw new Error(`Bundled emulator asset ${name} has no URI`);
  return uri;
}

let cached: EmulatorAssets | null = null;

export function loadEmulatorAssets(): EmulatorAssets {
  if (cached) return cached;
  const documentUri = resolveUri(documentModule, "embed.html");
  cached = {
    documentUri,
    gbcoreUri: resolveUri(coreModule, "gbcore.bin"),
    wasmUri: resolveUri(wasmModule, "gbcore.wasm"),
    modCoreUri: resolveUri(modCoreModule, "mod-core.bin"),
    readAccessUri: documentUri.slice(0, documentUri.lastIndexOf("/") + 1),
  };
  return cached;
}
