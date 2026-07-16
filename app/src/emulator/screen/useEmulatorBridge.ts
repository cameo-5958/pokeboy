/* eslint-disable react-hooks/exhaustive-deps -- preserve the original callback dependency arrays */
import AsyncStorage from "@react-native-async-storage/async-storage";
import { useCallback, useRef, type MutableRefObject } from "react";
import type WebView from "react-native-webview";
import type { WebViewMessageEvent } from "react-native-webview";

import { createApi } from "@/api/client";

import type { InputGroup, TelemetryEvent } from "./types";

export function useEmulatorBridge(options: {
  webViewRef: MutableRefObject<WebView | null>;
  inputRef: MutableRefObject<{ buttons: number; dpad: number }>;
  telemetry: boolean;
  telemetryEventsRef: MutableRefObject<TelemetryEvent[]>;
  telemetrySnapshotsRef: MutableRefObject<unknown[]>;
  api: ReturnType<typeof createApi>;
}) {
  const {
    webViewRef,
    inputRef,
    telemetry,
    telemetryEventsRef,
    telemetrySnapshotsRef,
    api,
  } = options;

  const postToEmulator = useCallback((message: object) => {
    webViewRef.current?.postMessage(JSON.stringify(message));
  }, []);
  // Dev-mode remote presses overlay the user's physical input; the emulator
  // always receives the OR of both so neither source can drop the other's held
  // buttons.
  const devInputRef = useRef({ buttons: 0, dpad: 0 });
  const sendMergedInput = useCallback(() => {
    postToEmulator({
      type: "input",
      buttons: inputRef.current.buttons | devInputRef.current.buttons,
      dpad: inputRef.current.dpad | devInputRef.current.dpad,
    });
  }, [postToEmulator]);
  // Battle Link DISC mode runs its Discord bot on the backend; the WebView
  // mod core talks to it over HTTP directly. Nothing to host on the phone.
  const onMessage = useCallback((event: WebViewMessageEvent) => {
    let message: { type?: unknown; detail?: unknown };
    try {
      message = JSON.parse(event.nativeEvent.data);
    } catch {
      return;
    }
    const detail = message?.detail;
    if (message?.type === "save") {
      if (!detail || typeof detail !== "object") return;
      const { id, ts, data } = detail as { id?: unknown; ts?: unknown; data?: unknown };
      if (typeof id !== "string" || typeof ts !== "number" || !Number.isFinite(ts) || ts < 0 || typeof data !== "string") return;
      AsyncStorage.setItem(`pokeboy.sav.${id}`, JSON.stringify({ v: 1, ts, data })).catch(() => {});
    } else if (message?.type === "save-request") {
      if (!detail || typeof detail !== "object") return;
      const { id, nonce } = detail as { id?: unknown; nonce?: unknown };
      if (typeof id !== "string") return;
      AsyncStorage.getItem(`pokeboy.sav.${id}`)
        .then((raw) => {
          let save: { ts: number; data: string } | null = null;
          try {
            const parsed = raw ? JSON.parse(raw) : null;
            if (parsed?.v === 1 && typeof parsed.ts === "number" && Number.isFinite(parsed.ts) && parsed.ts >= 0 && typeof parsed.data === "string") {
              save = { ts: parsed.ts, data: parsed.data };
            }
          } catch {}
          postToEmulator({ type: "save-data", id, nonce, ts: save?.ts ?? 0, data: save?.data ?? null });
        })
        .catch(() => postToEmulator({ type: "save-data", id, nonce, ts: 0, data: null }));
    } else if (message?.type === "save-corrupt") {
      console.warn("Pokeboy save corrupt", detail);
      if (telemetry) {
        telemetryEventsRef.current.push({ t: Date.now(), kind: "save-corrupt", detail });
      }
    } else if (message?.type === "error" || message?.type === "cache-error" || message?.type === "mod-event") {
      console.warn(`Pokeboy ${message.type}`, detail);
      if (telemetry) {
        telemetryEventsRef.current.push({ t: Date.now(), kind: message.type, detail });
      }
    } else if (message?.type === "telemetry") {
      if (telemetry) telemetrySnapshotsRef.current.push(detail);
    } else if (message?.type === "ready") {
      console.log("Pokeboy ready", detail);
    } else if (message?.type === "dev-frame") {
      if (!detail || typeof detail !== "object") return;
      const { nonce, png } = detail as { nonce?: unknown; png?: unknown };
      if (typeof nonce !== "string" || typeof png !== "string") return;
      api.postDevResult({ id: nonce, ok: true, png }).catch(() => {});
    } else if (
      message?.type === "mods" ||
      message?.type === "rom-source" ||
      message?.type === "payload-source"
    ) {
      console.log(`Pokeboy ${message.type}`, detail);
    }
  }, [postToEmulator, telemetry, api]);
  const setInput = useCallback((group: InputGroup, mask: number, held: boolean) => {
    const next = held ? inputRef.current[group] | mask : inputRef.current[group] & ~mask;
    inputRef.current = { ...inputRef.current, [group]: next };
    sendMergedInput();
  }, [sendMergedInput]);

  return { devInputRef, onMessage, postToEmulator, sendMergedInput, setInput };
}
