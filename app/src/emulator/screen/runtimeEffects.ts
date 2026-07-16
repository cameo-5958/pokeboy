/* eslint-disable react-hooks/exhaustive-deps -- preserve the original effect dependency arrays */
import { useEffect, type Dispatch, type MutableRefObject, type SetStateAction } from "react";
import { AppState, Platform } from "react-native";
import type WebView from "react-native-webview";

import { createApi } from "@/api/client";

import { DEV_POLL_MS, LCD_DRAIN_MS, TELEMETRY_FLUSH_MS } from "./constants";
import type { TelemetryEvent } from "./types";

export function useRuntimeEffects(options: {
  postToEmulator: (message: object) => void;
  emulatorSettings: object;
  ejected: boolean;
  inputRef: MutableRefObject<{ buttons: number; dpad: number }>;
  devInputRef: MutableRefObject<{ buttons: number; dpad: number }>;
  sendMergedInput: () => void;
  lcdDrainTimerRef: MutableRefObject<ReturnType<typeof setTimeout> | null>;
  ejectedRef: MutableRefObject<boolean>;
  setLcdDead: Dispatch<SetStateAction<boolean>>;
  enabledMods: ReadonlySet<string>;
  telemetry: boolean;
  telemetrySnapshotsRef: MutableRefObject<unknown[]>;
  telemetryEventsRef: MutableRefObject<TelemetryEvent[]>;
  api: ReturnType<typeof createApi>;
  deviceIdRef: MutableRefObject<string | null>;
  sessionIdRef: MutableRefObject<string>;
  cartridgeIdRef: MutableRefObject<string | null>;
  devMode: boolean;
  webViewRef: MutableRefObject<WebView | null>;
  lcdDead: boolean;
}) {
  const {
    postToEmulator,
    emulatorSettings,
    ejected,
    inputRef,
    devInputRef,
    sendMergedInput,
    lcdDrainTimerRef,
    ejectedRef,
    setLcdDead,
    enabledMods,
    telemetry,
    telemetrySnapshotsRef,
    telemetryEventsRef,
    api,
    deviceIdRef,
    sessionIdRef,
    cartridgeIdRef,
    devMode,
    webViewRef,
    lcdDead,
  } = options;

  useEffect(() => {
    postToEmulator({
      type: "settings",
      ...emulatorSettings,
    });
  }, [emulatorSettings, postToEmulator]);

  useEffect(() => {
    if (ejected) {
      inputRef.current = { buttons: 0, dpad: 0 };
      devInputRef.current = { buttons: 0, dpad: 0 };
    }
    postToEmulator({ type: "paused", value: ejected });
    if (ejected) sendMergedInput();
    if (!ejected) return;

    const drainTimer = setTimeout(() => {
      lcdDrainTimerRef.current = null;
      if (ejectedRef.current) setLcdDead(true);
    }, LCD_DRAIN_MS);
    lcdDrainTimerRef.current = drainTimer;
    return () => {
      clearTimeout(drainTimer);
      if (lcdDrainTimerRef.current === drainTimer) lcdDrainTimerRef.current = null;
    };
  }, [ejected, postToEmulator, sendMergedInput]);

  useEffect(() => {
    postToEmulator({ type: "mods", ids: Array.from(enabledMods) });
  }, [enabledMods, postToEmulator]);

  // Telemetry flush: only active while the toggle is on. Buffers are swapped
  // out (not copied) so a slow network request can't double-send. Flushes on
  // a timer and eagerly when the app backgrounds, so nothing is lost on exit.
  useEffect(() => {
    if (!telemetry) {
      telemetrySnapshotsRef.current = [];
      telemetryEventsRef.current = [];
      return;
    }
    const flush = () => {
      const snapshots = telemetrySnapshotsRef.current;
      const events = telemetryEventsRef.current;
      if (snapshots.length === 0 && events.length === 0) return;
      telemetrySnapshotsRef.current = [];
      telemetryEventsRef.current = [];
      api
        .postTelemetry({
          device: { id: deviceIdRef.current ?? "unknown", os: Platform.OS },
          session: sessionIdRef.current,
          cartridge: cartridgeIdRef.current,
          snapshots,
          events,
        })
        .catch(() => {});
    };
    const interval = setInterval(flush, TELEMETRY_FLUSH_MS);
    const subscription = AppState.addEventListener("change", (state) => {
      if (state === "background" || state === "inactive") flush();
    });
    return () => {
      clearInterval(interval);
      subscription.remove();
      telemetrySnapshotsRef.current = [];
      telemetryEventsRef.current = [];
    };
  }, [telemetry, api]);

  // Dev mode: poll the backend for remote-control commands (MCP-issued button
  // presses and LCD screenshot requests) while the toggle is on.
  useEffect(() => {
    if (!devMode) return;
    let alive = true;
    const pressTimers = new Set<ReturnType<typeof setTimeout>>();

    const applyPress = (cmd: { buttons: number; dpad: number; holdMs: number }) => {
      devInputRef.current = {
        buttons: devInputRef.current.buttons | cmd.buttons,
        dpad: devInputRef.current.dpad | cmd.dpad,
      };
      sendMergedInput();
      const timer = setTimeout(() => {
        pressTimers.delete(timer);
        devInputRef.current = {
          buttons: devInputRef.current.buttons & ~cmd.buttons,
          dpad: devInputRef.current.dpad & ~cmd.dpad,
        };
        sendMergedInput();
      }, Math.min(Math.max(cmd.holdMs, 16), 5000));
      pressTimers.add(timer);
    };

    const tick = async () => {
      const deviceId = deviceIdRef.current;
      if (!deviceId) return;
      let commands;
      try {
        commands = await api.pollDevCommands(deviceId);
      } catch {
        return; // backend unreachable — try again next tick
      }
      if (!alive) return;
      for (const cmd of commands) {
        if (cmd.kind === "press") {
          applyPress(cmd);
          api.postDevResult({ id: cmd.id, ok: true }).catch(() => {});
        } else if (cmd.kind === "screenshot") {
          if (webViewRef.current && !lcdDead) {
            // The embed replies with a dev-frame message; onMessage forwards
            // the PNG to the backend. Its dispatch timeout covers a dead page.
            postToEmulator({ type: "dev-frame", nonce: cmd.id });
          } else {
            api.postDevResult({ id: cmd.id, ok: false, error: "Emulator not running" }).catch(() => {});
          }
        }
      }
    };

    const interval = setInterval(tick, DEV_POLL_MS);
    void tick();
    return () => {
      alive = false;
      clearInterval(interval);
      for (const timer of pressTimers) clearTimeout(timer);
      devInputRef.current = { buttons: 0, dpad: 0 };
      sendMergedInput();
    };
  }, [devMode, api, lcdDead, postToEmulator, sendMergedInput]);
}
