const MAX_SNAPSHOTS = 720;
const MAX_EVENTS = 500;
const MAX_SESSIONS = 50;
const SESSION_TTL_MS = 24 * 60 * 60 * 1_000;

export interface TelemetryDevice {
  id: string;
  os: string;
}

export interface TelemetrySnapshot {
  t: number;
  rafFps: number;
  emuFps: number;
  speed: number | "inf";
  accMs: number;
  running: boolean;
  audio: {
    state: string;
    mode: string;
    backlogFrames: number;
    sampleRate: number;
  };
  wasmHeapBytes: number;
  jsHeapBytes: number | null;
}

export interface TelemetryEvent {
  t: number;
  kind: string;
  detail: unknown;
}

export interface TelemetrySession {
  device: TelemetryDevice;
  session: string;
  cartridge: string | null;
  startedAt: string;
  lastSeen: string;
  snapshots: TelemetrySnapshot[];
  events: TelemetryEvent[];
}

export interface TelemetrySessionSummary {
  key: string;
  device: TelemetryDevice;
  cartridge: string | null;
  startedAt: string;
  lastSeen: string;
  snapshotCount: number;
  eventCount: number;
  latest: TelemetrySnapshot | null;
}

const sessions = new Map<string, TelemetrySession>();

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function parseSnapshot(value: unknown): TelemetrySnapshot | undefined {
  if (!isRecord(value) || !isRecord(value.audio)) return undefined;

  const audio = value.audio;
  if (
    !isFiniteNumber(value.t)
    || !isFiniteNumber(value.rafFps)
    || !isFiniteNumber(value.emuFps)
    || !(isFiniteNumber(value.speed) || value.speed === "inf")
    || !isFiniteNumber(value.accMs)
    || typeof value.running !== "boolean"
    || typeof audio.state !== "string"
    || typeof audio.mode !== "string"
    || !isFiniteNumber(audio.backlogFrames)
    || !isFiniteNumber(audio.sampleRate)
    || !isFiniteNumber(value.wasmHeapBytes)
    || !(value.jsHeapBytes === null || isFiniteNumber(value.jsHeapBytes))
  ) {
    return undefined;
  }

  return {
    t: value.t,
    rafFps: value.rafFps,
    emuFps: value.emuFps,
    speed: value.speed,
    accMs: value.accMs,
    running: value.running,
    audio: {
      state: audio.state,
      mode: audio.mode,
      backlogFrames: audio.backlogFrames,
      sampleRate: audio.sampleRate,
    },
    wasmHeapBytes: value.wasmHeapBytes,
    jsHeapBytes: value.jsHeapBytes,
  };
}

function parseEvent(value: unknown): TelemetryEvent | undefined {
  if (
    !isRecord(value)
    || !isFiniteNumber(value.t)
    || typeof value.kind !== "string"
    || !("detail" in value)
  ) {
    return undefined;
  }
  return { t: value.t, kind: value.kind, detail: value.detail };
}

function trimOldest<T>(values: T[], maximum: number): void {
  if (values.length > maximum) values.splice(0, values.length - maximum);
}

function sweep(now = Date.now()): void {
  for (const [key, session] of sessions) {
    if (now - Date.parse(session.lastSeen) > SESSION_TTL_MS) sessions.delete(key);
  }

  if (sessions.size <= MAX_SESSIONS) return;
  const oldestFirst = [...sessions.entries()].sort(
    ([, a], [, b]) => Date.parse(a.lastSeen) - Date.parse(b.lastSeen),
  );
  for (const [key] of oldestFirst.slice(0, sessions.size - MAX_SESSIONS)) {
    sessions.delete(key);
  }
}

/** Validate and append one telemetry batch. Malformed individual records are ignored. */
export function ingest(body: unknown): void {
  if (!isRecord(body)) throw new Error("Telemetry body must be an object");
  if (!isRecord(body.device)) throw new Error("device must be an object");

  const { device } = body;
  if (typeof device.id !== "string" || device.id.trim() === "") {
    throw new Error("device.id must be a non-empty string");
  }
  if (typeof device.os !== "string" || device.os.trim() === "") {
    throw new Error("device.os must be a non-empty string");
  }
  if (typeof body.session !== "string" || body.session.trim() === "") {
    throw new Error("session must be a non-empty string");
  }
  if (!(body.cartridge === null || typeof body.cartridge === "string")) {
    throw new Error("cartridge must be a string or null");
  }
  if (!Array.isArray(body.snapshots)) throw new Error("snapshots must be an array");
  if (!Array.isArray(body.events)) throw new Error("events must be an array");

  const now = new Date();
  sweep(now.getTime());

  const normalizedDevice = { id: device.id, os: device.os };
  const key = `${normalizedDevice.id}:${body.session}`;
  let telemetrySession = sessions.get(key);
  if (!telemetrySession) {
    telemetrySession = {
      device: normalizedDevice,
      session: body.session,
      cartridge: body.cartridge,
      startedAt: now.toISOString(),
      lastSeen: now.toISOString(),
      snapshots: [],
      events: [],
    };
    sessions.set(key, telemetrySession);
  }

  telemetrySession.device = normalizedDevice;
  telemetrySession.cartridge = body.cartridge;
  telemetrySession.lastSeen = now.toISOString();
  telemetrySession.snapshots.push(
    ...body.snapshots.flatMap((snapshot) => {
      const parsed = parseSnapshot(snapshot);
      return parsed ? [parsed] : [];
    }),
  );
  telemetrySession.events.push(
    ...body.events.flatMap((event) => {
      const parsed = parseEvent(event);
      return parsed ? [parsed] : [];
    }),
  );
  trimOldest(telemetrySession.snapshots, MAX_SNAPSHOTS);
  trimOldest(telemetrySession.events, MAX_EVENTS);
  sweep(now.getTime());
}

function toSummary(key: string, session: TelemetrySession): TelemetrySessionSummary {
  return {
    key,
    device: session.device,
    cartridge: session.cartridge,
    startedAt: session.startedAt,
    lastSeen: session.lastSeen,
    snapshotCount: session.snapshots.length,
    eventCount: session.events.length,
    latest: session.snapshots.at(-1) ?? null,
  };
}

export function listSessions(): TelemetrySessionSummary[] {
  sweep();
  return [...sessions.entries()]
    .map(([key, session]) => toSummary(key, session))
    .sort((a, b) => Date.parse(b.lastSeen) - Date.parse(a.lastSeen));
}

export function getSession(key: string): TelemetrySession | undefined {
  sweep();
  return sessions.get(key);
}

function dottedValue(snapshot: TelemetrySnapshot, metric: string): unknown {
  let current: unknown = snapshot;
  for (const part of metric.split(".")) {
    if (!part || !isRecord(current) || !Object.prototype.hasOwnProperty.call(current, part)) {
      return undefined;
    }
    current = current[part];
  }
  return current;
}

export function getSeries(
  key: string,
  metric: string,
  seconds?: number,
): Array<{ t: number; value: unknown }> | undefined {
  sweep();
  const session = sessions.get(key);
  if (!session) return undefined;

  const newestT = session.snapshots.at(-1)?.t;
  const cutoff = seconds === undefined || newestT === undefined
    ? Number.NEGATIVE_INFINITY
    : newestT - Math.max(0, seconds) * 1_000;

  return session.snapshots.flatMap((snapshot) => {
    if (snapshot.t < cutoff) return [];
    const value = dottedValue(snapshot, metric);
    return value === undefined ? [] : [{ t: snapshot.t, value }];
  });
}

export function getEvents(key: string): TelemetryEvent[] | undefined {
  sweep();
  return sessions.get(key)?.events;
}
