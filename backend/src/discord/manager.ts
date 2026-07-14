/**
 * Lifecycle owner for the backend-hosted Battle Link Discord bot.
 *
 * The bot token never touches the phone or the WebView: it comes from the
 * DISCORD_BOT_TOKEN env var or from the git-ignored token file
 * (data/discord.json, `{"token": "..."}`). The file is watched, so dropping a
 * token in (or rotating it) takes effect without a server restart. No token
 * means no bot — every Battle Link route keeps working without one.
 */

import fsSync from "node:fs";
import fs from "node:fs/promises";

import { config } from "../config.js";
import { DiscordBattleLinkBot, type BotEvents } from "./battleLink.js";

let bot: DiscordBattleLinkBot | null = null;
let botToken = "";
let events: BotEvents | null = null;

/** The running bot, if a token is configured; null otherwise. */
export function discordBot(): DiscordBattleLinkBot | null {
  return bot;
}

async function readTokenFile(): Promise<string> {
  try {
    const raw = await fs.readFile(config.discordTokenFile, "utf8");
    const parsed = JSON.parse(raw) as { token?: unknown };
    return typeof parsed.token === "string" ? parsed.token.trim() : "";
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code !== "ENOENT") {
      console.warn(`battle-link discord: token file unreadable (${String(e)})`);
    }
    return "";
  }
}

async function reconcile(): Promise<void> {
  const token = config.discordToken || (await readTokenFile());
  if (token === botToken) return;
  if (bot) {
    bot.stop();
    bot = null;
    console.log("battle-link discord: bot stopped (token changed)");
  }
  botToken = token;
  if (!token || !events) return;
  bot = new DiscordBattleLinkBot(token, events);
  bot.start();
  console.log("battle-link discord: bot starting");
}

/**
 * Starts the bot when a token is configured and keeps it in sync with the
 * token file. `botEvents.sendDecision` is how widget clicks reach the
 * decision pipeline in routes/battle-link.ts.
 */
export function initDiscordBot(botEvents: BotEvents): void {
  events = botEvents;
  void reconcile();
  fsSync.watchFile(config.discordTokenFile, { interval: 2_000 }, (current, previous) => {
    if (current.mtimeMs === previous.mtimeMs) return;
    void reconcile();
  });
}
