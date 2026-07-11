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

let pending: Promise<EmulatorAssets> | null = null;

function usableUri(asset: Asset): string {
  const uri = asset.localUri ?? asset.uri;
  if (!uri) throw new Error(`Bundled emulator asset ${asset.name} has no URI`);
  return uri;
}

/** Resolves the emulator files copied into the native application bundle. */
export function loadEmulatorAssets(): Promise<EmulatorAssets> {
  if (pending) return pending;
  pending = (async () => {
    const [document, gbcore, wasm, modCore] = await Asset.loadAsync([
      documentModule,
      coreModule,
      wasmModule,
      modCoreModule,
    ]);
    const documentUri = usableUri(document);
    return {
      documentUri,
      gbcoreUri: usableUri(gbcore),
      wasmUri: usableUri(wasm),
      modCoreUri: usableUri(modCore),
      readAccessUri: documentUri.slice(0, documentUri.lastIndexOf("/") + 1),
    };
  })();
  return pending;
}
