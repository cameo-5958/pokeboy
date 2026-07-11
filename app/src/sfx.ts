// Lightweight retro "blip" sound effects.
//
// On web, tones are synthesized live with the Web Audio API (unchanged).
// On native (iOS/Android), there's no Web Audio API, so the same blips are
// pre-rendered to WAV assets (see scripts/generate-sfx-assets.js) and played
// through expo-audio.

import { Platform } from "react-native";

type Sfx = "a" | "b" | "dpad" | "start" | "select" | "eject" | "insert" | "snap" | "modOn" | "modOff";

// ---- native (expo-audio) ----------------------------------------------

const NATIVE_SOURCES: Record<Sfx, number> | null =
  Platform.OS === "web"
    ? null
    : {
        a: require("../assets/sfx/a.wav"),
        b: require("../assets/sfx/b.wav"),
        dpad: require("../assets/sfx/dpad.wav"),
        start: require("../assets/sfx/start.wav"),
        select: require("../assets/sfx/select.wav"),
        eject: require("../assets/sfx/eject.wav"),
        insert: require("../assets/sfx/insert.wav"),
        snap: require("../assets/sfx/snap.wav"),
        modOn: require("../assets/sfx/modOn.wav"),
        modOff: require("../assets/sfx/modOff.wav"),
      };

type NativeAudioPlayer = { play(): void; seekTo(seconds: number): void | Promise<void> };
let createAudioPlayer: ((source: number) => NativeAudioPlayer) | null = null;
if (NATIVE_SOURCES) {
  // Lazily required so a web bundle never has to resolve the native module.
  ({ createAudioPlayer } = require("expo-audio"));
}

// Reused players, a small round-robin pool per effect. Creating a fresh native
// player on every press leaks/exhausts iOS audio resources under rapid input,
// which eventually makes creation throw on each press. Two players per kind
// lets rapid repeat taps overlap without a seekTo() race on a playing player.
const POOL_SIZE = 2;
const pools = new Map<Sfx, NativeAudioPlayer[]>();
const poolNext = new Map<Sfx, number>();

function playNative(kind: Sfx) {
  if (!createAudioPlayer || !NATIVE_SOURCES) return;
  let pool = pools.get(kind);
  if (!pool) {
    pool = [];
    pools.set(kind, pool);
  }
  let player: NativeAudioPlayer;
  if (pool.length < POOL_SIZE) {
    player = createAudioPlayer(NATIVE_SOURCES[kind]);
    pool.push(player);
  } else {
    const i = (poolNext.get(kind) ?? 0) % pool.length;
    poolNext.set(kind, i + 1);
    player = pool[i];
    void Promise.resolve(player.seekTo(0)).catch(() => {});
  }
  player.play();
}

// ---- web (Web Audio API) ------------------------------------------------

let ctx: any = null;
let unavailable = false;

function context(): any {
  if (unavailable) return null;
  if (ctx) return ctx;
  const g = globalThis as any;
  const AC = g.AudioContext || g.webkitAudioContext;
  if (!AC) {
    unavailable = true;
    return null;
  }
  ctx = new AC();
  return ctx;
}

function tone(
  ac: any,
  freq: number,
  start: number,
  dur: number,
  type: string,
  peak = 0.16,
) {
  const osc = ac.createOscillator();
  const gain = ac.createGain();
  osc.type = type;
  osc.frequency.setValueAtTime(freq, start);
  // Quick attack, exponential decay — a short, punchy 8-bit blip.
  gain.gain.setValueAtTime(0.0001, start);
  gain.gain.exponentialRampToValueAtTime(peak, start + 0.006);
  gain.gain.exponentialRampToValueAtTime(0.0001, start + dur);
  osc.connect(gain).connect(ac.destination);
  osc.start(start);
  osc.stop(start + dur + 0.02);
}

function click(ac: any, freq: number, start: number, peak = 0.13) {
  tone(ac, freq, start, 0.026, "square", peak);
  tone(ac, freq * 1.5, start + 0.004, 0.018, "triangle", peak * 0.5);
}

function playWeb(kind: Sfx) {
  const ac = context();
  if (!ac) return;
  // Browsers keep the context suspended until a user gesture; presses count.
  if (ac.state === "suspended") void ac.resume();
  const t = ac.currentTime;

  switch (kind) {
    case "a":
      tone(ac, 880, t, 0.09, "square");
      break;
    case "b":
      tone(ac, 660, t, 0.09, "square");
      break;
    case "dpad":
      tone(ac, 440, t, 0.05, "square", 0.12);
      break;
    case "select":
      tone(ac, 330, t, 0.07, "square");
      break;
    case "start":
      tone(ac, 523, t, 0.07, "square");
      tone(ac, 784, t + 0.07, 0.09, "square");
      break;
    case "eject":
      tone(ac, 620, t, 0.06, "sawtooth", 0.13);
      tone(ac, 300, t + 0.06, 0.13, "sawtooth", 0.13);
      break;
    case "insert":
      tone(ac, 170, t, 0.045, "triangle", 0.12);
      click(ac, 760, t + 0.042, 0.12);
      click(ac, 1140, t + 0.086, 0.1);
      break;
    case "snap":
      click(ac, 980, t, 0.1);
      tone(ac, 180, t + 0.018, 0.032, "triangle", 0.08);
      break;
    case "modOn":
      tone(ac, 523, t, 0.05, "square", 0.12);
      tone(ac, 880, t + 0.05, 0.08, "square", 0.12);
      break;
    case "modOff":
      tone(ac, 523, t, 0.05, "square", 0.12);
      tone(ac, 349, t + 0.05, 0.08, "square", 0.12);
      break;
  }
}

// ---------------------------------------------------------------------

export function playSfx(kind: Sfx) {
  // Sound is best-effort garnish: a broken audio stack must never throw into
  // a button handler and take game input down with it.
  try {
    if (NATIVE_SOURCES) {
      playNative(kind);
    } else {
      playWeb(kind);
    }
  } catch {}
}
