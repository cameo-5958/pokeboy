/**
 * Locally-persisted app state, backed by AsyncStorage and shared app-wide
 * through a React context so every screen reads the same runtime backend
 * connection.
 *
 * - `Settings` - the backend connection (URL + API key) the user enters in the
 *   settings dialog.
 * - `InstalledManifest` - what this device has pulled from the backend, keyed
 *   by id → version. "Pull latest" diffs this against the live registry.
 */

import AsyncStorage from "@react-native-async-storage/async-storage";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

export type Settings = {
  backendUrl: string;
  apiKey: string;
  /** Opt-in: stream emulator vitals (fps, audio, heap) to the backend. */
  telemetry: boolean;
  /** Opt-in: poll the backend for remote-control commands (button presses,
   * LCD screenshots) issued through the dev MCP server. */
  devMode: boolean;
};

/** id → installed version, for ROMs and mods respectively. */
export type InstalledManifest = {
  roms: Record<string, string>;
  mods: Record<string, string>;
};

const SETTINGS_KEY = "pokeboy.settings.v1";
const MANIFEST_KEY = "pokeboy.installed.v1";

const DEFAULT_SETTINGS: Settings = {
  backendUrl: process.env.EXPO_PUBLIC_API_URL ?? "",
  apiKey: "",
  telemetry: false,
  devMode: false,
};

export const EMPTY_MANIFEST: InstalledManifest = { roms: {}, mods: {} };

type SettingsContextValue = {
  settings: Settings;
  updateSettings: (patch: Partial<Settings>) => void;
  /** False until the persisted read resolves - gate network calls on this. */
  settingsLoaded: boolean;
};

const SettingsContext = createContext<SettingsContextValue | null>(null);

/**
 * Loads persisted settings once and persists every update. Wrap the app in this
 * so all screens share one settings source.
 */
export function SettingsProvider({ children }: { children: ReactNode }) {
  const [settings, setSettings] = useState<Settings>(DEFAULT_SETTINGS);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let alive = true;
    AsyncStorage.getItem(SETTINGS_KEY)
      .then((raw) => {
        if (!alive) return;
        if (raw) {
          try {
            setSettings({ ...DEFAULT_SETTINGS, ...(JSON.parse(raw) as Partial<Settings>) });
          } catch {
            // Corrupt payload - fall back to defaults.
          }
        }
        setLoaded(true);
      })
      .catch(() => alive && setLoaded(true));
    return () => {
      alive = false;
    };
  }, []);

  const updateSettings = useCallback((patch: Partial<Settings>) => {
    setSettings((prev) => {
      const next = { ...prev, ...patch };
      AsyncStorage.setItem(SETTINGS_KEY, JSON.stringify(next)).catch(() => {});
      return next;
    });
  }, []);

  const value = useMemo(
    () => ({ settings, updateSettings, settingsLoaded: loaded }),
    [settings, updateSettings, loaded],
  );

  return <SettingsContext.Provider value={value}>{children}</SettingsContext.Provider>;
}

export function useSettings(): SettingsContextValue {
  const ctx = useContext(SettingsContext);
  if (!ctx) throw new Error("useSettings must be used within a SettingsProvider");
  return ctx;
}

export async function loadInstalled(): Promise<InstalledManifest> {
  try {
    const raw = await AsyncStorage.getItem(MANIFEST_KEY);
    if (!raw) return EMPTY_MANIFEST;
    const parsed = JSON.parse(raw) as Partial<InstalledManifest>;
    return { roms: parsed.roms ?? {}, mods: parsed.mods ?? {} };
  } catch {
    return EMPTY_MANIFEST;
  }
}

export async function saveInstalled(manifest: InstalledManifest): Promise<void> {
  await AsyncStorage.setItem(MANIFEST_KEY, JSON.stringify(manifest));
}

export type DiffStatus = "new" | "update" | "current";

export type DiffEntry = {
  id: string;
  label: string;
  kind: "rom" | "mod";
  status: DiffStatus;
  /** Installed version, if any. */
  from?: string;
  /** Registry version. */
  to: string;
};

/** Compares an installed version map against the registry's current versions. */
export function diffSection(
  kind: "rom" | "mod",
  installed: Record<string, string>,
  remote: { id: string; version: string; label: string }[],
): DiffEntry[] {
  return remote.map((item) => {
    const from = installed[item.id];
    const status: DiffStatus = from === undefined ? "new" : from === item.version ? "current" : "update";
    return { id: item.id, label: item.label, kind, status, from, to: item.version };
  });
}
