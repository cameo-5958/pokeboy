/**
 * Thin client for the pokeboy backend. The base URL comes from the
 * EXPO_PUBLIC_API_URL env var, falling back to localhost. On a physical
 * device, set it to your machine's LAN IP (e.g. http://192.168.1.20:4000).
 */

const BASE_URL =
  process.env.EXPO_PUBLIC_API_URL?.replace(/\/$/, "") ?? "http://localhost:4000";

export type Cartridge = {
  id: string;
  title: string;
  file: string;
  /** Absolute URL to the cartridge label image. */
  img: string | null;
};

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`);
  if (!res.ok) {
    throw new Error(`Request failed: ${res.status} ${res.statusText}`);
  }
  return (await res.json()) as T;
}

export const api = {
  baseUrl: BASE_URL,
  listCartridges: () => get<Cartridge[]>("/api/cartridges"),
  getCartridge: (id: string) => get<Cartridge>(`/api/cartridges/${id}`),
  /** URL to stream a cartridge's ROM bytes. */
  romUrl: (id: string) => `${BASE_URL}/api/cartridges/${id}/rom`,
  /** Minimal WASM player mounted inside the native Game Boy LCD. */
  emulatorUrl: (id: string) =>
    `${BASE_URL}/emulator/embed.html?cartridge=${encodeURIComponent(id)}`,
};
