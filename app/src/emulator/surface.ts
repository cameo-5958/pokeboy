/**
 * The host-agnostic contract between the app shell and the emulator document.
 *
 * `EmulatorSurface` has two implementations resolved by the bundler: a React
 * Native WebView (`.tsx`) and a DOM iframe (`.web.tsx`). Both load the same
 * `embed.html`, the same `gbcore.wasm` and the same `mod-core`, and both speak
 * the same JSON postMessage protocol, so emulator behaviour does not depend on
 * which host mounted it.
 */

/** Imperative surface handle. Mirrors the subset of WebView the app uses. */
export type EmulatorHandle = {
  postMessage(message: string): void;
  reload(): void;
};

/**
 * Structural stand-in for WebViewMessageEvent. WebView's own event satisfies
 * this shape, so the native path passes its event straight through.
 */
export type EmulatorMessageEvent = { nativeEvent: { data: string } };

/** Runtime configuration handed to embed.html (see `PokeboyRuntime`). */
export type EmulatorConfig = {
  backendUrl: string;
  apiKey?: string;
  cartridgeId: string;
  cartridgeVersion: string;
  gbcoreUri: string;
  wasmUri: string;
  wasmBase64: string;
  modCoreUri: string;
  battleLinkEndpoint: string;
  battleLinkMode: string;
  battleLinkMaxTimeTillRandomMs: number;
};

export type EmulatorSurfaceProps = {
  /** URL of embed.html (file:// on native, http(s):// on web). */
  uri: string;
  /** Pre-injection source for hosts that support it. Native only. */
  injected: string;
  /** The same config as `injected`, unserialised, for hosts that must post it. */
  config: EmulatorConfig;
  /** Directory the native WebView may read siblings from. Native only. */
  readAccessUri: string;
  style?: unknown;
  onMessage(event: EmulatorMessageEvent): void;
  /** Fired once the document is ready for host state to be pushed. */
  onLoad(): void;
};
