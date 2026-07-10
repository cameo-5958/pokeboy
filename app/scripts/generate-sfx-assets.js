/* eslint-env node */
// Renders the oscillator envelopes from src/sfx.ts to bundled WAV assets, so
// native platforms (no Web Audio API) can play the same blips via expo-audio.
// Re-run with `node scripts/generate-sfx-assets.js` after tuning a sound in sfx.ts.
const fs = require("fs");
const path = require("path");

const SAMPLE_RATE = 44100;
const OUT_DIR = path.join(__dirname, "..", "assets", "sfx");

function waveform(type, phase) {
  const p = phase - Math.floor(phase);
  switch (type) {
    case "square":
      return p < 0.5 ? 1 : -1;
    case "sawtooth":
      return 2 * (p - 0.5);
    case "triangle":
      return 4 * Math.abs(p - 0.5) - 1;
    default:
      throw new Error(`unknown waveform ${type}`);
  }
}

// Mirrors AudioSystem's `tone()`: quick exponential attack, exponential decay,
// oscillator continues (silently, at the 0.0001 floor) until stop.
function renderTone(buffer, freq, start, dur, type, peak = 0.16) {
  const attack = 0.006;
  const stop = start + dur + 0.02;
  const startSample = Math.floor(start * SAMPLE_RATE);
  const stopSample = Math.ceil(stop * SAMPLE_RATE);
  for (let s = startSample; s < stopSample && s < buffer.length; s++) {
    const t = s / SAMPLE_RATE;
    const dt = t - start;
    let gain;
    if (dt < attack) {
      gain = 0.0001 * Math.pow(peak / 0.0001, dt / attack);
    } else if (dt < dur) {
      gain = peak * Math.pow(0.0001 / peak, (dt - attack) / (dur - attack));
    } else {
      gain = 0.0001;
    }
    buffer[s] += gain * waveform(type, freq * dt);
  }
}

function renderClick(buffer, freq, start, peak = 0.13) {
  renderTone(buffer, freq, start, 0.026, "square", peak);
  renderTone(buffer, freq * 1.5, start + 0.004, 0.018, "triangle", peak * 0.5);
}

// One render function per Sfx kind, matching playSfx()'s switch in sfx.ts.
const SFX = {
  a: (buf) => renderTone(buf, 880, 0, 0.09, "square"),
  b: (buf) => renderTone(buf, 660, 0, 0.09, "square"),
  dpad: (buf) => renderTone(buf, 440, 0, 0.05, "square", 0.12),
  select: (buf) => renderTone(buf, 330, 0, 0.07, "square"),
  start: (buf) => {
    renderTone(buf, 523, 0, 0.07, "square");
    renderTone(buf, 784, 0.07, 0.09, "square");
  },
  eject: (buf) => {
    renderTone(buf, 620, 0, 0.06, "sawtooth", 0.13);
    renderTone(buf, 300, 0.06, 0.13, "sawtooth", 0.13);
  },
  insert: (buf) => {
    renderTone(buf, 170, 0, 0.045, "triangle", 0.12);
    renderClick(buf, 760, 0.042, 0.12);
    renderClick(buf, 1140, 0.086, 0.1);
  },
  snap: (buf) => {
    renderClick(buf, 980, 0, 0.1);
    renderTone(buf, 180, 0.018, 0.032, "triangle", 0.08);
  },
  modOn: (buf) => {
    renderTone(buf, 523, 0, 0.05, "square", 0.12);
    renderTone(buf, 880, 0.05, 0.08, "square", 0.12);
  },
  modOff: (buf) => {
    renderTone(buf, 523, 0, 0.05, "square", 0.12);
    renderTone(buf, 349, 0.05, 0.08, "square", 0.12);
  },
};

const DURATIONS = {
  a: 0.11,
  b: 0.11,
  dpad: 0.07,
  select: 0.09,
  start: 0.18,
  eject: 0.21,
  insert: 0.14,
  snap: 0.07,
  modOn: 0.15,
  modOff: 0.15,
};

function writeWav(filePath, samples) {
  const dataSize = samples.length * 2;
  const buf = Buffer.alloc(44 + dataSize);
  buf.write("RIFF", 0);
  buf.writeUInt32LE(36 + dataSize, 4);
  buf.write("WAVE", 8);
  buf.write("fmt ", 12);
  buf.writeUInt32LE(16, 16);
  buf.writeUInt16LE(1, 20); // PCM
  buf.writeUInt16LE(1, 22); // mono
  buf.writeUInt32LE(SAMPLE_RATE, 24);
  buf.writeUInt32LE(SAMPLE_RATE * 2, 28); // byte rate
  buf.writeUInt16LE(2, 32); // block align
  buf.writeUInt16LE(16, 34); // bits per sample
  buf.write("data", 36);
  buf.writeUInt32LE(dataSize, 40);
  for (let i = 0; i < samples.length; i++) {
    const clamped = Math.max(-1, Math.min(1, samples[i]));
    buf.writeInt16LE(Math.round(clamped * 32767), 44 + i * 2);
  }
  fs.writeFileSync(filePath, buf);
}

fs.mkdirSync(OUT_DIR, { recursive: true });
for (const [kind, render] of Object.entries(SFX)) {
  const buffer = new Float32Array(Math.ceil(DURATIONS[kind] * SAMPLE_RATE));
  render(buffer);
  const outPath = path.join(OUT_DIR, `${kind}.wav`);
  writeWav(outPath, buffer);
  console.log(`wrote ${outPath}`);
}
