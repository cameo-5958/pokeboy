import AsyncStorage from "@react-native-async-storage/async-storage";

/**
 * Client for the pokeboy backend. The base URL and API key are supplied at
 * runtime from the user's saved settings (see `createApi`); `DEFAULT_BASE_URL`
 * is only a fallback for when nothing has been configured yet.
 *
 * On a physical device, set the backend URL to your machine's LAN IP
 * (e.g. http://192.168.1.20:4000) in the app's Settings.
 */

const DEFAULT_BASE_URL =
  process.env.EXPO_PUBLIC_API_URL?.replace(/\/$/, "") ?? "http://localhost:4000";

export type Cartridge = {
  id: string;
  title: string;
  file: string;
  version?: string;
  /** Absolute URL to the cartridge label image. */
  img: string | null;
};

export type RegistryRom = {
  id: string;
  title: string;
  version: string;
  checksum: string | null;
};
export type RegistryMod = {
  id: string;
  name: string;
  desc: string | null;
  version: string;
  checksum: string | null;
};
export type Registry = { roms: RegistryRom[]; mods: RegistryMod[] };

export type ApiConfig = { baseUrl?: string; apiKey?: string };

/** Remote-control command queued by the backend's dev MCP tools. */
export type DevCommand =
  | { id: string; kind: "screenshot" }
  | { id: string; kind: "press"; buttons: number; dpad: number; holdMs: number };

/** A payload plus whether it came from the network (`fresh`) or the local cache. */
export type FetchResult<T> = { data: T; fresh: boolean };

export type Api = ReturnType<typeof createApi>;

/** Opt-in telemetry payload - a batch of buffered snapshots/events for one device+session. */
export interface TelemetryPayload {
  device: { id: string; os: string };
  session: string;
  cartridge: string | null;
  snapshots: unknown[];
  events: unknown[];
}

/**
 * Builds a backend client bound to a given base URL + API key. Callers pass the
 * user's saved settings so every request hits the configured backend and sends
 * the device key.
 */
export function createApi(cfg: ApiConfig = {}) {
  const base = cfg.baseUrl?.trim().replace(/\/$/, "") || DEFAULT_BASE_URL;
  const headers: Record<string, string> | undefined = cfg.apiKey
    ? { "x-api-key": cfg.apiKey }
    : undefined;

  async function getWithMeta<T>(path: string): Promise<FetchResult<T>> {
    const cacheKey = `pokeboy.api.v1:${encodeURIComponent(base)}:${path}`;
    let local: T | undefined;
    try {
      const raw = await AsyncStorage.getItem(cacheKey);
      if (raw) local = JSON.parse(raw) as T;
    } catch {
      // A corrupt/unavailable cache must not prevent an API refresh.
    }
    try {
      const res = await fetch(`${base}${path}`, { headers });
      if (!res.ok) throw new Error(`Request failed: ${res.status} ${res.statusText}`);
      const remote = (await res.json()) as T;
      await AsyncStorage.setItem(cacheKey, JSON.stringify(remote)).catch(() => {});
      return { data: remote, fresh: true };
    } catch (error) {
      if (local !== undefined) return { data: local, fresh: false };
      throw error;
    }
  }

  async function get<T>(path: string): Promise<T> {
    return (await getWithMeta<T>(path)).data;
  }

  return {
    baseUrl: base,
    /** Local-first: `fresh: false` means the payload came from the offline cache. */
    listCartridges: () => getWithMeta<Cartridge[]>("/api/cartridges"),
    getCartridge: (id: string) => get<Cartridge>(`/api/cartridges/${id}`),
    /** URL to stream a cartridge's ROM bytes. */
    romUrl: (id: string) => `${base}/api/cartridges/${id}/rom`,
    fetchRegistry: () => get<Registry>("/api/registry"),
    /** Local-first registry lookup, including whether the result is current. */
    fetchRegistryWithMeta: () => getWithMeta<Registry>("/api/registry"),
    /** Dev mode: fetch (and clear) the remote-control commands queued for this device. */
    pollDevCommands: async (deviceId: string): Promise<DevCommand[]> => {
      const res = await fetch(
        `${base}/api/dev/commands?device=${encodeURIComponent(deviceId)}`,
        { headers },
      );
      if (!res.ok) throw new Error(`Request failed: ${res.status} ${res.statusText}`);
      const body = (await res.json()) as { commands?: DevCommand[] };
      return Array.isArray(body.commands) ? body.commands : [];
    },
    /** Dev mode: report a command's outcome (press ack or screenshot PNG). */
    postDevResult: async (body: { id: string; ok: boolean; error?: string; png?: string }) => {
      const res = await fetch(`${base}/api/dev/result`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...(headers ?? {}) },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(`Request failed: ${res.status} ${res.statusText}`);
    },
    /** Fire-and-forget telemetry upload; callers should swallow rejections. */
    postTelemetry: async (body: unknown): Promise<void> => {
      const res = await fetch(`${base}/api/telemetry`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...(headers ?? {}) },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(`Request failed: ${res.status} ${res.statusText}`);
    },
  };
}
