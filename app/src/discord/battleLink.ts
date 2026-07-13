/**
 * Phone-hosted Discord bot for the Battle Link "DISC" decision source.
 *
 * Bridges the emulator's decision snapshots (delivered by the native layer
 * from the WebView mod core) to a Discord channel:
 *
 *  - /connect binds the invoking channel to the battle currently awaiting or
 *    producing decisions. With no current battle the interaction is left
 *    unacknowledged on purpose.
 *  - every decision poll posts an embed with the battle state known so far
 *    plus move / switch / item selections as message components.
 *  - a component click resolves the pending decision back into the emulator.
 *  - /disconnect — or the battle ending — tears the session down.
 *
 * Opponent knowledge is accumulated per battle: a player mon appears in the
 * embed only once the mod has revealed it (it took the field), and its last
 * revealed data is retained for the rest of the battle.
 */

import { DiscordGateway } from "./gateway";
import {
  createChannelMessage,
  createFollowup,
  editChannelMessage,
  editWebhookMessage,
  interactionCallback,
  registerCommands,
} from "./rest";

// Gen 1 move names by internal id (1-165); id 0 is an empty move slot.
const MOVE_NAMES = ("POUND/KARATE CHOP/DOUBLESLAP/COMET PUNCH/MEGA PUNCH/PAY DAY/FIRE PUNCH/ICE PUNCH/" +
  "THUNDERPUNCH/SCRATCH/VICEGRIP/GUILLOTINE/RAZOR WIND/SWORDS DANCE/CUT/GUST/WING ATTACK/WHIRLWIND/FLY/" +
  "BIND/SLAM/VINE WHIP/STOMP/DOUBLE KICK/MEGA KICK/JUMP KICK/ROLLING KICK/SAND-ATTACK/HEADBUTT/" +
  "HORN ATTACK/FURY ATTACK/HORN DRILL/TACKLE/BODY SLAM/WRAP/TAKE DOWN/THRASH/DOUBLE-EDGE/TAIL WHIP/" +
  "POISON STING/TWINEEDLE/PIN MISSILE/LEER/BITE/GROWL/ROAR/SING/SUPERSONIC/SONICBOOM/DISABLE/ACID/" +
  "EMBER/FLAMETHROWER/MIST/WATER GUN/HYDRO PUMP/SURF/ICE BEAM/BLIZZARD/PSYBEAM/BUBBLEBEAM/AURORA BEAM/" +
  "HYPER BEAM/PECK/DRILL PECK/SUBMISSION/LOW KICK/COUNTER/SEISMIC TOSS/STRENGTH/ABSORB/MEGA DRAIN/" +
  "LEECH SEED/GROWTH/RAZOR LEAF/SOLARBEAM/POISONPOWDER/STUN SPORE/SLEEP POWDER/PETAL DANCE/STRING SHOT/" +
  "DRAGON RAGE/FIRE SPIN/THUNDERSHOCK/THUNDERBOLT/THUNDER WAVE/THUNDER/ROCK THROW/EARTHQUAKE/FISSURE/" +
  "DIG/TOXIC/CONFUSION/PSYCHIC/HYPNOSIS/MEDITATE/AGILITY/QUICK ATTACK/RAGE/TELEPORT/NIGHT SHADE/MIMIC/" +
  "SCREECH/DOUBLE TEAM/RECOVER/HARDEN/MINIMIZE/SMOKESCREEN/CONFUSE RAY/WITHDRAW/DEFENSE CURL/BARRIER/" +
  "LIGHT SCREEN/HAZE/REFLECT/FOCUS ENERGY/BIDE/METRONOME/MIRROR MOVE/SELFDESTRUCT/EGG BOMB/LICK/SMOG/" +
  "SLUDGE/BONE CLUB/FIRE BLAST/WATERFALL/CLAMP/SWIFT/SKULL BASH/SPIKE CANNON/CONSTRICT/AMNESIA/KINESIS/" +
  "SOFTBOILED/HI JUMP KICK/GLARE/DREAM EATER/POISON GAS/BARRAGE/LEECH LIFE/LOVELY KISS/SKY ATTACK/" +
  "TRANSFORM/BUBBLE/DIZZY PUNCH/SPORE/FLASH/PSYWAVE/SPLASH/ACID ARMOR/CRABHAMMER/EXPLOSION/FURY SWIPES/" +
  "BONEMERANG/REST/ROCK SLIDE/HYPER FANG/SHARPEN/CONVERSION/TRI ATTACK/SUPER FANG/SLASH/SUBSTITUTE/" +
  "STRUGGLE").split("/");

const ITEM_LABELS: Record<string, string> = {
  fullRestore: "FULL RESTORE", potion: "POTION", superPotion: "SUPER POTION",
  hyperPotion: "HYPER POTION", fullHeal: "FULL HEAL", guardSpec: "GUARD SPEC.",
  xAttack: "X ATTACK", xDefend: "X DEFEND", xSpeed: "X SPEED", xSpecial: "X SPECIAL",
};

const EMBED_COLOR = 0x306850; // Pokeboy shell green
const EMBED_COLOR_DONE = 0x8b8679;
// Interaction tokens die after 15 minutes; leave headroom before we stop
// using one as a message-delivery fallback.
const INTERACTION_TOKEN_TTL_MS = 14 * 60 * 1000;

export type SnapshotMove = { slot: number; move: number; current: number; ppUps: number };
export type SnapshotMon = {
  slot?: number;
  partySlot?: number;
  species: number;
  nickname: string;
  hp: number;
  maxHp: number;
  level: number;
  status: number;
  moves: SnapshotMove[];
};
export type LegalAction = {
  code: number;
  type: "move" | "switch" | "item";
  slot?: number;
  move?: number;
  current?: number;
  struggle?: boolean;
  partySlot?: number;
  item?: number;
  itemName?: string;
};
export type BattleSnapshot = {
  protocol: number;
  battleId: string;
  turn: number;
  attempt: number;
  timeoutMs: number;
  trainer: { class: number; party: SnapshotMon[]; active: SnapshotMon };
  opponent: { party: SnapshotMon[]; active: SnapshotMon };
  legalActions: LegalAction[];
};

export type BattleLinkDecision = { battleId: string; turn: number; attempt: number; action: number };

export type BotEvents = {
  /** Deliver a chosen action back to the emulator. */
  sendDecision: (decision: BattleLinkDecision) => void;
  /** Diagnostics (telemetry / console). */
  onEvent?: (kind: string, detail?: unknown) => void;
};

type MessageRef =
  | { kind: "channel"; channelId: string; messageId: string }
  | { kind: "webhook"; token: string; messageId: string };

type Session = {
  channelId: string | null;
  interactionToken: string;
  interactionAt: number;
};

type PendingPoll = {
  snapshot: BattleSnapshot;
  posted: MessageRef | null;
  decided: boolean;
};

function moveName(id: number): string {
  return MOVE_NAMES[id - 1] || `MOVE #${id}`;
}

function statusText(status: number): string {
  if (!status) return "";
  if (status & 0x40) return "PAR";
  if (status & 0x20) return "FRZ";
  if (status & 0x10) return "BRN";
  if (status & 0x08) return "PSN";
  if (status & 0x07) return "SLP";
  return "";
}

function hpBar(hp: number, maxHp: number): string {
  const total = 10;
  const filled = maxHp > 0 ? Math.max(hp > 0 ? 1 : 0, Math.round((hp / maxHp) * total)) : 0;
  return "▰".repeat(Math.min(total, filled)) + "▱".repeat(Math.max(0, total - filled));
}

function monLine(mon: SnapshotMon): string {
  const status = mon.hp === 0 ? "FNT" : statusText(mon.status);
  return `${mon.nickname || "?"} L${mon.level} ${mon.hp}/${mon.maxHp}${status ? ` [${status}]` : ""}`;
}

function activeDetail(mon: SnapshotMon): string {
  const moves = mon.moves
    .filter((move) => move.move > 0)
    .map((move) => `• ${moveName(move.move)} — ${move.current} PP`)
    .join("\n");
  return `**${monLine(mon)}**\n${hpBar(mon.hp, mon.maxHp)}\n${moves || "• (no known moves)"}`;
}

function actionLabel(action: LegalAction, snapshot: BattleSnapshot): string {
  if (action.type === "move") {
    if (action.struggle) return "STRUGGLE";
    return `${moveName(action.move || 0)} (${action.current ?? "?"} PP)`;
  }
  if (action.type === "switch") {
    const mon = snapshot.trainer.party.find((candidate) => candidate.slot === action.partySlot);
    return mon ? `SWITCH: ${monLine(mon)}` : `SWITCH #${action.partySlot}`;
  }
  return `USE ${ITEM_LABELS[action.itemName || ""] || action.itemName || "ITEM"}`;
}

export class DiscordBattleLinkBot {
  private gateway: DiscordGateway | null = null;
  private applicationId: string | null = null;
  private commandsRegistered = false;
  private session: Session | null = null;
  private activeBattleId: string | null = null;
  private pendingPoll: PendingPoll | null = null;
  private knownOpponent = new Map<number, SnapshotMon>();
  private stopped = false;

  constructor(
    private readonly token: string,
    private readonly events: BotEvents,
  ) {}

  start(): void {
    this.stopped = false;
    this.gateway = new DiscordGateway(this.token, {
      onReady: (applicationId) => this.onReady(applicationId),
      onInteraction: (interaction) => { void this.onInteraction(interaction); },
      onStatus: (status, detail) => this.emit(`gateway-${status}`, detail),
      onFatal: (reason) => this.emit("gateway-fatal", { reason }),
    });
    this.gateway.start();
  }

  stop(): void {
    this.stopped = true;
    this.gateway?.stop();
    this.gateway = null;
    this.session = null;
    this.pendingPoll = null;
    this.activeBattleId = null;
    this.knownOpponent.clear();
  }

  get connected(): boolean {
    return this.session !== null;
  }

  private emit(kind: string, detail?: unknown): void {
    try { this.events.onEvent?.(kind, detail); } catch {}
  }

  private onReady(applicationId: string): void {
    this.applicationId = applicationId;
    if (!this.commandsRegistered) {
      registerCommands(this.token, applicationId)
        .then(() => { this.commandsRegistered = true; this.emit("commands-registered"); })
        .catch((error) => this.emit("commands-register-failed", { message: String(error) }));
    }
  }

  // ---- Emulator-side events (forwarded by the native layer) ----------------

  handleRequest(snapshot: BattleSnapshot): void {
    if (this.stopped) return;
    if (this.activeBattleId && this.activeBattleId !== snapshot.battleId) {
      // The previous battle vanished without a battle-end (e.g. WebView
      // reload); drop its accumulated knowledge before tracking the new one.
      this.knownOpponent.clear();
    }
    this.activeBattleId = snapshot.battleId;
    const active = snapshot.opponent.active;
    const slot = typeof active.partySlot === "number" ? active.partySlot : -1;
    const partyEntry = snapshot.opponent.party.find((mon) => mon.slot === slot);
    if (partyEntry) this.knownOpponent.set(slot, partyEntry);
    // Refresh already-revealed mons from the latest snapshot (their HP/status
    // legitimately changed on-screen); never add unrevealed slots.
    for (const [seen, previous] of this.knownOpponent) {
      const fresh = snapshot.opponent.party.find((mon) => mon.slot === seen);
      if (fresh) this.knownOpponent.set(seen, fresh);
      else this.knownOpponent.set(seen, previous);
    }
    this.pendingPoll = { snapshot, posted: null, decided: false };
    if (this.session) void this.postPoll(this.pendingPoll);
  }

  handleCancel(detail: { battleId?: unknown; turn?: unknown; attempt?: unknown; reason?: unknown }): void {
    const poll = this.matchPoll(detail);
    if (!poll) return;
    this.pendingPoll = null;
    void this.finalizePollMessage(poll, `CANCELLED (${String(detail.reason || "cancelled")})`);
  }

  handleResolved(detail: { battleId?: unknown; turn?: unknown; attempt?: unknown; code?: unknown; source?: unknown }): void {
    const poll = this.matchPoll(detail);
    this.pendingPoll = null;
    if (!poll) return;
    // A Discord-sourced decision already rewrote the message via the
    // component ack; only fallback resolutions need an edit here.
    if (detail.source === "discord" && poll.decided) return;
    const action = poll.snapshot.legalActions.find((candidate) => candidate.code === detail.code);
    const label = action ? actionLabel(action, poll.snapshot) : `ACTION ${String(detail.code)}`;
    void this.finalizePollMessage(poll, `RESOLVED: ${label} (${String(detail.source || "unknown")})`);
  }

  handleBattleEnd(detail: { battleId?: unknown; reason?: unknown }): void {
    if (this.activeBattleId && detail.battleId && detail.battleId !== this.activeBattleId) return;
    const hadSession = this.session !== null;
    const poll = this.pendingPoll;
    this.pendingPoll = null;
    this.activeBattleId = null;
    this.knownOpponent.clear();
    if (poll) void this.finalizePollMessage(poll, "BATTLE ENDED");
    if (hadSession) {
      void this.postNote("**BATTLE LINK** — battle ended, disconnected.");
      this.session = null;
      this.emit("auto-disconnected", detail);
    }
  }

  /** The WebView rebooted: all emulator-side battle state is gone. */
  handleEmulatorReset(): void {
    this.handleBattleEnd({ battleId: this.activeBattleId, reason: "emulator-reset" });
  }

  private matchPoll(detail: { battleId?: unknown; turn?: unknown; attempt?: unknown }): PendingPoll | null {
    const poll = this.pendingPoll;
    if (!poll) return null;
    const { snapshot } = poll;
    if (detail.battleId !== snapshot.battleId || detail.turn !== snapshot.turn || detail.attempt !== snapshot.attempt) {
      return null;
    }
    return poll;
  }

  // ---- Discord-side events --------------------------------------------------

  private async onInteraction(interaction: any): Promise<void> {
    try {
      if (interaction?.type === 2) await this.onCommand(interaction);
      else if (interaction?.type === 3) await this.onComponent(interaction);
    } catch (error) {
      this.emit("interaction-error", { message: String(error) });
    }
  }

  private channelIdOf(interaction: any): string | null {
    const id = interaction?.channel_id ?? interaction?.channel?.id;
    return typeof id === "string" ? id : null;
  }

  private async onCommand(interaction: any): Promise<void> {
    const name = interaction?.data?.name;
    if (name === "connect") {
      // No current battle: deliberately leave the interaction unacknowledged.
      if (!this.activeBattleId && !this.pendingPoll) {
        this.emit("connect-ignored");
        return;
      }
      this.session = {
        channelId: this.channelIdOf(interaction),
        interactionToken: interaction.token,
        interactionAt: Date.now(),
      };
      const poll = this.pendingPoll;
      if (poll && !poll.decided) {
        await interactionCallback(interaction.id, interaction.token, {
          type: 4, // CHANNEL_MESSAGE_WITH_SOURCE
          data: this.pollMessage(poll.snapshot),
        });
        poll.posted = { kind: "webhook", token: interaction.token, messageId: "@original" };
      } else {
        await interactionCallback(interaction.id, interaction.token, {
          type: 4,
          data: { embeds: [this.infoEmbed("Connected. Waiting for the next decision…")] },
        });
      }
      this.emit("connected", { channelId: this.session.channelId });
    } else if (name === "disconnect") {
      if (!this.session) {
        this.emit("disconnect-ignored");
        return;
      }
      this.session = null;
      await interactionCallback(interaction.id, interaction.token, {
        type: 4,
        data: { embeds: [this.infoEmbed("Disconnected from the battle.")], flags: 64 }, // ephemeral
      });
      this.emit("disconnected");
    }
  }

  private async onComponent(interaction: any): Promise<void> {
    const customId = String(interaction?.data?.custom_id || "");
    const parts = customId.split("|");
    const kind = parts[0];
    if (kind !== "bl" && kind !== "bls") return;
    if (this.session) {
      // Any interaction refreshes the webhook fallback token.
      this.session.interactionToken = interaction.token;
      this.session.interactionAt = Date.now();
    }
    const [, battleId, turnText, attemptText] = parts;
    const code = kind === "bl" ? Number(parts[4]) : Number(interaction?.data?.values?.[0]);
    const poll = this.matchPoll({ battleId, turn: Number(turnText), attempt: Number(attemptText) });
    if (!poll || poll.decided || !Number.isInteger(code)) {
      // Stale click: silently ack so Discord doesn't flag a failure.
      await interactionCallback(interaction.id, interaction.token, { type: 6 }); // DEFERRED_UPDATE_MESSAGE
      return;
    }
    const action = poll.snapshot.legalActions.find((candidate) => candidate.code === code);
    if (!action) {
      await interactionCallback(interaction.id, interaction.token, { type: 6 });
      return;
    }
    poll.decided = true;
    this.events.sendDecision({
      battleId: poll.snapshot.battleId,
      turn: poll.snapshot.turn,
      attempt: poll.snapshot.attempt,
      action: code,
    });
    await interactionCallback(interaction.id, interaction.token, {
      type: 7, // UPDATE_MESSAGE
      data: {
        embeds: [this.pollEmbed(poll.snapshot, `CHOSEN: ${actionLabel(action, poll.snapshot)}`)],
        components: [],
      },
    });
    this.emit("decision", { code });
  }

  // ---- Message delivery -----------------------------------------------------

  private webhookTokenFresh(): boolean {
    return this.session !== null && Date.now() - this.session.interactionAt < INTERACTION_TOKEN_TTL_MS;
  }

  private async postPoll(poll: PendingPoll): Promise<void> {
    const session = this.session;
    if (!session || !this.applicationId) return;
    const payload = this.pollMessage(poll.snapshot);
    if (session.channelId) {
      try {
        const message = await createChannelMessage(this.token, session.channelId, payload);
        poll.posted = { kind: "channel", channelId: session.channelId, messageId: message.id };
        return;
      } catch (error) {
        this.emit("channel-post-failed", { message: String(error) });
      }
    }
    if (this.webhookTokenFresh()) {
      try {
        const message = await createFollowup(this.applicationId, session.interactionToken, payload);
        poll.posted = { kind: "webhook", token: session.interactionToken, messageId: message.id };
        return;
      } catch (error) {
        this.emit("followup-post-failed", { message: String(error) });
      }
    }
    this.emit("poll-post-failed", { battleId: poll.snapshot.battleId, turn: poll.snapshot.turn });
  }

  private async finalizePollMessage(poll: PendingPoll, footer: string): Promise<void> {
    const posted = poll.posted;
    if (!posted || !this.applicationId) return;
    const payload = {
      embeds: [this.pollEmbed(poll.snapshot, footer)],
      components: [],
    };
    try {
      if (posted.kind === "channel") {
        await editChannelMessage(this.token, posted.channelId, posted.messageId, payload);
      } else {
        await editWebhookMessage(this.applicationId, posted.token, posted.messageId, payload);
      }
    } catch (error) {
      this.emit("finalize-failed", { message: String(error) });
    }
  }

  private async postNote(content: string): Promise<void> {
    const session = this.session;
    if (!session) return;
    try {
      if (session.channelId) {
        await createChannelMessage(this.token, session.channelId, { content });
        return;
      }
      if (this.applicationId && this.webhookTokenFresh()) {
        await createFollowup(this.applicationId, session.interactionToken, { content });
      }
    } catch (error) {
      this.emit("note-post-failed", { message: String(error) });
    }
  }

  // ---- Embed / component building -------------------------------------------

  private infoEmbed(text: string) {
    return { title: "BATTLE LINK", description: text, color: EMBED_COLOR };
  }

  private pollEmbed(snapshot: BattleSnapshot, footer?: string) {
    const trainerParty = snapshot.trainer.party.map(
      (mon, index) => `${index + 1}. ${monLine(mon)}`,
    ).join("\n");
    const opponentParty = snapshot.opponent.party.map((mon, index) => {
      const known = typeof mon.slot === "number" ? this.knownOpponent.get(mon.slot) : undefined;
      return `${index + 1}. ${known ? monLine(known) : "???"}`;
    }).join("\n");
    return {
      title: `BATTLE LINK — TURN ${snapshot.turn}${snapshot.attempt ? ` · RETRY ${snapshot.attempt}` : ""}`,
      color: footer ? EMBED_COLOR_DONE : EMBED_COLOR,
      description: `Trainer class ${snapshot.trainer.class} · decide within ${Math.round(snapshot.timeoutMs / 1000)}s`,
      fields: [
        { name: "YOUR ACTIVE", value: activeDetail(snapshot.trainer.active), inline: true },
        { name: "OPPONENT ACTIVE", value: activeDetail(snapshot.opponent.active), inline: true },
        { name: "YOUR PARTY", value: trainerParty || "—", inline: false },
        { name: "OPPONENT PARTY (REVEALED)", value: opponentParty || "—", inline: false },
      ],
      footer: { text: footer || `battle ${snapshot.battleId.slice(0, 8)}` },
    };
  }

  private pollComponents(snapshot: BattleSnapshot) {
    const key = `${snapshot.battleId}|${snapshot.turn}|${snapshot.attempt}`;
    const moves = snapshot.legalActions.filter((action) => action.type === "move");
    const switches = snapshot.legalActions.filter((action) => action.type === "switch");
    const items = snapshot.legalActions.filter((action) => action.type === "item");
    const rows: unknown[] = [];
    if (moves.length) {
      rows.push({
        type: 1, // ACTION_ROW
        components: moves.slice(0, 5).map((action) => ({
          type: 2, // BUTTON
          style: 1, // PRIMARY
          label: actionLabel(action, snapshot).slice(0, 80),
          custom_id: `bl|${key}|${action.code}`,
        })),
      });
    }
    if (switches.length) {
      rows.push({
        type: 1,
        components: [{
          type: 3, // STRING_SELECT
          custom_id: `bls|${key}`,
          placeholder: "SWITCH POKÉMON…",
          options: switches.slice(0, 25).map((action) => ({
            label: actionLabel(action, snapshot).slice(0, 100),
            value: String(action.code),
          })),
        }],
      });
    }
    if (items.length) {
      rows.push({
        type: 1,
        components: items.slice(0, 5).map((action) => ({
          type: 2,
          style: 2, // SECONDARY
          label: actionLabel(action, snapshot).slice(0, 80),
          custom_id: `bl|${key}|${action.code}`,
        })),
      });
    }
    return rows;
  }

  private pollMessage(snapshot: BattleSnapshot) {
    return {
      embeds: [this.pollEmbed(snapshot)],
      components: this.pollComponents(snapshot),
    };
  }
}
