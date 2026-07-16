import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef } from "react";
import { View } from "react-native";

import type { EmulatorHandle, EmulatorSurfaceProps } from "./surface";

/**
 * Web host: the emulator document runs in a plain iframe.
 *
 * Deliberately thin. embed.html already listens on `window` for messages and
 * normalises its own outbound bridge, so the only real work here is answering
 * the runtime handshake — the iframe cannot be injected into before load the
 * way a WebView can, so the document asks for its config instead.
 */
const EmulatorSurface = forwardRef<EmulatorHandle, EmulatorSurfaceProps>(function EmulatorSurface(
  { uri, config, style, onMessage, onLoad },
  ref,
) {
  const frameRef = useRef<HTMLIFrameElement | null>(null);

  const post = useCallback((message: string) => {
    frameRef.current?.contentWindow?.postMessage(message, "*");
  }, []);

  useImperativeHandle(
    ref,
    () => ({
      postMessage: post,
      // Re-assigning src is the iframe equivalent of WebView.reload(); the
      // document then re-runs the handshake and host state is replayed.
      reload: () => {
        const frame = frameRef.current;
        if (frame) frame.src = frame.src;
      },
    }),
    [post],
  );

  // The listener is registered once; this keeps it reading current values
  // without tearing down and missing a handshake on every prop change.
  const latest = useRef({ config, onMessage, onLoad, post });
  latest.current = { config, onMessage, onLoad, post };

  useEffect(() => {
    function handle(event: MessageEvent) {
      if (event.source !== frameRef.current?.contentWindow) return;
      const data = event.data;
      if (typeof data !== "string") return;
      let type: string | undefined;
      try {
        type = JSON.parse(data)?.type;
      } catch {
        return; // embed.html only ever posts JSON strings
      }
      if (type === "runtime-request") {
        latest.current.post(JSON.stringify({ type: "runtime", detail: latest.current.config }));
        return;
      }
      if (type === "listening") {
        // Not at `runtime-request`: the document only attaches its message
        // listener after loading the core, so host state pushed any earlier is
        // silently dropped. This is the native onLoad equivalent, made explicit.
        latest.current.onLoad();
        return;
      }
      latest.current.onMessage({ nativeEvent: { data } });
    }
    window.addEventListener("message", handle);
    return () => window.removeEventListener("message", handle);
  }, []);

  return (
    <View style={style as never}>
      <iframe
        ref={frameRef}
        src={uri}
        title="Pokeboy emulator"
        // Display-only, exactly like the native surface: input arrives over
        // postMessage from the app's own on-screen controls.
        style={{ border: 0, width: "100%", height: "100%", display: "block", pointerEvents: "none" }}
        allow="autoplay"
      />
    </View>
  );
});

export default EmulatorSurface;
