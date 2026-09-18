// Native Game Boy screen is 160x144. The LCD keeps that ratio; the emulator
// mounts its framebuffer into the LCD well later.
export const SCREEN_RATIO = 160 / 144;

// The console keeps the same formation. Ejecting pops the cartridge out of the
// top slot, then the whole console slides down from under it - the cartridge
// always renders BEHIND the console body, never over it.
export const PAD_TOP = 28;
export const PAD_BOTTOM = 28;
export const PULL_DIST = 56; // drag distance that fully pulls the cartridge out
export const SPEEDS = ["x0.5", "x1", "x3", "xINF"] as const;

export const DEVICE_ID_KEY = "pokeboy.device-id.v1";
export const TELEMETRY_FLUSH_MS = 5000;
export const LCD_DRAIN_MS = 300;
export const LABEL_CACHE_PREFIX = "pokeboy.label.v1:";
export const CARTRIDGE_RETRY_MS = 15000;
export const MOD_REGISTRY_RETRY_MS = 15000;
export const DEV_POLL_MS = 750;
export const BATTLE_LINK_ENDPOINT_KEY = "pokeboy.mod.battle-link.endpoint.v1";
export const DEFAULT_BATTLE_LINK_ENDPOINT = process.env.EXPO_PUBLIC_BATTLE_LINK_URL ?? "";
export const BATTLE_LINK_MAX_WAIT_KEY = "pokeboy.mod.battle-link.maxTimeTillRandom.v1";
export const DEFAULT_BATTLE_LINK_MAX_WAIT_S = 30;
export const BATTLE_LINK_MODE_KEY = "pokeboy.mod.battle-link.mode.v1";
// The Discord bot moved to the backend; any token an older build stored in
// the device keychain is deleted on startup (the phone never needs it again).
export const BATTLE_LINK_DISCORD_TOKEN_KEY = "pokeboy.mod.battle-link.discord-token.v1";
export const ENABLED_MODS_KEY = "pokeboy.mods.enabled.v1";
