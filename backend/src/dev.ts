/**
 * Dev-mode remote control: an in-memory command mailbox between MCP tools and
 * devices. The app polls for queued commands while its DEV MODE toggle is on
 * and posts each command's result back; `dispatch` resolves when the matching
 * result arrives (or times out if the device never picks the command up).
 */

import { randomUUID } from "node:crypto";

export type DevCommand =
  | { id: string; kind: "screenshot" }
  | { id: string; kind: "press"; buttons: number; dpad: number; holdMs: number };

export type DevResult = {
  id: string;
  ok: boolean;
  error?: string;
  /** Screenshot payload: a data:image/png;base64 URI of the 160x144 LCD. */
  png?: string;
};

const RESULT_TIMEOUT_MS = 15_000;

const queues = new Map<string, DevCommand[]>();
const waiters = new Map<string, { resolve: (result: DevResult) => void; timer: NodeJS.Timeout }>();
const lastPoll = new Map<string, number>();

/** Devices that have polled for commands, most recently active first. */
export function listDevices(): { id: string; lastPollAt: string; secondsAgo: number }[] {
  const now = Date.now();
  return Array.from(lastPoll.entries())
    .sort((a, b) => b[1] - a[1])
    .map(([id, at]) => ({
      id,
      lastPollAt: new Date(at).toISOString(),
      secondsAgo: Math.round((now - at) / 1000),
    }));
}

/** A device is "live" if it has polled within the last few poll intervals. */
export function liveDevices(withinMs = 10_000): string[] {
  const now = Date.now();
  return listDevices()
    .filter(({ id }) => now - (lastPoll.get(id) ?? 0) <= withinMs)
    .map(({ id }) => id);
}

/** Hand all queued commands to the polling device. */
export function drainCommands(device: string): DevCommand[] {
  lastPoll.set(device, Date.now());
  const queue = queues.get(device) ?? [];
  queues.set(device, []);
  return queue;
}

/** Resolve the waiter for a completed command. Returns false for unknown ids. */
export function submitResult(result: DevResult): boolean {
  const waiter = waiters.get(result.id);
  if (!waiter) return false;
  waiters.delete(result.id);
  clearTimeout(waiter.timer);
  waiter.resolve(result);
  return true;
}

/** Queue a command for a device and wait for its result. */
export function dispatch(
  device: string,
  command: { kind: "screenshot" } | { kind: "press"; buttons: number; dpad: number; holdMs: number },
): Promise<DevResult> {
  const id = randomUUID();
  const full = { ...command, id } as DevCommand;
  const queue = queues.get(device);
  if (queue) queue.push(full);
  else queues.set(device, [full]);

  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      waiters.delete(id);
      // Drop the command if the device never picked it up, so a later poll
      // doesn't execute a press the caller already gave up on.
      const pending = queues.get(device);
      if (pending) queues.set(device, pending.filter((c) => c.id !== id));
      resolve({
        id,
        ok: false,
        error: "Timed out waiting for the device. Is DEV MODE toggled on and the app foregrounded?",
      });
    }, RESULT_TIMEOUT_MS);
    waiters.set(id, { resolve, timer });
  });
}
