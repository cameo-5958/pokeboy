// Lightweight retro "blip" sound effects synthesized with the Web Audio API.
// It no-ops on platforms without Web Audio (e.g. native), so callers don't need
// to guard. On native, wire this up to expo-av if real sound is needed there.

type Sfx = "a" | "b" | "dpad" | "start" | "select" | "eject" | "insert" | "modOn" | "modOff";

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

export function playSfx(kind: Sfx) {
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
      tone(ac, 300, t, 0.06, "sawtooth", 0.13);
      tone(ac, 640, t + 0.06, 0.11, "sawtooth", 0.13);
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
