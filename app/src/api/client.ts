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
  version: string;
  checksum: string | null;
};
export type Registry = { roms: RegistryRom[]; mods: RegistryMod[] };

export type ApiConfig = { baseUrl?: string; apiKey?: string };

export type Api = ReturnType<typeof createApi>;

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

  async function get<T>(path: string): Promise<T> {
    const res = await fetch(`${base}${path}`, { headers });
    if (!res.ok) {
      throw new Error(`Request failed: ${res.status} ${res.statusText}`);
    }
    return (await res.json()) as T;
  }

  return {
    baseUrl: base,
    listCartridges: () => get<Cartridge[]>("/api/cartridges"),
    getCartridge: (id: string) => get<Cartridge>(`/api/cartridges/${id}`),
    /** URL to stream a cartridge's ROM bytes. */
    romUrl: (id: string) => `${base}/api/cartridges/${id}/rom`,
    /** Minimal WASM player mounted inside the native Game Boy LCD. */
    emulatorUrl: (id: string) =>
      `${base}/emulator/embed.html?cartridge=${encodeURIComponent(id)}`,
    fetchRegistry: () => get<Registry>("/api/registry"),
  };
}
