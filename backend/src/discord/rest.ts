/**
 * Minimal Discord REST v10 client for the backend-hosted Battle Link bot.
 *
 * Only the handful of endpoints the bot needs; no external dependencies - it
 * runs on Node's built-in fetch. Every helper throws a DiscordRestError with
 * the response body on non-2xx so callers can fall back (e.g. channel
 * message -> interaction webhook followup).
 */

const API_BASE = "https://discord.com/api/v10";

export class DiscordRestError extends Error {
  constructor(
    readonly status: number,
    readonly body: string,
    path: string,
  ) {
    super(`Discord REST ${status} on ${path}: ${body.slice(0, 200)}`);
    this.name = "DiscordRestError";
  }
}

async function request(
  path: string,
  method: string,
  token: string | null,
  body?: unknown,
): Promise<unknown> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bot ${token}`;
  const response = await fetch(`${API_BASE}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  if (!response.ok) throw new DiscordRestError(response.status, text, path);
  return text ? JSON.parse(text) : null;
}

export type CommandDefinition = {
  name: string;
  description: string;
  type: number;
  integration_types: number[];
  contexts: number[];
};

/** Global application commands, installable as a user APP (usable anywhere). */
export const BATTLE_LINK_COMMANDS: CommandDefinition[] = [
  {
    name: "connect",
    description: "Connect this channel to the current Battle Link battle",
    type: 1, // CHAT_INPUT
    integration_types: [0, 1], // GUILD_INSTALL + USER_INSTALL
    contexts: [0, 1, 2], // GUILD + BOT_DM + PRIVATE_CHANNEL
  },
  {
    name: "disconnect",
    description: "Disconnect from the current Battle Link battle",
    type: 1,
    integration_types: [0, 1],
    contexts: [0, 1, 2],
  },
];

export function registerCommands(token: string, applicationId: string): Promise<unknown> {
  return request(`/applications/${applicationId}/commands`, "PUT", token, BATTLE_LINK_COMMANDS);
}

/** Interaction callback: must land within 3 seconds of INTERACTION_CREATE. */
export function interactionCallback(
  interactionId: string,
  interactionToken: string,
  payload: { type: number; data?: unknown },
): Promise<unknown> {
  return request(`/interactions/${interactionId}/${interactionToken}/callback`, "POST", null, payload);
}

export function createChannelMessage(
  token: string,
  channelId: string,
  payload: unknown,
): Promise<{ id: string }> {
  return request(`/channels/${channelId}/messages`, "POST", token, payload) as Promise<{ id: string }>;
}

export function editChannelMessage(
  token: string,
  channelId: string,
  messageId: string,
  payload: unknown,
): Promise<unknown> {
  return request(`/channels/${channelId}/messages/${messageId}`, "PATCH", token, payload);
}

/** Followup on an interaction token (valid 15 minutes; no bot token needed). */
export function createFollowup(
  applicationId: string,
  interactionToken: string,
  payload: unknown,
): Promise<{ id: string }> {
  return request(`/webhooks/${applicationId}/${interactionToken}`, "POST", null, payload) as Promise<{ id: string }>;
}

export function editWebhookMessage(
  applicationId: string,
  interactionToken: string,
  messageId: string,
  payload: unknown,
): Promise<unknown> {
  return request(
    `/webhooks/${applicationId}/${interactionToken}/messages/${messageId}`,
    "PATCH",
    null,
    payload,
  );
}
