import { Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import {
  Animated,
  AppState,
  Image,
  Modal,
  PanResponder,
  Platform,
  Pressable,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
  type LayoutChangeEvent,
  useWindowDimensions,
} from "react-native";
import WebView, { type WebViewMessageEvent } from "react-native-webview";

import { createApi, type Cartridge as CartridgeInfo } from "@/api/client";
import { playSfx } from "@/sfx";
import {
  diffSection,
  loadInstalled,
  saveInstalled,
  useSettings,
  type DiffEntry,
} from "@/settings";

// Native Game Boy screen is 160x144. The LCD keeps that ratio; the emulator
// mounts its framebuffer into the LCD well later.
const SCREEN_RATIO = 160 / 144;

// The console keeps the same formation. Ejecting pops the cartridge out of the
// top slot, then the whole console slides down from under it — the cartridge
// always renders BEHIND the console body, never over it.
const PAD_TOP = 28;
const PAD_BOTTOM = 28;
const PULL_DIST = 56; // drag distance that fully pulls the cartridge out
const SPEEDS = ["x0.5", "x1", "x3", "xINF"] as const;

const DEVICE_ID_KEY = "pokeboy.device-id.v1";
const TELEMETRY_FLUSH_MS = 5000;
const LCD_DRAIN_MS = 300;
const LABEL_CACHE_PREFIX = "pokeboy.label.v1:";
const CARTRIDGE_RETRY_MS = 15000;
const DEV_POLL_MS = 750;

function blobToDataUri(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      if (typeof reader.result === "string") resolve(reader.result);
      else reject(new Error("Could not encode cartridge label"));
    };
    reader.onerror = () => reject(reader.error ?? new Error("Could not read cartridge label"));
    reader.onabort = () => reject(new Error("Cartridge label read was aborted"));
    reader.readAsDataURL(blob);
  });
}

/** Short opaque id — good enough to distinguish devices/sessions, not a real UUID. */
function genId(): string {
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

type TelemetryEvent = { t: number; kind: string; detail: unknown };

type Unit = (value: number) => number;
type Translate = Animated.AnimatedInterpolation<number>;
type AnimValue = Animated.Value;
type PanHandlers = ReturnType<typeof PanResponder.create>["panHandlers"];
type InputGroup = "buttons" | "dpad";

const EmulatorContext = createContext<{
  uri: string | null;
  webViewRef: { current: WebView | null };
  onMessage: (event: WebViewMessageEvent) => void;
  setInput: (group: InputGroup, mask: number, held: boolean) => void;
  settings: { speed: number | "inf"; muted: boolean; volume: number; telemetry: boolean };
  paused: boolean;
  mods: readonly string[];
}>({
  uri: null,
  webViewRef: { current: null },
  onMessage: () => undefined,
  setInput: () => undefined,
  settings: { speed: 1, muted: false, volume: 1, telemetry: false },
  paused: false,
  mods: [],
});

// Real DMG carts are nearly square; the label sticker (where the game image
// mounts) dominates the front face.
function cartMetrics(u: Unit, width: number) {
  const labelWidth = width - u(24);
  const labelHeight = labelWidth * 0.85;
  // paddings + grip ridges above the label + contact strip below it
  return { labelWidth, labelHeight, height: labelHeight + u(58) };
}

// Hardware add-ons that can be snapped onto the console. Pure client state —
// enabling one just tracks it locally (behavior wiring comes later).
const MODS = [
  {
    id: "tradeback-npc",
    name: "TRADEBACK NPC",
    desc: "Trades back the first Pokemon in your party at Celadon Center.",
  },
] as const;

// Fixed-size pieces so the panel stack height is known up front — portrait
// uses it to park the ejected cartridge low enough to leave room above.
function modMetrics(u: Unit) {
  const pad = u(10);
  const nameH = u(18); // name row, flanked by the scroll arrows
  const descH = u(24); // two lines of description
  const btnH = u(22); // YES / NO row
  const configH = u(22); // per-mod CONFIG button below the YES / NO row
  const gap = u(6);
  const stackGap = u(8); // between the browser panel and the count panel
  const countH = u(24);
  const settingsH = u(26);
  const panelH = pad * 2 + nameH + gap + descH + gap + btnH + gap + configH;
  return {
    pad,
    nameH,
    descH,
    btnH,
    configH,
    gap,
    stackGap,
    countH,
    settingsH,
    height: panelH + stackGap + countH + stackGap + settingsH,
  };
}

export default function EmulatorScreen() {
  const { width, height } = useWindowDimensions();
  // Declare mod state before any derived values, effects, or callbacks. This
  // also keeps Metro's transformed module clear of temporal-dead-zone access.
  const [enabledMods, setEnabledMods] = useState<ReadonlySet<string>>(() => new Set());
  // Backend client bound to the user's saved URL + key, so the cartridge list
  // and emulator WebView both hit the configured backend.
  const { settings, settingsLoaded } = useSettings();
  const api = useMemo(
    () => createApi({ baseUrl: settings.backendUrl, apiKey: settings.apiKey }),
    [settings.backendUrl, settings.apiKey],
  );
  const landscape = width > height;

  const bodyWidth = landscape ? width * 0.96 : Math.min(width * 0.92, height * 0.5);
  const bodyHeight = height - PAD_TOP - PAD_BOTTOM;
  const scale = (landscape ? bodyHeight : bodyWidth) / 360;
  const u: Unit = (v) => v * scale;

  const cartWidth = landscape ? u(178) : bodyWidth * 0.66;
  const cart = cartMetrics(u, cartWidth);
  // Landscape hugs the left grip, but never so far left that the selector
  // arrow (size u34 + gap u12) would fall off screen.
  const slotLeft = landscape
    ? Math.max(u(30), u(46) + 8 - (width - bodyWidth) / 2)
    : (bodyWidth - cartWidth) / 2;

  // How much of the seated cartridge peeks above the slot.
  const lip = u(12);
  // Phase-1 pop. Landscape fills the viewport vertically, so cap the pop to
  // the headroom above the shell to keep the cartridge on screen.
  const popOut = landscape ? Math.max(PAD_TOP - lip - 4, 4) : u(34);
  // Once dropped, the cartridge parks at the top of the page (matters in
  // portrait, where the centered shell leaves headroom above it) and the
  // shell drops just far enough below to clear it with a small gap.
  const settle = landscape ? u(6) : 0;
  const [bodyTop, setBodyTop] = useState(PAD_TOP);
  const mod = modMetrics(u);
  // Portrait sinks the shell well below the cartridge to free up the middle
  // of the screen; landscape only needs enough to clear it.
  const clearance = landscape ? u(16) : u(160);
  const modGap = mod.height + u(14);
  // Portrait centers the ejected cartridge + mod stack in the empty space
  // above the lowered shell; landscape keeps it at the top with the panel right.
  const parkTop = landscape ? PAD_TOP : clearance + modGap;
  const parkDelta = landscape ? Math.max(0, bodyTop - parkTop) : bodyTop - parkTop;
  const bodyDrop = Math.max(0, cart.height + settle + clearance - parkDelta);

  // 0 = inserted, 1 = cartridge popped out, 2 = console dropped down.
  const anim = useRef(new Animated.Value(0)).current;
  const ejectedRef = useRef(false);
  const startVal = useRef(0);
  // State mirror of ejectedRef so the mod panel can gate its touch handling
  // (it sits at opacity 0 while the cartridge is seated).
  const [ejected, setEjected] = useState(false);
  const [lcdDead, setLcdDead] = useState(false);
  const lcdDrainTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [volume, setVolume] = useState(0.72);
  const [muted, setMuted] = useState(false);
  const [speedIdx, setSpeedIdx] = useState(1);
  const [cartridges, setCartridges] = useState<CartridgeInfo[]>([]);
  const [cartridgeIdx, setCartridgeIdx] = useState(0);
  const [labelImages, setLabelImages] = useState<Record<string, string>>({});
  const requestedLabelUrlsRef = useRef(new Set<string>());
  const refreshedLabelUrlsRef = useRef(new Set<string>());
  const mountedRef = useRef(true);
  const webViewRef = useRef<WebView | null>(null);
  const inputRef = useRef({ buttons: 0, dpad: 0 });
  const cartridge = cartridges[cartridgeIdx] ?? null;
  // Latest cartridge id, readable from the flush interval without adding
  // `cartridge` to that effect's deps (which would restart the timer).
  const cartridgeIdRef = useRef<string | null>(null);
  cartridgeIdRef.current = cartridge?.id ?? null;

  // Opt-in telemetry: identity + buffers. Buffers are refs (not state) so a
  // snapshot arriving every second never triggers a re-render or changes any
  // memoized prop identity (WebView, context value, etc.).
  const deviceIdRef = useRef<string | null>(null);
  const sessionIdRef = useRef<string>(genId());
  const telemetrySnapshotsRef = useRef<unknown[]>([]);
  const telemetryEventsRef = useRef<TelemetryEvent[]>([]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    let alive = true;
    AsyncStorage.getItem(DEVICE_ID_KEY)
      .then((raw) => {
        if (!alive) return;
        if (raw) {
          deviceIdRef.current = raw;
          return;
        }
        const id = genId();
        deviceIdRef.current = id;
        AsyncStorage.setItem(DEVICE_ID_KEY, id).catch(() => {});
      })
      .catch(() => {
        // Fall back to a session-only id if storage is unavailable.
        if (alive && !deviceIdRef.current) deviceIdRef.current = genId();
      });
    return () => {
      alive = false;
    };
  }, []);
  const emulatorSettings = useMemo(
    () => ({
      speed: (SPEEDS[speedIdx] === "xINF" ? "inf" : Number(SPEEDS[speedIdx].slice(1))) as number | "inf",
      muted,
      volume,
      telemetry: settings.telemetry,
    }),
    [speedIdx, muted, volume, settings.telemetry],
  );

  // The list is local-first, so a launch while the backend is unreachable
  // (e.g. VPN not up yet) resolves from the offline cache — which can include
  // cartridges since removed server-side. Keep retrying, and re-check when the
  // app foregrounds, until a fresh response replaces any stale entries.
  useEffect(() => {
    if (!settingsLoaded) return;
    let alive = true;
    let retry: ReturnType<typeof setTimeout> | null = null;
    const load = async () => {
      try {
        const { data, fresh } = await api.listCartridges();
        if (!alive) return;
        setCartridges(data);
        setCartridgeIdx((i) => Math.min(i, Math.max(0, data.length - 1)));
        if (!fresh) retry = setTimeout(load, CARTRIDGE_RETRY_MS);
      } catch {
        if (!alive) return;
        setCartridges([]);
        retry = setTimeout(load, CARTRIDGE_RETRY_MS);
      }
    };
    load();
    const subscription = AppState.addEventListener("change", (state) => {
      if (state === "active") load();
    });
    return () => {
      alive = false;
      if (retry) clearTimeout(retry);
      subscription.remove();
    };
  }, [api, settingsLoaded]);

  const labelUrls = useMemo(
    () => Array.from(new Set(cartridges.flatMap((item) => (item.img ? [item.img] : [])))),
    [cartridges],
  );

  useEffect(() => {
    for (const url of labelUrls) {
      if (requestedLabelUrlsRef.current.has(url)) continue;
      requestedLabelUrlsRef.current.add(url);
      const cacheKey = `${LABEL_CACHE_PREFIX}${url}`;

      AsyncStorage.getItem(cacheKey)
        .then((cached) => {
          if (
            !cached ||
            !mountedRef.current ||
            refreshedLabelUrlsRef.current.has(url)
          ) return;
          setLabelImages((previous) =>
            previous[url] === cached ? previous : { ...previous, [url]: cached },
          );
        })
        .catch(() => {});

      fetch(url)
        .then((response) => {
          if (!response.ok) throw new Error(`Label request failed: ${response.status}`);
          return response.blob();
        })
        .then(blobToDataUri)
        .then((dataUri) => {
          refreshedLabelUrlsRef.current.add(url);
          AsyncStorage.setItem(cacheKey, dataUri).catch(() => {});
          if (!mountedRef.current) return;
          setLabelImages((previous) =>
            previous[url] === dataUri ? previous : { ...previous, [url]: dataUri },
          );
        })
        .catch((error) => {
          // Un-mark the URL so the next cartridge-list refresh retries it
          // (a launch-time failure would otherwise hide labels all session).
          requestedLabelUrlsRef.current.delete(url);
          console.warn("Pokeboy label fetch failed", url, error);
        });
    }
  }, [labelUrls]);

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
      if (settings.telemetry) {
        telemetryEventsRef.current.push({ t: Date.now(), kind: "save-corrupt", detail });
      }
    } else if (message?.type === "error" || message?.type === "cache-error") {
      console.warn(`Pokeboy ${message.type}`, detail);
      if (settings.telemetry) {
        telemetryEventsRef.current.push({ t: Date.now(), kind: message.type, detail });
      }
    } else if (message?.type === "telemetry") {
      if (settings.telemetry) telemetrySnapshotsRef.current.push(detail);
    } else if (message?.type === "dev-frame") {
      if (!detail || typeof detail !== "object") return;
      const { nonce, png } = detail as { nonce?: unknown; png?: unknown };
      if (typeof nonce !== "string" || typeof png !== "string") return;
      api.postDevResult({ id: nonce, ok: true, png }).catch(() => {});
    } else if (
      message?.type === "ready" ||
      message?.type === "mods" ||
      message?.type === "rom-source" ||
      message?.type === "payload-source"
    ) {
      console.log(`Pokeboy ${message.type}`, detail);
    }
  }, [postToEmulator, settings.telemetry, api]);
  const setInput = useCallback((group: InputGroup, mask: number, held: boolean) => {
    const next = held ? inputRef.current[group] | mask : inputRef.current[group] & ~mask;
    inputRef.current = { ...inputRef.current, [group]: next };
    sendMergedInput();
  }, [sendMergedInput]);

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
    if (!settings.telemetry) {
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
  }, [settings.telemetry, api]);

  // Dev mode: poll the backend for remote-control commands (MCP-issued button
  // presses and LCD screenshot requests) while the toggle is on.
  useEffect(() => {
    if (!settings.devMode) return;
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
  }, [settings.devMode, api, lcdDead, postToEmulator, sendMergedInput]);

  const selectCartridge = (direction: -1 | 1) => {
    if (cartridges.length < 2) return;
    playSfx("select");
    setCartridgeIdx((current) => (current + direction + cartridges.length) % cartridges.length);
  };
  const cycleSpeed = () => {
    playSfx("select");
    setSpeedIdx((i) => (i + 1) % SPEEDS.length);
  };
  const toggleMute = () => {
    playSfx("select");
    setMuted((v) => !v);
  };

  // Enabled mods, by id.
  const setMod = (id: string, on: boolean) => {
    if (enabledMods.has(id) === on) return;
    playSfx(on ? "modOn" : "modOff");
    setEnabledMods((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  };

  const pan = useRef(
    (() => {
      const eject = () => {
        ejectedRef.current = true;
        setEjected(true);
        playSfx("eject");
        anim.stopAnimation(() =>
          Animated.sequence([
            Animated.timing(anim, { toValue: 1, duration: 190, useNativeDriver: false }),
            Animated.spring(anim, { toValue: 2, useNativeDriver: false, bounciness: 6, speed: 11 }),
          ]).start(),
        );
      };
      const insert = () => {
        ejectedRef.current = false;
        if (lcdDrainTimerRef.current !== null) {
          clearTimeout(lcdDrainTimerRef.current);
          lcdDrainTimerRef.current = null;
        }
        setLcdDead(false);
        setEjected(false);
        playSfx("insert");
        anim.stopAnimation((v) => {
          if (v > 1) {
            Animated.sequence([
              Animated.spring(anim, { toValue: 1, useNativeDriver: false, bounciness: 3, speed: 12 }),
              Animated.timing(anim, { toValue: 0, duration: 190, useNativeDriver: false }),
            ]).start(({ finished }) => {
              if (finished) playSfx("snap");
            });
          } else {
            Animated.timing(anim, { toValue: 0, duration: 180, useNativeDriver: false }).start(({ finished }) => {
              if (finished) playSfx("snap");
            });
          }
        });
      };
      const toggle = () => (ejectedRef.current ? insert() : eject());

      return PanResponder.create({
        onStartShouldSetPanResponder: () => true,
        onMoveShouldSetPanResponder: (_, g) => Math.abs(g.dy) > 3,
        onPanResponderGrant: () => anim.stopAnimation((v) => (startVal.current = v)),
        onPanResponderMove: (_, g) => {
          if (ejectedRef.current) return; // only live-scrub the pull, not the drop
          // Dragging up pulls the cartridge out of the slot.
          anim.setValue(Math.max(0, Math.min(1, startVal.current - g.dy / PULL_DIST)));
        },
        onPanResponderRelease: (_, g) => {
          const moved = Math.abs(g.dy) + Math.abs(g.dx);
          if (moved < 6 || ejectedRef.current) return toggle();
          anim.stopAnimation((v) => (v >= 0.6 ? eject() : insert()));
        },
        onPanResponderTerminationRequest: () => false,
      });
    })(),
  ).current;

  const bodyTranslate = anim.interpolate({ inputRange: [0, 1, 2], outputRange: [0, 0, bodyDrop] });
  // The cartridge is a child of the dropping wrapper, so phase 2 subtracts the
  // drop to leave it (almost) fixed on screen while the console slides away.
  const cartTranslate = anim.interpolate({
    inputRange: [0, 1, 2],
    outputRange: [0, -popOut, lip + settle - bodyDrop - parkDelta],
  });

  const onBodyLayout = (e: LayoutChangeEvent) => setBodyTop(e.nativeEvent.layout.y);

  const emulatorUri = useMemo(
    () => (cartridge ? api.emulatorUrl(cartridge.id, cartridge.version) : null),
    [api, cartridge?.id, cartridge?.version],
  );
  const modsList = useMemo(() => Array.from(enabledMods), [enabledMods]);
  const emulatorContextValue = useMemo(
    () => ({
      uri: lcdDead ? null : emulatorUri,
      webViewRef,
      onMessage,
      setInput,
      settings: emulatorSettings,
      paused: ejected,
      mods: modsList,
    }),
    [emulatorUri, lcdDead, webViewRef, onMessage, setInput, emulatorSettings, ejected, modsList],
  );

  return (
    <EmulatorContext.Provider value={emulatorContextValue}>
    <View style={styles.page}>
      <Stack.Screen options={{ headerShown: false }} />
      <StatusBar hidden />
      <Animated.View onLayout={onBodyLayout} style={{ transform: [{ translateY: bodyTranslate }] }}>
        {/* Cartridge layer — kept UNDER the console body (zIndex 0 vs 1) so it
            emerges from behind the top edge and slides back under on insert. */}
        <Animated.View
          style={{
            position: "absolute",
            top: -lip,
            left: slotLeft,
            width: cartWidth,
            height: cart.height,
            zIndex: 0,
            transform: [{ translateY: cartTranslate }],
          }}
        >
          <Cartridge
            u={u}
            width={cartWidth}
            labelWidth={cart.labelWidth}
            labelHeight={cart.labelHeight}
            height={cart.height}
            panHandlers={pan.panHandlers}
            image={cartridge?.img ? labelImages[cartridge.img] ?? cartridge.img : null}
          />
          <CartridgeArrow u={u} anim={anim} side="left" cartHeight={cart.height} onPress={() => selectCartridge(-1)} />
          <CartridgeArrow u={u} anim={anim} side="right" cartHeight={cart.height} onPress={() => selectCartridge(1)} />
          <ModChanger
            u={u}
            anim={anim}
            active={ejected}
            landscape={landscape}
            cartWidth={cartWidth}
            enabled={enabledMods}
            onSet={setMod}
          />
        </Animated.View>

        {landscape ? (
          <LandscapeConsole
            bodyWidth={bodyWidth}
            bodyHeight={bodyHeight}
            u={u}
            slotLeft={slotLeft}
            slotWidth={cartWidth}
            panHandlers={pan.panHandlers}
            volume={volume}
            muted={muted}
            speed={SPEEDS[speedIdx]}
            onVolume={setVolume}
            onMute={toggleMute}
            onCycleSpeed={cycleSpeed}
          />
        ) : (
          <PortraitConsole
            bodyWidth={bodyWidth}
            u={u}
            slotLeft={slotLeft}
            slotWidth={cartWidth}
            panHandlers={pan.panHandlers}
            volume={volume}
            muted={muted}
            speed={SPEEDS[speedIdx]}
            onVolume={setVolume}
            onMute={toggleMute}
            onCycleSpeed={cycleSpeed}
          />
        )}
      </Animated.View>
    </View>
    </EmulatorContext.Provider>
  );
}

/* ------------------------------------------------------------------ *
 * Layouts
 * ------------------------------------------------------------------ */

function PortraitConsole({
  bodyWidth,
  u,
  slotLeft,
  slotWidth,
  panHandlers,
  volume,
  muted,
  speed,
  onVolume,
  onMute,
  onCycleSpeed,
}: {
  bodyWidth: number;
  u: Unit;
  slotLeft: number;
  slotWidth: number;
  panHandlers: PanHandlers;
  volume: number;
  muted: boolean;
  speed: (typeof SPEEDS)[number];
  onVolume: (value: number) => void;
  onMute: () => void;
  onCycleSpeed: () => void;
}) {
  const screenWidth = bodyWidth * 0.82;
  const screenHeight = screenWidth / SCREEN_RATIO;

  return (
    <View
      style={[
        styles.body,
        {
          width: bodyWidth,
          borderRadius: u(22),
          borderBottomRightRadius: u(66),
          padding: u(18),
          paddingTop: u(26),
        },
      ]}
    >
      <CartSlot u={u} left={slotLeft} width={slotWidth} panHandlers={panHandlers} />
      <Bezel u={u} screenWidth={screenWidth} screenHeight={screenHeight} variant="portrait" />
      <MiniControls
        u={u}
        volume={volume}
        muted={muted}
        speed={speed}
        onVolume={onVolume}
        onMute={onMute}
        onCycleSpeed={onCycleSpeed}
      />
      <Wordmark u={u} style={{ alignSelf: "flex-start", marginTop: u(14), marginLeft: u(4) }} />

      <View style={{ width: "100%", marginTop: u(18), gap: u(16) }}>
        <View style={styles.portraitTopRow}>
          <Dpad u={u} size={u(108)} />
          <FaceButtons u={u} />
        </View>
        <PillRow u={u} />
      </View>
    </View>
  );
}

function LandscapeConsole({
  bodyWidth,
  bodyHeight,
  u,
  slotLeft,
  slotWidth,
  panHandlers,
  volume,
  muted,
  speed,
  onVolume,
  onMute,
  onCycleSpeed,
}: {
  bodyWidth: number;
  bodyHeight: number;
  u: Unit;
  slotLeft: number;
  slotWidth: number;
  panHandlers: PanHandlers;
  volume: number;
  muted: boolean;
  speed: (typeof SPEEDS)[number];
  onVolume: (value: number) => void;
  onMute: () => void;
  onCycleSpeed: () => void;
}) {
  // Wide shell, shrunk vertically: the left grip carries the slot up top with
  // the "Poké boy" mark over the D-pad, the screen sits in the middle, and the
  // action buttons ride low on the right.
  // As tall as the bezel allows, with a small strip reserved under it.
  const screenHeight = bodyHeight - u(92);
  const screenWidth = screenHeight * SCREEN_RATIO;

  return (
    <View
      style={[
        styles.body,
        styles.bodyLandscape,
        {
          width: bodyWidth,
          height: bodyHeight,
          borderRadius: u(30),
          borderBottomRightRadius: u(58),
          paddingHorizontal: u(30),
          paddingVertical: u(16),
        },
      ]}
    >
      <CartSlot u={u} left={slotLeft} width={slotWidth} panHandlers={panHandlers} />

      {/* Left grip: wordmark over D-pad, sized to match the slot above it */}
      <View style={[styles.landZone, { width: slotWidth }]}>
        <Wordmark u={u} style={{ marginBottom: u(14) }} />
        <Dpad u={u} size={u(112)} />
      </View>

      {/* Center: screen */}
      <View style={[styles.landZone, { width: screenWidth }]}>
        <Bezel u={u} screenWidth={screenWidth} screenHeight={screenHeight} variant="landscape" />
        <MiniControls
          u={u}
          volume={volume}
          muted={muted}
          speed={speed}
          onVolume={onVolume}
          onMute={onMute}
          onCycleSpeed={onCycleSpeed}
          width={screenWidth}
        />
      </View>

      {/* Right grip: A/B (wide) over Start/Select, vertically centered */}
      <View style={[styles.landZone, { gap: u(48) }]}>
        <FaceButtons u={u} w={u(70)} h={u(58)} />
        <PillRow u={u} gap={u(18)} />
      </View>
    </View>
  );
}

/* ------------------------------------------------------------------ *
 * Cartridge
 * ------------------------------------------------------------------ */

// The slot mouth on the shell's top edge. Also a grab zone, so the seated
// cartridge can be tapped/pulled even though only its lip peeks out.
function CartSlot({
  u,
  left,
  width,
  panHandlers,
}: {
  u: Unit;
  left: number;
  width: number;
  panHandlers: PanHandlers;
}) {
  return (
    <View
      {...panHandlers}
      style={{
        position: "absolute",
        top: 0,
        left: left - u(9),
        width: width + u(18),
        height: u(30),
        zIndex: 3,
        alignItems: "stretch",
      }}
    >
      <View
        style={[
          styles.cartSlot,
          { height: u(7), borderBottomLeftRadius: u(3), borderBottomRightRadius: u(3) },
        ]}
      />
    </View>
  );
}

function Cartridge({
  u,
  width,
  height,
  labelWidth,
  labelHeight,
  panHandlers,
  image,
}: {
  u: Unit;
  width: number;
  height: number;
  labelWidth: number;
  labelHeight: number;
  panHandlers: PanHandlers;
  image: string | null;
}) {
  return (
    <View
      {...panHandlers}
      style={[
        styles.cartBody,
        {
          width,
          height,
          borderTopLeftRadius: u(10),
          borderTopRightRadius: u(10),
          borderBottomLeftRadius: u(6),
          borderBottomRightRadius: u(6),
          borderWidth: u(1.5),
          paddingTop: u(8),
          paddingBottom: u(9),
          paddingHorizontal: u(12),
        },
      ]}
    >
      {/* Grip ridges */}
      <View style={[styles.cartRidges, { marginBottom: u(6) }]}>
        {[0, 1, 2].map((i) => (
          <View
            key={i}
            style={[styles.cartRidge, { height: u(2), marginBottom: u(3), borderRadius: u(1) }]}
          />
        ))}
      </View>
      {/* Label sticker — the game image mounts inside the reserve */}
      <View
        style={[
          styles.cartLabel,
          { width: labelWidth, height: labelHeight, borderRadius: u(5), padding: u(5) },
        ]}
      >
        {image ? (
          <Image source={{ uri: image }} style={[styles.cartImageReserve, { borderRadius: u(3) }]} resizeMode="cover" />
        ) : (
          <View style={[styles.cartImageReserve, { borderRadius: u(3) }]} />
        )}
      </View>
      {/* Edge connector */}
      <View style={[styles.cartBottom, { height: u(12), marginTop: u(8), borderRadius: u(2) }]}>
        {[0, 1, 2, 3, 4].map((i) => (
          <View key={i} style={[styles.cartContact, { width: u(6), borderRadius: u(1) }]} />
        ))}
      </View>
    </View>
  );
}

// Cartridge selector chevrons. Fixed circles beside the cartridge that fade
// and scale in as the console drops away (selection wiring comes later).
function CartridgeArrow({
  u,
  anim,
  side,
  cartHeight,
  onPress,
}: {
  u: Unit;
  anim: AnimValue;
  side: "left" | "right";
  cartHeight: number;
  onPress: () => void;
}) {
  const size = u(34);
  const opacity = anim.interpolate({ inputRange: [1, 2], outputRange: [0, 1], extrapolate: "clamp" });
  const grow = anim.interpolate({ inputRange: [1, 2], outputRange: [0.5, 1], extrapolate: "clamp" });
  return (
    <Animated.View
      style={[
        {
          position: "absolute",
          top: cartHeight / 2 - size / 2,
          opacity,
          transform: [{ scale: grow }],
        },
        side === "left" ? { left: -(size + u(12)) } : { right: -(size + u(12)) },
      ]}
    >
      <Pressable
        onPress={onPress}
        style={({ pressed }) => [
          styles.cartArrow,
          { width: size, height: size, borderRadius: size / 2 },
          pressed && { opacity: 0.6 },
        ]}
      >
        <Text selectable={false} style={[styles.cartArrowText, { fontSize: size * 0.62 }]}>
          {side === "left" ? "‹" : "›"}
        </Text>
      </Pressable>
    </Animated.View>
  );
}

/* ------------------------------------------------------------------ *
 * Mod changer
 * ------------------------------------------------------------------ */

// Accessory bay for snapping mods onto the console. Anchored to the ejected
// cartridge — right of it in landscape, above it in portrait — and fades in
// with the console drop, like the selector arrows. Two stacked panels: a
// one-mod-at-a-time browser (name, blurb, YES/NO), and an enabled count.
function ModChanger({
  u,
  anim,
  active,
  landscape,
  cartWidth,
  enabled,
  onSet,
}: {
  u: Unit;
  anim: AnimValue;
  active: boolean;
  landscape: boolean;
  cartWidth: number;
  enabled: ReadonlySet<string>;
  onSet: (id: string, on: boolean) => void;
}) {
  const [idx, setIdx] = useState(0);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [configOpen, setConfigOpen] = useState(false);
  const m = modMetrics(u);
  const width = landscape ? u(170) : cartWidth + u(24);
  const opacity = anim.interpolate({ inputRange: [1, 2], outputRange: [0, 1], extrapolate: "clamp" });
  const grow = anim.interpolate({ inputRange: [1, 2], outputRange: [0.7, 1], extrapolate: "clamp" });

  const mod = MODS[idx];
  const on = enabled.has(mod.id);
  const scroll = (dir: 1 | -1) => {
    playSfx("dpad");
    setIdx((i) => (i + dir + MODS.length) % MODS.length);
  };
  const toggleSettings = () => {
    playSfx("select");
    setSettingsOpen((v) => !v);
  };
  const toggleConfig = () => {
    playSfx("select");
    setConfigOpen((v) => !v);
  };

  return (
    <Animated.View
      pointerEvents={active ? "auto" : "none"}
      style={[
        { position: "absolute", width, opacity, transform: [{ scale: grow }] },
        landscape
          ? // Clear the right selector arrow (offset u12 + size u34) plus a gap.
            { left: cartWidth + u(58), top: 0 }
          : { left: (cartWidth - width) / 2, top: -(m.height + u(14)) },
      ]}
    >
      {/* Panel 1: mod browser */}
      <View style={[styles.modPanel, { borderRadius: u(10), padding: m.pad }]}>
        <View style={[styles.modNameRow, { height: m.nameH }]}>
          <ModScroll u={u} h={m.nameH} dir={-1} onPress={scroll} />
          <Text selectable={false} style={[styles.modName, { fontSize: u(9), letterSpacing: u(1) }]}>
            {mod.name}
          </Text>
          <ModScroll u={u} h={m.nameH} dir={1} onPress={scroll} />
        </View>
        <Text
          selectable={false}
          numberOfLines={2}
          style={[
            styles.modDesc,
            { height: m.descH, fontSize: u(8), lineHeight: u(12), marginTop: m.gap },
          ]}
        >
          {mod.desc}
        </Text>
        <View style={{ flexDirection: "row", gap: m.gap, marginTop: m.gap }}>
          <ModChoice u={u} h={m.btnH} label="YES" active={on} onPress={() => onSet(mod.id, true)} />
          <ModChoice u={u} h={m.btnH} label="NO" active={!on} onPress={() => onSet(mod.id, false)} />
        </View>
        <Pressable
          onPress={toggleConfig}
          style={({ pressed }) => [
            styles.settingsButton,
            { height: m.configH, borderRadius: u(5), marginTop: m.gap },
            pressed && { opacity: 0.62 },
          ]}
        >
          <Text
            selectable={false}
            style={[styles.modName, styles.settingsButtonText, { fontSize: u(8), letterSpacing: u(1) }]}
          >
            CONFIG
          </Text>
        </Pressable>
      </View>

      {active ? <ModConfigModal open={configOpen} mod={mod} onClose={toggleConfig} /> : null}

      {/* Panel 2: enabled count */}
      <View
        style={[
          styles.modPanel,
          styles.modCountRow,
          { height: m.countH, marginTop: m.stackGap, borderRadius: u(8), paddingHorizontal: m.pad },
        ]}
      >
        <Text selectable={false} style={[styles.modName, { fontSize: u(8), letterSpacing: u(1) }]}>
          ENABLED
        </Text>
        <Text selectable={false} style={[styles.modCountValue, { fontSize: u(9) }]}>
          {enabled.size}/{MODS.length}
        </Text>
      </View>

      {active ? (
        <>
          <View
            style={[
              styles.modPanel,
              { height: m.settingsH, marginTop: m.stackGap, borderRadius: u(8), padding: u(4) },
            ]}
          >
            <Pressable
              onPress={toggleSettings}
              style={({ pressed }) => [
                styles.settingsButton,
                { flex: 1, borderRadius: u(5) },
                settingsOpen && styles.settingsButtonOn,
                pressed && { opacity: 0.62 },
              ]}
            >
              <Text
                selectable={false}
                style={[
                  styles.modName,
                  styles.settingsButtonText,
                  { fontSize: u(8), letterSpacing: u(1) },
                  settingsOpen && styles.settingsButtonTextOn,
                ]}
              >
                SETTINGS
              </Text>
            </Pressable>
          </View>
          <SettingsModal open={settingsOpen} onClose={toggleSettings} />
        </>
      ) : null}
    </Animated.View>
  );
}

type PullState =
  | { phase: "idle" }
  | { phase: "loading" }
  | { phase: "error"; message: string }
  | { phase: "done"; changed: DiffEntry[]; total: number; newCount: number; updateCount: number };

// True full-screen settings dialog. Rendered through React Native's Modal so it
// escapes the widget's transformed layer and dims the whole screen. Holds the
// locally-persisted backend connection (URL + API key) and the "pull latest"
// action that syncs the installed ROM/mod manifest with the backend registry.
function SettingsModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { width } = useWindowDimensions();
  const cardWidth = Math.min(width * 0.86, 380);
  const { settings, updateSettings } = useSettings();
  const [pull, setPull] = useState<PullState>({ phase: "idle" });

  const pullLatest = async () => {
    playSfx("select");
    setPull({ phase: "loading" });
    try {
      const api = createApi({ baseUrl: settings.backendUrl, apiKey: settings.apiKey });
      const registry = await api.fetchRegistry();
      const installed = await loadInstalled();
      const romDiff = diffSection(
        "rom",
        installed.roms,
        registry.roms.map((r) => ({ id: r.id, version: r.version, label: r.title })),
      );
      const modDiff = diffSection(
        "mod",
        installed.mods,
        registry.mods.map((m) => ({ id: m.id, version: m.version, label: m.name })),
      );
      const all = [...romDiff, ...modDiff];
      const changed = all.filter((e) => e.status !== "current");

      // Pulling installs the registry's current versions as the new baseline.
      await saveInstalled({
        roms: Object.fromEntries(registry.roms.map((r) => [r.id, r.version])),
        mods: Object.fromEntries(registry.mods.map((m) => [m.id, m.version])),
      });

      setPull({
        phase: "done",
        changed,
        total: all.length,
        newCount: changed.filter((e) => e.status === "new").length,
        updateCount: changed.filter((e) => e.status === "update").length,
      });
    } catch (e) {
      setPull({ phase: "error", message: e instanceof Error ? e.message : "Pull failed" });
    }
  };

  return (
    <Modal visible={open} transparent animationType="fade" onRequestClose={onClose}>
      {/* Backdrop — tapping outside the card closes the dialog. */}
      <Pressable style={styles.settingsBackdrop} onPress={onClose}>
        {/* Stop taps on the card itself from bubbling to the backdrop. */}
        <Pressable style={[styles.settingsCard, { width: cardWidth }]} onPress={() => {}}>
          <View style={styles.settingsCardHeader}>
            <Text selectable={false} style={styles.settingsTitle}>
              SETTINGS
            </Text>
            <Pressable
              onPress={onClose}
              hitSlop={10}
              style={({ pressed }) => [styles.settingsCloseBtn, pressed && { opacity: 0.6 }]}
            >
              <Text selectable={false} style={styles.settingsCloseBtnText}>
                ✕
              </Text>
            </Pressable>
          </View>

          <View style={styles.settingsField}>
            <Text selectable={false} style={styles.settingsFieldLabel}>
              BACKEND URL
            </Text>
            <TextInput
              value={settings.backendUrl}
              onChangeText={(t) => updateSettings({ backendUrl: t })}
              placeholder="http://localhost:4000"
              placeholderTextColor="#9a958a"
              autoCapitalize="none"
              autoCorrect={false}
              keyboardType="url"
              style={styles.settingsInput}
            />
          </View>

          <View style={styles.settingsField}>
            <Text selectable={false} style={styles.settingsFieldLabel}>
              BACKEND API KEY
            </Text>
            <TextInput
              value={settings.apiKey}
              onChangeText={(t) => updateSettings({ apiKey: t })}
              placeholder="••••••••"
              placeholderTextColor="#9a958a"
              autoCapitalize="none"
              autoCorrect={false}
              secureTextEntry
              style={styles.settingsInput}
            />
          </View>

          <View style={styles.settingsField}>
            <View style={styles.settingsToggleRow}>
              <View style={{ flex: 1 }}>
                <Text selectable={false} style={styles.settingsFieldLabel}>
                  TELEMETRY
                </Text>
                <Text selectable={false} style={styles.settingsToggleHint}>
                  Stream emulator vitals to the backend
                </Text>
              </View>
              <Switch
                value={settings.telemetry}
                onValueChange={(v) => updateSettings({ telemetry: v })}
                trackColor={{ false: "#aaa596", true: "#9a1f4c" }}
                thumbColor="#d8d4c6"
              />
            </View>
          </View>

          <View style={styles.settingsField}>
            <View style={styles.settingsToggleRow}>
              <View style={{ flex: 1 }}>
                <Text selectable={false} style={styles.settingsFieldLabel}>
                  DEV MODE
                </Text>
                <Text selectable={false} style={styles.settingsToggleHint}>
                  Allow remote button presses &amp; LCD screenshots
                </Text>
              </View>
              <Switch
                value={settings.devMode}
                onValueChange={(v) => updateSettings({ devMode: v })}
                trackColor={{ false: "#aaa596", true: "#9a1f4c" }}
                thumbColor="#d8d4c6"
              />
            </View>
          </View>

          <View style={styles.settingsSyncSection}>
            <Pressable
              onPress={pullLatest}
              disabled={pull.phase === "loading"}
              style={({ pressed }) => [
                styles.pullButton,
                pull.phase === "loading" && { opacity: 0.6 },
                pressed && { opacity: 0.75 },
              ]}
            >
              <Text selectable={false} style={styles.pullButtonText}>
                {pull.phase === "loading" ? "PULLING…" : "PULL LATEST"}
              </Text>
            </Pressable>
            <PullResult pull={pull} />
          </View>
        </Pressable>
      </Pressable>
    </Modal>
  );
}

// Status readout for the "pull latest" action: comparison summary plus a short
// list of what changed against the installed manifest.
function PullResult({ pull }: { pull: PullState }) {
  if (pull.phase === "idle") {
    return (
      <Text selectable={false} style={styles.pullHint}>
        Compares installed ROM &amp; mods against the backend registry.
      </Text>
    );
  }
  if (pull.phase === "loading") {
    return <Text selectable={false} style={styles.pullHint}>Checking registry…</Text>;
  }
  if (pull.phase === "error") {
    return (
      <Text selectable={false} style={styles.pullError}>
        {pull.message}
      </Text>
    );
  }

  if (pull.changed.length === 0) {
    return (
      <Text selectable={false} style={styles.pullOk}>
        ✓ Up to date — {pull.total} item{pull.total === 1 ? "" : "s"} installed.
      </Text>
    );
  }
  return (
    <View>
      <Text selectable={false} style={styles.pullOk}>
        ✓ Pulled {pull.newCount} new, {pull.updateCount} updated.
      </Text>
      <View style={styles.pullList}>
        {pull.changed.map((e) => (
          <View key={`${e.kind}:${e.id}`} style={styles.pullRow}>
            <Text
              selectable={false}
              style={[
                styles.pullBadge,
                e.status === "new" ? styles.pullBadgeNew : styles.pullBadgeUpdate,
              ]}
            >
              {e.status === "new" ? "NEW" : "UPD"}
            </Text>
            <Text selectable={false} style={styles.pullItemLabel} numberOfLines={1}>
              {e.label}
            </Text>
            <Text selectable={false} style={styles.pullItemVer}>
              {e.status === "update" ? `${e.from} → ${e.to}` : `v${e.to}`}
            </Text>
          </View>
        ))}
      </View>
    </View>
  );
}

// Per-mod configuration pop-up. Same full-screen Modal treatment as the
// settings dialog. Placeholder body for now — each mod's actual config
// controls get wired in here later.
function ModConfigModal({
  open,
  mod,
  onClose,
}: {
  open: boolean;
  mod: (typeof MODS)[number];
  onClose: () => void;
}) {
  const { width } = useWindowDimensions();
  const cardWidth = Math.min(width * 0.86, 380);
  return (
    <Modal visible={open} transparent animationType="fade" onRequestClose={onClose}>
      {/* Backdrop — tapping outside the card closes the dialog. */}
      <Pressable style={styles.settingsBackdrop} onPress={onClose}>
        {/* Stop taps on the card itself from bubbling to the backdrop. */}
        <Pressable style={[styles.settingsCard, { width: cardWidth }]} onPress={() => {}}>
          <View style={styles.settingsCardHeader}>
            <Text selectable={false} style={styles.settingsTitle}>
              {mod.name} CONFIG
            </Text>
            <Pressable
              onPress={onClose}
              hitSlop={10}
              style={({ pressed }) => [styles.settingsCloseBtn, pressed && { opacity: 0.6 }]}
            >
              <Text selectable={false} style={styles.settingsCloseBtnText}>
                ✕
              </Text>
            </Pressable>
          </View>
          <Text selectable={false} style={styles.modConfigPlaceholder}>
            No options for this mod yet.
          </Text>
        </Pressable>
      </Pressable>
    </Modal>
  );
}

function ModScroll({
  u,
  h,
  dir,
  onPress,
}: {
  u: Unit;
  h: number;
  dir: 1 | -1;
  onPress: (dir: 1 | -1) => void;
}) {
  return (
    <Pressable
      onPress={() => onPress(dir)}
      style={({ pressed }) => [
        styles.modScroll,
        { width: u(20), height: h, borderRadius: u(4) },
        pressed && { opacity: 0.6 },
      ]}
    >
      <Text selectable={false} style={[styles.modScrollText, { fontSize: h * 0.7 }]}>
        {dir === -1 ? "‹" : "›"}
      </Text>
    </Pressable>
  );
}

function ModChoice({
  u,
  h,
  label,
  active,
  onPress,
}: {
  u: Unit;
  h: number;
  label: string;
  active: boolean;
  onPress: () => void;
}) {
  return (
    <Pressable
      onPress={onPress}
      style={({ pressed }) => [
        styles.modChoice,
        { height: h, borderRadius: u(5) },
        active && styles.modChoiceOn,
        pressed && { opacity: 0.6 },
      ]}
    >
      <Text
        selectable={false}
        style={[styles.modChoiceText, { fontSize: u(8) }, active && styles.modChoiceTextOn]}
      >
        {label}
      </Text>
    </Pressable>
  );
}

/* ------------------------------------------------------------------ *
 * Shared pieces
 * ------------------------------------------------------------------ */

function Bezel({
  u,
  screenWidth,
  screenHeight,
  variant,
}: {
  u: Unit;
  screenWidth: number;
  screenHeight: number;
  variant: "portrait" | "landscape";
}) {
  const portrait = variant === "portrait";
  return (
    <View
      style={[
        styles.bezel,
        {
          width: portrait ? "100%" : undefined,
          borderRadius: u(12),
          borderBottomRightRadius: u(40),
          paddingTop: u(16),
          paddingBottom: portrait ? u(12) : u(14),
          paddingHorizontal: u(16),
        },
      ]}
    >
      {/* Twin pinstripes — the signature DMG mark */}
      <View style={[styles.stripes, { top: u(9), height: u(5), left: u(16), right: u(16) }]}>
        <View style={[styles.stripe, { backgroundColor: "#1c2a86", marginBottom: u(2) }]} />
        <View style={[styles.stripe, { backgroundColor: "#8a1f45" }]} />
      </View>

      {portrait ? (
        <View style={[styles.batteryRow, { left: u(14), top: u(26) }]}>
          <View style={[styles.led, { width: u(8), height: u(8), borderRadius: u(4) }]} />
          <Text style={[styles.batteryLabel, { fontSize: u(6), marginTop: u(3) }]}>BATTERY</Text>
        </View>
      ) : null}

      <Lcd u={u} width={screenWidth} height={screenHeight} />

      {portrait ? (
        <Text style={[styles.dotMatrix, { fontSize: u(8), marginTop: u(8), letterSpacing: u(1.2) }]}>
          DOT MATRIX WITH STEREO SOUND
        </Text>
      ) : null}
    </View>
  );
}

function Lcd({ u, width, height }: { u: Unit; width: number; height: number }) {
  const { uri, webViewRef, onMessage, settings, paused, mods } = useContext(EmulatorContext);
  const onLoad = useCallback(() => {
    // A fresh WebView needs the current host state immediately.
    webViewRef.current?.postMessage(JSON.stringify({ type: "input", buttons: 0, dpad: 0 }));
    webViewRef.current?.postMessage(JSON.stringify({ type: "settings", ...settings }));
    webViewRef.current?.postMessage(JSON.stringify({ type: "paused", value: paused }));
    webViewRef.current?.postMessage(JSON.stringify({ type: "mods", ids: mods }));
  }, [webViewRef, settings, paused, mods]);
  return (
    <View style={[styles.lcd, { width, height, borderRadius: u(4), borderWidth: u(1.5) }]}>
      {uri ? (
        <WebView
          ref={webViewRef}
          source={{ uri }}
          style={styles.emulator}
          pointerEvents="none"
          scrollEnabled={false}
          overScrollMode="never"
          javaScriptEnabled
          allowsInlineMediaPlayback
          mediaPlaybackRequiresUserAction={false}
          onMessage={onMessage}
          onLoad={onLoad}
          // Android/iOS can kill the WebView's content process under memory
          // pressure; reload and let onLoad replay the host state.
          onContentProcessDidTerminate={() => webViewRef.current?.reload()}
        />
      ) : (
        <View style={styles.lcdWell} pointerEvents="none" />
      )}
      <View
        style={[styles.lcdGlare, { top: -u(20), right: -u(28), width: u(120), height: u(64) }]}
        pointerEvents="none"
      />
    </View>
  );
}

function Wordmark({ u, style }: { u: Unit; style?: object }) {
  return (
    <View style={[styles.wordmark, style]}>
      <Text style={[styles.brandPoke, { fontSize: u(20) }]}>Poké</Text>
      <Text style={[styles.brandBoy, { fontSize: u(24), marginLeft: u(4) }]}>boy</Text>
    </View>
  );
}

function Dpad({ u, size }: { u: Unit; size: number }) {
  const arm = u(32);
  const off = (size - arm) / 2; // length of each arm beyond the center hub
  return (
    <View style={{ width: size, height: size }}>
      {/* Visual cross (non-interactive) */}
      <View
        pointerEvents="none"
        style={[styles.dpadBar, { width: arm, height: size, left: off, borderRadius: u(5) }]}
      />
      <View
        pointerEvents="none"
        style={[styles.dpadBar, { height: arm, width: size, top: off, borderRadius: u(5) }]}
      />
      <View
        pointerEvents="none"
        style={[
          styles.dpadHub,
          { width: u(22), height: u(22), borderRadius: u(11), top: size / 2 - u(11), left: size / 2 - u(11) },
        ]}
      />
      {/* Directional press zones */}
      <DpadZone mask={0x04} style={{ left: off, top: 0, width: arm, height: off, borderTopLeftRadius: u(5), borderTopRightRadius: u(5) }} />
      <DpadZone mask={0x08} style={{ left: off, top: size - off, width: arm, height: off, borderBottomLeftRadius: u(5), borderBottomRightRadius: u(5) }} />
      <DpadZone mask={0x02} style={{ top: off, left: 0, height: arm, width: off, borderTopLeftRadius: u(5), borderBottomLeftRadius: u(5) }} />
      <DpadZone mask={0x01} style={{ top: off, left: size - off, height: arm, width: off, borderTopRightRadius: u(5), borderBottomRightRadius: u(5) }} />
    </View>
  );
}

function DpadZone({ style, mask }: { style: object; mask: number }) {
  const { setInput } = useContext(EmulatorContext);
  return (
    <Pressable
      onPressIn={() => { setInput("dpad", mask, true); playSfx("dpad"); }}
      onPressOut={() => setInput("dpad", mask, false)}
      style={({ pressed }) => [{ position: "absolute" }, style, pressed && styles.dpadZonePressed]}
    />
  );
}

function FaceButtons({ u, w, h }: { u: Unit; w?: number; h?: number }) {
  const bw = w ?? u(52);
  const bh = h ?? u(52);
  const fontSize = bh * 0.46;
  return (
    <View style={styles.abGroup}>
      <FaceButton label="B" w={bw} h={bh} fontSize={fontSize} />
      <View style={{ marginLeft: u(20), marginBottom: u(30) }}>
        <FaceButton label="A" w={bw} h={bh} fontSize={fontSize} />
      </View>
    </View>
  );
}

function FaceButton({
  label,
  w,
  h,
  fontSize,
}: {
  label: "A" | "B";
  w: number;
  h: number;
  fontSize: number;
}) {
  const { setInput } = useContext(EmulatorContext);
  const mask = label === "A" ? 0x01 : 0x02;
  return (
    <Pressable
      onPressIn={() => { setInput("buttons", mask, true); playSfx(label === "A" ? "a" : "b"); }}
      onPressOut={() => setInput("buttons", mask, false)}
      style={({ pressed }) => [
        styles.faceButton,
        { width: w, height: h, borderRadius: h / 2, alignItems: "center", justifyContent: "center" },
        pressed && styles.faceButtonPressed,
      ]}
    >
      {/* Counter-rotate the label so it stays upright despite the tilted group */}
      <Text selectable={false} style={[styles.faceLabelOn, { fontSize, transform: [{ rotate: "25deg" }] }]}>
        {label}
      </Text>
    </Pressable>
  );
}

function PillRow({ u, gap }: { u: Unit; gap?: number }) {
  return (
    <View style={[styles.pillRow, { gap: gap ?? u(24) }]}>
      <Pill u={u} label="SELECT" />
      <Pill u={u} label="START" />
    </View>
  );
}

function Pill({ u, label }: { u: Unit; label: string }) {
  const { setInput } = useContext(EmulatorContext);
  const mask = label === "START" ? 0x08 : 0x04;
  return (
    <Pressable
      onPressIn={() => {
        setInput("buttons", mask, true);
        playSfx(label === "START" ? "start" : "select");
      }}
      onPressOut={() => setInput("buttons", mask, false)}
      style={({ pressed }) => [styles.pillGroup, pressed && { opacity: 0.55 }]}
    >
      <View
        style={[
          styles.pill,
          { width: u(40), height: u(12), borderRadius: u(6), transform: [{ rotate: "-25deg" }] },
        ]}
      />
      <Text style={[styles.pillLabel, { fontSize: u(8), marginTop: u(6) }]}>{label}</Text>
    </Pressable>
  );
}

function MiniControls({
  u,
  volume,
  muted,
  speed,
  onVolume,
  onMute,
  onCycleSpeed,
  width,
}: {
  u: Unit;
  volume: number;
  muted: boolean;
  speed: (typeof SPEEDS)[number];
  onVolume: (value: number) => void;
  onMute: () => void;
  onCycleSpeed: () => void;
  width?: number;
}) {
  const controlWidth = Math.min(width ?? u(214), u(214));
  const volumeWidth = u(86);

  return (
    <View style={[styles.miniControls, { marginTop: u(7), width: controlWidth, height: u(20) }]}>
      <View style={[styles.miniControlSide, { width: volumeWidth }]}>
        <Pressable
          onPress={onMute}
          style={({ pressed }) => [
            styles.muteButton,
            { width: u(19), height: u(15), borderRadius: u(4) },
            muted && styles.muteButtonOn,
            pressed && { opacity: 0.62 },
          ]}
        >
          <Text
            selectable={false}
            style={[styles.miniLabel, styles.muteText, muted && styles.muteTextOn, { fontSize: u(6) }]}
          >
            M
          </Text>
        </Pressable>
        <VolumeSlider
          u={u}
          width={u(56)}
          value={muted ? 0 : volume}
          onChange={onVolume}
          disabled={muted}
        />
      </View>
      <View style={[styles.miniDivider, { height: u(13), marginHorizontal: u(7) }]} />
      <Pressable
        onPress={onCycleSpeed}
        style={({ pressed }) => [
          styles.speedButton,
          { width: u(48), height: u(16), borderRadius: u(8) },
          pressed && { opacity: 0.62 },
        ]}
      >
        <Text selectable={false} style={[styles.miniLabel, styles.speedText, { fontSize: u(7) }]}>
          {speed}
        </Text>
      </Pressable>
    </View>
  );
}

function VolumeSlider({
  u,
  width,
  value,
  onChange,
  disabled,
}: {
  u: Unit;
  width: number;
  value: number;
  onChange: (value: number) => void;
  disabled?: boolean;
}) {
  const trackWidth = useRef(1);
  const setFromX = (x: number) => {
    if (!disabled) onChange(Math.max(0, Math.min(1, x / trackWidth.current)));
  };
  const pan = useRef(
    PanResponder.create({
      onStartShouldSetPanResponder: () => true,
      onMoveShouldSetPanResponder: () => true,
      onPanResponderGrant: (e) => setFromX(e.nativeEvent.locationX),
      onPanResponderMove: (e) => setFromX(e.nativeEvent.locationX),
    }),
  ).current;

  return (
    <View
      {...pan.panHandlers}
      onLayout={(e) => {
        trackWidth.current = Math.max(1, e.nativeEvent.layout.width);
      }}
      style={[styles.volumeTrack, { width, height: u(4), borderRadius: u(2) }]}
    >
      <View style={[styles.volumeFill, { width: `${value * 100}%`, borderRadius: u(2) }]} />
      <View
        pointerEvents="none"
        style={[
          styles.volumeThumb,
          {
            width: u(8),
            height: u(8),
            borderRadius: u(4),
            left: `${value * 100}%`,
            marginLeft: -u(4),
            top: -u(2),
          },
        ]}
      />
    </View>
  );
}

/* ------------------------------------------------------------------ *
 * Styles
 * ------------------------------------------------------------------ */

const styles = StyleSheet.create({
  page: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    paddingTop: PAD_TOP,
    paddingBottom: PAD_BOTTOM,
    backgroundColor: "#26262f",
  },

  body: {
    alignItems: "center",
    backgroundColor: "#c8c3b4",
    borderColor: "#b0ab9c",
    borderWidth: 2,
    borderTopColor: "#dcd8cc",
    borderLeftColor: "#d2cec1",
    borderRightColor: "#aaa596",
    borderBottomColor: "#a09b8c",
    shadowColor: "#5c5647",
    shadowOffset: { width: 0, height: 18 },
    shadowOpacity: 0.3,
    shadowRadius: 30,
    zIndex: 1,
  },
  bodyLandscape: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },

  landZone: {
    alignSelf: "stretch",
    alignItems: "center",
    justifyContent: "center",
  },

  portraitTopRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 8,
  },
  miniControls: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
  },
  miniControlSide: {
    flexDirection: "row",
    alignItems: "center",
  },
  miniLabel: {
    color: "#4c4a55",
    fontWeight: "800",
    letterSpacing: 0.5,
    userSelect: "none",
  },
  miniDivider: {
    width: 1,
    backgroundColor: "#aaa596",
  },
  volumeTrack: {
    marginLeft: 5,
    backgroundColor: "#aaa596",
  },
  volumeFill: {
    height: "100%",
    backgroundColor: "#4c4a55",
  },
  volumeThumb: {
    position: "absolute",
    backgroundColor: "#9a1f4c",
  },
  muteButton: {
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "#c8c3b4",
    borderWidth: 1,
    borderColor: "#aaa596",
  },
  muteButtonOn: {
    backgroundColor: "#454550",
    borderColor: "#33333c",
  },
  muteText: {
    color: "#4c4a55",
  },
  muteTextOn: {
    color: "#d6d1c2",
  },
  speedButton: {
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "#d8d4c6",
    borderWidth: 1,
    borderColor: "#aaa596",
  },
  speedText: {
    color: "#31316f",
  },

  cartSlot: {
    backgroundColor: "#2a2a2e",
  },
  cartBody: {
    alignItems: "center",
    backgroundColor: "#454550",
    borderColor: "#33333c",
    shadowColor: "#000",
    shadowOffset: { width: 0, height: 6 },
    shadowOpacity: 0.25,
    shadowRadius: 10,
  },
  cartRidges: {
    width: "34%",
  },
  cartRidge: {
    width: "100%",
    backgroundColor: "#5c5c67",
  },
  cartLabel: {
    backgroundColor: "#d6d1c2",
  },
  cartImageReserve: {
    flex: 1,
    backgroundColor: "#c5bfab",
    borderWidth: 1,
    borderColor: "#aaa38d",
  },
  cartBottom: {
    width: "78%",
    flexDirection: "row",
    justifyContent: "space-around",
    backgroundColor: "#2f2f37",
    paddingHorizontal: 4,
    paddingVertical: 2,
  },
  cartContact: {
    height: "100%",
    backgroundColor: "#b7aa75",
  },
  cartArrow: {
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "#d8d4c6",
    borderWidth: 1,
    borderColor: "#a59f8d",
    shadowColor: "#5c5647",
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.18,
    shadowRadius: 3,
  },
  cartArrowText: {
    color: "#4c4a55",
    fontWeight: "800",
    userSelect: "none",
  },

  modPanel: {
    backgroundColor: "#d8d4c6",
    borderWidth: 1,
    borderColor: "#a59f8d",
    shadowColor: "#5c5647",
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.2,
    shadowRadius: 6,
  },
  modNameRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  modName: {
    color: "#4c4a55",
    fontWeight: "800",
    userSelect: "none",
  },
  modDesc: {
    color: "#6b665a",
    fontWeight: "600",
    textAlign: "center",
    userSelect: "none",
  },
  modScroll: {
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "#c8c3b4",
    borderWidth: 1,
    borderColor: "#aaa596",
  },
  modScrollText: {
    color: "#4c4a55",
    fontWeight: "800",
    userSelect: "none",
  },
  modChoice: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "#c8c3b4",
    borderWidth: 1,
    borderColor: "#aaa596",
  },
  modChoiceOn: {
    backgroundColor: "#454550",
    borderColor: "#33333c",
  },
  modChoiceText: {
    color: "#4c4a55",
    fontWeight: "800",
    letterSpacing: 1,
    userSelect: "none",
  },
  modChoiceTextOn: {
    color: "#d6d1c2",
  },
  modCountRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  modCountValue: {
    color: "#9a1f4c",
    fontWeight: "800",
    userSelect: "none",
  },
  settingsButton: {
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "#c8c3b4",
    borderWidth: 1,
    borderColor: "#aaa596",
  },
  settingsButtonOn: {
    backgroundColor: "#454550",
    borderColor: "#33333c",
  },
  settingsButtonText: {
    color: "#4c4a55",
  },
  settingsButtonTextOn: {
    color: "#d6d1c2",
  },
  modConfigPlaceholder: {
    color: "#6b665a",
    fontWeight: "600",
    userSelect: "none",
  },
  settingsBackdrop: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 20,
    backgroundColor: "rgba(20, 20, 26, 0.6)",
  },
  settingsCard: {
    backgroundColor: "#d8d4c6",
    borderRadius: 16,
    borderWidth: 2,
    borderColor: "#a59f8d",
    padding: 20,
    shadowColor: "#000",
    shadowOffset: { width: 0, height: 12 },
    shadowOpacity: 0.35,
    shadowRadius: 24,
  },
  settingsCardHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginBottom: 18,
  },
  settingsTitle: {
    color: "#31316f",
    fontSize: 18,
    fontWeight: "800",
    fontStyle: "italic",
    letterSpacing: 1,
    userSelect: "none",
  },
  settingsCloseBtn: {
    width: 30,
    height: 30,
    borderRadius: 8,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "#c8c3b4",
    borderWidth: 1,
    borderColor: "#aaa596",
  },
  settingsCloseBtnText: {
    color: "#4c4a55",
    fontSize: 14,
    fontWeight: "800",
    userSelect: "none",
  },
  settingsField: {
    marginBottom: 14,
  },
  settingsFieldLabel: {
    color: "#6b665a",
    fontSize: 11,
    fontWeight: "800",
    letterSpacing: 1,
    marginBottom: 6,
    userSelect: "none",
  },
  settingsInput: {
    height: 42,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#aaa596",
    backgroundColor: "#efece3",
    paddingHorizontal: 12,
    fontSize: 14,
    color: "#33333c",
  },
  settingsToggleRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
  },
  settingsToggleHint: {
    marginTop: 2,
    color: "#8a8577",
    fontSize: 11,
    lineHeight: 15,
    userSelect: "none",
  },
  settingsSyncSection: {
    marginTop: 6,
    paddingTop: 16,
    borderTopWidth: 1,
    borderTopColor: "#c0bbac",
  },
  pullButton: {
    height: 44,
    borderRadius: 8,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "#9a1f4c",
    borderWidth: 1,
    borderColor: "#6f153a",
  },
  pullButtonText: {
    color: "#f2d9e2",
    fontSize: 14,
    fontWeight: "800",
    letterSpacing: 1.5,
    fontStyle: "italic",
    userSelect: "none",
  },
  pullHint: {
    marginTop: 10,
    color: "#6b665a",
    fontSize: 11,
    lineHeight: 15,
    userSelect: "none",
  },
  pullError: {
    marginTop: 10,
    color: "#a3341f",
    fontSize: 12,
    fontWeight: "700",
    userSelect: "none",
  },
  pullOk: {
    marginTop: 10,
    color: "#2f6b3a",
    fontSize: 12,
    fontWeight: "800",
    userSelect: "none",
  },
  pullList: {
    marginTop: 8,
    gap: 5,
  },
  pullRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
  },
  pullBadge: {
    fontSize: 9,
    fontWeight: "800",
    letterSpacing: 0.5,
    color: "#fff",
    paddingHorizontal: 5,
    paddingVertical: 2,
    borderRadius: 4,
    overflow: "hidden",
    userSelect: "none",
  },
  pullBadgeNew: {
    backgroundColor: "#2f6b3a",
  },
  pullBadgeUpdate: {
    backgroundColor: "#b5761c",
  },
  pullItemLabel: {
    flex: 1,
    color: "#4c4a55",
    fontSize: 12,
    fontWeight: "600",
    userSelect: "none",
  },
  pullItemVer: {
    color: "#6b665a",
    fontSize: 10,
    fontVariant: ["tabular-nums"],
    userSelect: "none",
  },

  bezel: {
    alignItems: "center",
    backgroundColor: "#4c4a55",
    shadowColor: "#000",
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.25,
    shadowRadius: 6,
  },
  stripes: {
    position: "absolute",
  },
  stripe: {
    height: 2,
    borderRadius: 1,
  },
  batteryRow: {
    position: "absolute",
    alignItems: "center",
  },
  led: {
    backgroundColor: "#d64b3a",
    shadowColor: "#ff6a52",
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 0.8,
    shadowRadius: 4,
  },
  batteryLabel: {
    color: "#c4c2cc",
    fontWeight: "700",
    letterSpacing: 1,
  },

  lcd: {
    overflow: "hidden",
    backgroundColor: "#8fa027",
    borderColor: "#2b2d1a",
  },
  emulator: {
    flex: 1,
    backgroundColor: "#c8d4a4",
  },
  lcdWell: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: "#9cad3a",
    borderColor: "rgba(20, 26, 8, 0.28)",
    borderWidth: 1,
  },
  lcdGlare: {
    position: "absolute",
    backgroundColor: "rgba(255, 255, 255, 0.14)",
    transform: [{ rotate: "-20deg" }],
    borderRadius: 999,
  },

  dotMatrix: {
    color: "#b7b5c1",
    fontWeight: "600",
  },

  wordmark: {
    flexDirection: "row",
    alignItems: "baseline",
  },
  brandPoke: {
    color: "#31316f",
    fontStyle: "italic",
    fontWeight: "800",
  },
  brandBoy: {
    color: "#9a1f4c",
    fontWeight: "800",
    fontStyle: "italic",
    letterSpacing: 0.5,
  },

  dpadBar: {
    position: "absolute",
    backgroundColor: "#2c2c30",
  },
  dpadHub: {
    position: "absolute",
    backgroundColor: "#242428",
  },
  dpadZonePressed: {
    backgroundColor: "rgba(255, 255, 255, 0.12)",
  },
  abGroup: {
    flexDirection: "row",
    alignItems: "flex-end",
    transform: [{ rotate: "-25deg" }],
  },
  faceButton: {
    backgroundColor: "#9a1f4c",
    borderWidth: 2,
    borderColor: "#6f153a",
    shadowColor: "#000",
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.3,
    shadowRadius: 3,
  },
  faceButtonPressed: {
    backgroundColor: "#7c1740",
    transform: [{ scale: 0.92 }],
  },
  faceLabelOn: {
    color: "#f2d9e2",
    fontWeight: "800",
    fontStyle: "italic",
    userSelect: "none",
  },

  pillRow: {
    flexDirection: "row",
    justifyContent: "center",
  },
  pillGroup: {
    alignItems: "center",
  },
  pill: {
    backgroundColor: "#8a8577",
    borderWidth: 1,
    borderColor: "#6f6b5e",
  },
  pillLabel: {
    color: "#31316f",
    fontWeight: "800",
    fontStyle: "italic",
    letterSpacing: 0.5,
  },
});
