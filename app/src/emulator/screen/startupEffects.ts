/* eslint-disable react-hooks/exhaustive-deps -- preserve the original effect dependency arrays */
import AsyncStorage from "@react-native-async-storage/async-storage";
import * as SecureStore from "expo-secure-store";
import { useEffect, useMemo, type Dispatch, type MutableRefObject, type SetStateAction } from "react";
import { AppState } from "react-native";

import { createApi, type Cartridge as CartridgeInfo, type RegistryMod } from "@/api/client";
import type { EmulatorAssets } from "@/emulator/assets";
import { loadEmulatorAssets } from "@/emulator/assets";

import {
  BATTLE_LINK_DISCORD_TOKEN_KEY,
  BATTLE_LINK_ENDPOINT_KEY,
  BATTLE_LINK_MAX_WAIT_KEY,
  BATTLE_LINK_MODE_KEY,
  CARTRIDGE_RETRY_MS,
  DEVICE_ID_KEY,
  ENABLED_MODS_KEY,
  LABEL_CACHE_PREFIX,
  MOD_REGISTRY_RETRY_MS,
} from "./constants";
import type { BattleLinkMode, BootError, TelemetryEvent } from "./types";
import { blobToDataUri, genId } from "./utils";

export function useStartupEffects(options: {
  mountedRef: MutableRefObject<boolean>;
  setEmulatorAssets: Dispatch<SetStateAction<EmulatorAssets | null>>;
  setEmulatorAssetError: Dispatch<SetStateAction<BootError | null>>;
  settingsLoaded: boolean;
  telemetry: boolean;
  emulatorAssetError: BootError | null;
  emulatorAssetErrorReportedRef: MutableRefObject<boolean>;
  telemetryEventsRef: MutableRefObject<TelemetryEvent[]>;
  deviceIdRef: MutableRefObject<string | null>;
  setBattleLinkEndpoint: Dispatch<SetStateAction<string>>;
  setBattleLinkMaxWaitS: Dispatch<SetStateAction<number>>;
  setBattleLinkMode: Dispatch<SetStateAction<BattleLinkMode>>;
  setEnabledMods: Dispatch<SetStateAction<ReadonlySet<string>>>;
}) {
  const {
    mountedRef,
    setEmulatorAssets,
    setEmulatorAssetError,
    settingsLoaded,
    telemetry,
    emulatorAssetError,
    emulatorAssetErrorReportedRef,
    telemetryEventsRef,
    deviceIdRef,
    setBattleLinkEndpoint,
    setBattleLinkMaxWaitS,
    setBattleLinkMode,
    setEnabledMods,
  } = options;

  useEffect(() => {
    mountedRef.current = true;
    loadEmulatorAssets().then((assets) => {
      if (mountedRef.current) setEmulatorAssets(assets);
    }).catch((error) => {
      console.error("Unable to load bundled emulator assets", error);
      if (!mountedRef.current) return;
      setEmulatorAssetError({
        message: error instanceof Error ? error.message : String(error),
        name: error instanceof Error ? error.name : "Error",
        stack: error instanceof Error ? error.stack ?? null : null,
      });
    });
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    if (
      !settingsLoaded ||
      !telemetry ||
      !emulatorAssetError ||
      emulatorAssetErrorReportedRef.current
    ) return;
    emulatorAssetErrorReportedRef.current = true;
    telemetryEventsRef.current.push({
      t: Date.now(),
      kind: "emulator-assets-error",
      detail: emulatorAssetError,
    });
  }, [emulatorAssetError, telemetry, settingsLoaded]);

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

  useEffect(() => {
    let alive = true;
    AsyncStorage.getItem(BATTLE_LINK_ENDPOINT_KEY)
      .then((value) => { if (alive && value) setBattleLinkEndpoint(value); })
      .catch(() => {});
    AsyncStorage.getItem(BATTLE_LINK_MAX_WAIT_KEY)
      .then((value) => {
        const seconds = Number(value);
        if (alive && value !== null && Number.isFinite(seconds) && seconds >= 0) {
          setBattleLinkMaxWaitS(seconds);
        }
      })
      .catch(() => {});
    AsyncStorage.getItem(BATTLE_LINK_MODE_KEY)
      .then((value) => {
        if (alive && (value === "get" || value === "discord")) setBattleLinkMode(value);
      })
      .catch(() => {});
    SecureStore.deleteItemAsync(BATTLE_LINK_DISCORD_TOKEN_KEY).catch(() => {});
    AsyncStorage.getItem(ENABLED_MODS_KEY)
      .then((value) => {
        if (!alive || !value) return;
        const ids: unknown = JSON.parse(value);
        if (Array.isArray(ids)) {
          setEnabledMods(new Set(ids.filter((id): id is string => typeof id === "string")));
        }
      })
      .catch(() => {});
    return () => { alive = false; };
  }, []);
}

export function useCatalogEffects(options: {
  api: ReturnType<typeof createApi>;
  settingsLoaded: boolean;
  setCartridges: Dispatch<SetStateAction<CartridgeInfo[]>>;
  setCartridgeIdx: Dispatch<SetStateAction<number>>;
  setMods: Dispatch<SetStateAction<readonly RegistryMod[]>>;
  setEnabledMods: Dispatch<SetStateAction<ReadonlySet<string>>>;
}) {
  const { api, settingsLoaded, setCartridges, setCartridgeIdx, setMods, setEnabledMods } = options;
  // The list is local-first, so a launch while the backend is unreachable
  // (e.g. VPN not up yet) resolves from the offline cache - which can include
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

  // The accessory bay must reflect the backend catalog, rather than a list
  // compiled into the app. This also lets a registry update add a mod without
  // requiring a matching IPA change.
  useEffect(() => {
    if (!settingsLoaded) return;
    let alive = true;
    let retry: ReturnType<typeof setTimeout> | null = null;
    const load = async () => {
      try {
        const { data, fresh } = await api.fetchRegistryWithMeta();
        if (!alive) return;
        setMods(data.mods);
        const available = new Set(data.mods.map((mod) => mod.id));
        setEnabledMods((previous) => {
          const next = new Set([...previous].filter((id) => available.has(id)));
          return next.size === previous.size ? previous : next;
        });
        if (!fresh) retry = setTimeout(load, MOD_REGISTRY_RETRY_MS);
      } catch {
        if (!alive) return;
        setMods([]);
        retry = setTimeout(load, MOD_REGISTRY_RETRY_MS);
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
}

export function useLabelEffect(options: {
  cartridges: CartridgeInfo[];
  requestedLabelUrlsRef: MutableRefObject<Set<string>>;
  refreshedLabelUrlsRef: MutableRefObject<Set<string>>;
  mountedRef: MutableRefObject<boolean>;
  setLabelImages: Dispatch<SetStateAction<Record<string, string>>>;
}) {
  const { cartridges, requestedLabelUrlsRef, refreshedLabelUrlsRef, mountedRef, setLabelImages } = options;
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
          if (!cached || !mountedRef.current || refreshedLabelUrlsRef.current.has(url)) return;
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
}
