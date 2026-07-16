import { Asset } from "expo-asset";

import documentModule from "../../assets/emulator/embed.html";
import coreModule from "../../assets/emulator/gbcore-glue.bin";
import wasmModule from "../../assets/emulator/gbcore.wasm";
import modCoreModule from "../../assets/emulator/mod-core.bin";

import type { EmulatorAssets } from "./assets";

/**
 * Web resolution of the same four bundled assets.
 *
 * The bytes are identical to the native path — only how they are addressed
 * differs. On web the bundler serves each asset over http(s), so:
 *   - `asset.uri` is the URL (`localUri` is a native-only concept);
 *   - `wasmBase64` stays empty, because an http(s) document *can* fetch its
 *     sibling wasm. embed.html already falls back to fetching `wasmUri` when
 *     no base64 is supplied — that branch exists for WKWebView's file://
 *     restriction, which does not apply here;
 *   - `readAccessUri` is meaningless without a file:// sandbox.
 */
function uri(asset: Asset, name: string): string {
  if (!asset.uri) throw new Error(`Bundled emulator asset ${name} has no URI`);
  return asset.uri;
}

let cached: EmulatorAssets | null = null;
let pending: Promise<EmulatorAssets> | null = null;

export function loadEmulatorAssets(): Promise<EmulatorAssets> {
  if (cached) return Promise.resolve(cached);
  if (pending) return pending;
  pending = Asset.loadAsync([documentModule, coreModule, wasmModule, modCoreModule])
    .then(([document, gbcore, wasm, modCore]) => {
      cached = {
        documentUri: uri(document, "embed.html"),
        gbcoreUri: uri(gbcore, "gbcore-glue.bin"),
        wasmUri: uri(wasm, "gbcore.wasm"),
        wasmBase64: "",
        modCoreUri: uri(modCore, "mod-core.bin"),
        readAccessUri: "",
      };
      return cached;
    })
    .catch((error) => {
      pending = null;
      throw error;
    });
  return pending;
}
