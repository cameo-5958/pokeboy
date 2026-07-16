import { Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";
import type { EmulatorAssets } from "@/emulator/assets";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { useCallback, useMemo, useRef, useState } from "react";
import {
  Animated,
  PanResponder,
  Text,
  View,
  type LayoutChangeEvent,
  useWindowDimensions,
} from "react-native";
import WebView from "react-native-webview";

import { createApi, type Cartridge as CartridgeInfo, type RegistryMod } from "@/api/client";
import { playSfx } from "@/sfx";
import { useSettings } from "@/settings";

import {
  BATTLE_LINK_ENDPOINT_KEY,
  BATTLE_LINK_MAX_WAIT_KEY,
  BATTLE_LINK_MODE_KEY,
  DEFAULT_BATTLE_LINK_ENDPOINT,
  DEFAULT_BATTLE_LINK_MAX_WAIT_S,
  ENABLED_MODS_KEY,
  PAD_BOTTOM,
  PAD_TOP,
  PULL_DIST,
  SPEEDS,
} from "./constants";
import {
  Cartridge,
  CartridgeArrow,
  LandscapeConsole,
  PortraitConsole,
} from "./ConsoleShell";
import { EmulatorContext } from "./EmulatorContext";
import { cartMetrics, modMetrics } from "./metrics";
import { ModChanger } from "./ModChanger";
import { useRuntimeEffects } from "./runtimeEffects";
import { useCatalogEffects, useLabelEffect, useStartupEffects } from "./startupEffects";
import { styles } from "./styles";
import type { BattleLinkMode, BootError, TelemetryEvent, Unit } from "./types";
import { useEmulatorBridge } from "./useEmulatorBridge";
import { genId } from "./utils";

export default function EmulatorScreen({ appBuildVersion }: { appBuildVersion: string }) {
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
  const [battleLinkEndpoint, setBattleLinkEndpoint] = useState(DEFAULT_BATTLE_LINK_ENDPOINT);
  const [battleLinkMaxWaitS, setBattleLinkMaxWaitS] = useState(DEFAULT_BATTLE_LINK_MAX_WAIT_S);
  const [battleLinkMode, setBattleLinkMode] = useState<BattleLinkMode>("get");
  const [cartridges, setCartridges] = useState<CartridgeInfo[]>([]);
  const [mods, setMods] = useState<readonly RegistryMod[]>([]);
  const [cartridgeIdx, setCartridgeIdx] = useState(0);
  const [labelImages, setLabelImages] = useState<Record<string, string>>({});
  const requestedLabelUrlsRef = useRef(new Set<string>());
  const refreshedLabelUrlsRef = useRef(new Set<string>());
  const mountedRef = useRef(true);
  const webViewRef = useRef<WebView | null>(null);
  const [emulatorAssets, setEmulatorAssets] = useState<EmulatorAssets | null>(null);
  const [emulatorAssetError, setEmulatorAssetError] = useState<BootError | null>(null);
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
  const emulatorAssetErrorReportedRef = useRef(false);

  useStartupEffects({
    mountedRef,
    setEmulatorAssets,
    setEmulatorAssetError,
    settingsLoaded,
    telemetry: settings.telemetry,
    emulatorAssetError,
    emulatorAssetErrorReportedRef,
    telemetryEventsRef,
    deviceIdRef,
    setBattleLinkEndpoint,
    setBattleLinkMaxWaitS,
    setBattleLinkMode,
    setEnabledMods,
  });

  const saveBattleLinkEndpoint = useCallback((value: string) => {
    const endpoint = value.trim();
    setBattleLinkEndpoint(endpoint);
    AsyncStorage.setItem(BATTLE_LINK_ENDPOINT_KEY, endpoint).catch(() => {});
  }, []);
  const saveBattleLinkMaxWait = useCallback((seconds: number) => {
    setBattleLinkMaxWaitS(seconds);
    AsyncStorage.setItem(BATTLE_LINK_MAX_WAIT_KEY, String(seconds)).catch(() => {});
  }, []);
  const saveBattleLinkMode = useCallback((mode: BattleLinkMode) => {
    setBattleLinkMode(mode);
    AsyncStorage.setItem(BATTLE_LINK_MODE_KEY, mode).catch(() => {});
  }, []);
  const emulatorSettings = useMemo(
    () => ({
      speed: (SPEEDS[speedIdx] === "xINF" ? "inf" : Number(SPEEDS[speedIdx].slice(1))) as number | "inf",
      muted,
      volume,
      telemetry: settings.telemetry,
      battleLinkEndpoint,
      battleLinkMode,
      battleLinkMaxTimeTillRandomMs: Math.round(battleLinkMaxWaitS * 1000),
    }),
    [speedIdx, muted, volume, settings.telemetry, battleLinkEndpoint, battleLinkMode, battleLinkMaxWaitS],
  );

  // The list is local-first, so a launch while the backend is unreachable
  // (e.g. VPN not up yet) resolves from the offline cache — which can include
  // cartridges since removed server-side. Keep retrying, and re-check when the
  // app foregrounds, until a fresh response replaces any stale entries.
  useCatalogEffects({
    api,
    settingsLoaded,
    setCartridges,
    setCartridgeIdx,
    setMods,
    setEnabledMods,
  });

  useLabelEffect({
    cartridges,
    requestedLabelUrlsRef,
    refreshedLabelUrlsRef,
    mountedRef,
    setLabelImages,
  });

  const { devInputRef, onMessage, postToEmulator, sendMergedInput, setInput } = useEmulatorBridge({
    webViewRef,
    inputRef,
    telemetry: settings.telemetry,
    telemetryEventsRef,
    telemetrySnapshotsRef,
    api,
  });

  useRuntimeEffects({
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
    telemetry: settings.telemetry,
    telemetrySnapshotsRef,
    telemetryEventsRef,
    api,
    deviceIdRef,
    sessionIdRef,
    cartridgeIdRef,
    devMode: settings.devMode,
    webViewRef,
    lcdDead,
  });

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

  // Enabled mods, by id. Persisted so toggles survive app relaunches.
  const setMod = (id: string, on: boolean) => {
    if (enabledMods.has(id) === on) return;
    playSfx(on ? "modOn" : "modOff");
    setEnabledMods((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      AsyncStorage.setItem(ENABLED_MODS_KEY, JSON.stringify(Array.from(next))).catch(() => {});
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

  const emulator = useMemo(
    () => {
      if (!cartridge || !emulatorAssets) return null;
      const config = {
        backendUrl: api.baseUrl,
        apiKey: settings.apiKey,
        cartridgeId: cartridge.id,
        cartridgeVersion: cartridge.version ?? "0",
        gbcoreUri: emulatorAssets.gbcoreUri,
        wasmUri: emulatorAssets.wasmUri,
        wasmBase64: emulatorAssets.wasmBase64,
        modCoreUri: emulatorAssets.modCoreUri,
        battleLinkEndpoint,
        battleLinkMode,
        battleLinkMaxTimeTillRandomMs: Math.round(battleLinkMaxWaitS * 1000),
      };
      return {
        uri: emulatorAssets.documentUri,
        injected: `window.PokeboyRuntime=${JSON.stringify(config)};true;`,
        readAccessUri: emulatorAssets.readAccessUri,
        key: `${cartridge.id}@${cartridge.version ?? "0"}`,
      };
    },
    [api.baseUrl, battleLinkEndpoint, battleLinkMode, battleLinkMaxWaitS, cartridge, emulatorAssets, settings.apiKey],
  );
  const modsList = useMemo(() => Array.from(enabledMods), [enabledMods]);
  const emulatorContextValue = useMemo(
    () => ({
      emulator: lcdDead ? null : emulator,
      webViewRef,
      onMessage,
      setInput,
      settings: emulatorSettings,
      paused: ejected,
      mods: modsList,
    }),
    [emulator, lcdDead, webViewRef, onMessage, setInput, emulatorSettings, ejected, modsList],
  );

  return (
    <EmulatorContext.Provider value={emulatorContextValue}>
    <View style={styles.page}>
      <Stack.Screen options={{ headerShown: false }} />
      <StatusBar hidden />
      {emulatorAssetError ? (
        <View style={styles.bootError} pointerEvents="none">
          <Text style={styles.bootErrorText}>
            EMULATOR START FAILED{"\n"}{emulatorAssetError.message}
          </Text>
        </View>
      ) : null}
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
            mods={mods}
            enabled={enabledMods}
            onSet={setMod}
            battleLinkEndpoint={battleLinkEndpoint}
            onBattleLinkEndpoint={saveBattleLinkEndpoint}
            battleLinkMaxWaitS={battleLinkMaxWaitS}
            onBattleLinkMaxWait={saveBattleLinkMaxWait}
            battleLinkMode={battleLinkMode}
            onBattleLinkMode={saveBattleLinkMode}
            appBuildVersion={appBuildVersion}
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
