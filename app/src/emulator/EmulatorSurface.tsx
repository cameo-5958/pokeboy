import { forwardRef, useCallback, useImperativeHandle, useRef } from "react";
import WebView from "react-native-webview";

import type { EmulatorHandle, EmulatorMessageEvent, EmulatorSurfaceProps } from "./surface";

/**
 * Native host: the emulator document runs in a React Native WebView.
 *
 * Two host quirks are absorbed here so that embed.html stays host-agnostic:
 *
 * 1. `injectedJavaScriptBeforeContentLoaded` is dependable on iOS but not on
 *    Android, where the document routinely starts without `PokeboyRuntime` and
 *    fails to boot. Injection is kept as the iOS fast path, and the same
 *    request/answer handshake the web surface uses is honoured as the fallback.
 *    embed.html expresses this as `PokeboyRuntime || ask`, so whichever arrives
 *    first wins and both hosts end up with an identical runtime.
 * 2. Host state is pushed on `listening`, not on WebView.onLoad. The document
 *    attaches its message listener after an await, so onLoad can fire while it
 *    is still deaf — the state would be silently dropped.
 */
const EmulatorSurface = forwardRef<EmulatorHandle, EmulatorSurfaceProps>(function EmulatorSurface(
  { uri, injected, config, readAccessUri, style, onMessage, onLoad },
  ref,
) {
  const webViewRef = useRef<WebView | null>(null);

  const post = useCallback((message: string) => {
    webViewRef.current?.postMessage(message);
  }, []);

  useImperativeHandle(
    ref,
    () => ({ postMessage: post, reload: () => webViewRef.current?.reload() }),
    [post],
  );

  const handleMessage = useCallback(
    (event: EmulatorMessageEvent) => {
      let type: string | undefined;
      try {
        type = JSON.parse(event.nativeEvent.data)?.type;
      } catch {
        return; // embed.html only ever posts JSON strings
      }
      if (type === "runtime-request") {
        post(JSON.stringify({ type: "runtime", detail: config }));
        return;
      }
      if (type === "listening") {
        onLoad();
        return;
      }
      onMessage(event);
    },
    [config, onLoad, onMessage, post],
  );

  return (
    <WebView
      ref={webViewRef}
      source={{ uri }}
      // The bundled emulator loads from a file:// URI, which the default
      // whitelist (http/https) silently blocks — leaving a blank WebView.
      originWhitelist={["file://*", "http://*", "https://*"]}
      injectedJavaScriptBeforeContentLoaded={injected}
      // iOS: widen the file:// sandbox to the emulator asset directory.
      allowingReadAccessToURL={readAccessUri}
      // Android equivalents. Without these the document does not load at all
      // (net::ERR_ACCESS_DENIED): react-native-webview denies file:// access by
      // default, the page must read its sibling core/mod-core scripts, and it
      // fetches the backend from a file:// (opaque) origin. iOS ignores them.
      allowFileAccess
      allowFileAccessFromFileURLs
      allowUniversalAccessFromFileURLs
      style={style as never}
      pointerEvents="none"
      scrollEnabled={false}
      overScrollMode="never"
      javaScriptEnabled
      allowsInlineMediaPlayback
      mediaPlaybackRequiresUserAction={false}
      onMessage={handleMessage}
      // Android/iOS can kill the WebView's content process under memory
      // pressure; reload and let the handshake replay the host state.
      onContentProcessDidTerminate={() => webViewRef.current?.reload()}
    />
  );
});

export default EmulatorSurface;
