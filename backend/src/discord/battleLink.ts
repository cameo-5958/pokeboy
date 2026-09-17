/**
 * Backend-hosted Discord bot for the Battle Link "DISC" decision source.
 *
 * Bridges the emulator's decision snapshots (delivered over HTTP by the
 * WebView mod core via /battle-link/decision and /battle-link/event) to a
 * Discord channel:
 *
 *  - /connect binds the invoking channel to the battle currently awaiting or
 *    producing decisions.
 *  - the session owns ONE widget message; every decision poll, resolution and
 *    battle end edits that same message in place (state embed plus move /
 *    switch / item components) instead of posting a new one.
 *  - a component click resolves the pending decision back into the emulator.
 *    Clicks are acknowledged before any other work: the 3-second callback
 *    window is the whole budget on a phone network, and each ack also chains
 *    the widget onto the click's fresh webhook token, so a user-installed app
 *    with no channel access can keep editing the widget indefinitely.
 *  - /disconnect - or the battle ending - tears the session down.
 *
 * Opponent knowledge is accumulated per battle: a player mon appears in the
 * embed only once the mod has revealed it (it took the field), and its last
 * revealed data is retained for the rest of the battle.
 */

import { DiscordGateway } from "./gateway.js";
import {
  createChannelMessage,
  createFollowup,
  DiscordRestError,
  editChannelMessage,
  editWebhookMessage,
  interactionCallback,
  registerCommands,
} from "./rest.js";
import type { BattleSnapshot, BotEvents, SnapshotMon } from "./battleLinkTypes.js";
import { actionLabel, infoEmbed, pollEmbed, pollMessage } from "./battleLinkPresentation.js";

export type {
  BattleLinkDecision,
  BattleSnapshot,
  BotEvents,
  LegalAction,
  SnapshotMon,
  SnapshotMove,
} from "./battleLinkTypes.js";

// Interaction tokens die after 15 minutes; leave headroom before we stop
// using one as a message-delivery fallback.
const INTERACTION_TOKEN_TTL_MS = 14 * 60 * 1000;

type Widget =
  | { kind: "channel"; channelId: string; messageId: string }
  | { kind: "webhook"; token: string; messageId: string; at: number };

type Session = {
  channelId: string | null;
  interactionToken: string;
  interactionAt: number;
};

type PendingPoll = {
  snapshot: BattleSnapshot;
  decided: boolean;
};

export class DiscordBattleLinkBot {
  private gateway: DiscordGateway | null = null;
  private applicationId: string | null = null;
  private commandsRegistered = false;
  private session: Session | null = null;
  private activeBattleId: string | null = null;
  private pendingPoll: PendingPoll | null = null;
  /** The one message this session edits in place. */
  private widget: Widget | null = null;
  /** The poll whose content the widget currently shows. */
  private displayed: PendingPoll | null = null;
  /** Serializes widget edits so turns can't render out of order. */
  private queue: Promise<void> = Promise.resolve();
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
    this.widget = null;
    this.displayed = null;
    this.queue = Promise.resolve();
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

  // ---- Emulator-side events (posted by the mod core over HTTP) -------------

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
    this.pendingPoll = { snapshot, decided: false };
    if (this.session) this.schedulePoll(this.pendingPoll);
  }

  handleCancel(detail: { battleId?: unknown; turn?: unknown; attempt?: unknown; reason?: unknown }): void {
    const poll = this.matchPoll(detail);
    if (!poll) return;
    this.pendingPoll = null;
    this.finalizePoll(poll, `CANCELLED (${String(detail.reason || "cancelled")})`);
  }

  handleResolved(detail: { battleId?: unknown; turn?: unknown; attempt?: unknown; code?: unknown; source?: unknown }): void {
    const poll = this.matchPoll(detail);
    this.pendingPoll = null;
    if (!poll) return;
    // A widget click already rewrote the message as CHOSEN, and the emulator
    // then consumes it through the decision long-poll (source "remote").
    // Only a fallback that overrode the click (deadline race → random) still
    // needs an edit here.
    if (poll.decided && !String(detail.source ?? "").startsWith("random")) return;
    const action = poll.snapshot.legalActions.find((candidate) => candidate.code === detail.code);
    const label = action ? actionLabel(action, poll.snapshot) : `ACTION ${String(detail.code)}`;
    this.finalizePoll(poll, `RESOLVED: ${label} (${String(detail.source || "unknown")})`);
  }

  handleBattleEnd(detail: { battleId?: unknown; reason?: unknown }): void {
    if (this.activeBattleId && detail.battleId && detail.battleId !== this.activeBattleId) return;
    const hadSession = this.session !== null;
    this.pendingPoll = null;
    this.activeBattleId = null;
    this.knownOpponent.clear();
    if (hadSession) {
      // Close out whatever the widget is showing; a dead session must not
      // leave live-looking components behind.
      const shown = this.displayed;
      this.enqueue(async () => {
        if (shown) {
          await this.editWidget({
            embeds: [pollEmbed(shown.snapshot, this.knownOpponent, "BATTLE ENDED — DISCONNECTED")],
            components: [],
          });
        }
        this.widget = null;
        this.displayed = null;
      });
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
      if (!this.activeBattleId && !this.pendingPoll) {
        // An unacknowledged command renders as "This interaction failed" in
        // the client; decline out loud instead.
        this.emit("connect-ignored");
        await interactionCallback(interaction.id, interaction.token, {
          type: 4, // CHANNEL_MESSAGE_WITH_SOURCE
          data: {
            embeds: [infoEmbed("No active battle. Start a trainer battle, then /connect again.")],
            flags: 64, // ephemeral
          },
        });
        return;
      }
      const channelId = this.channelIdOf(interaction);
      const poll = this.pendingPoll;
      const showPoll = poll !== null && !poll.decided;
      await interactionCallback(interaction.id, interaction.token, {
        type: 4,
        data: showPoll
          ? pollMessage(poll.snapshot, this.knownOpponent)
          : { embeds: [infoEmbed("Connected. Waiting for the next decision…")] },
      });
      // Only a delivered response becomes the session widget; a failed
      // callback (stale replay) must not bind us to a dead token.
      this.session = { channelId, interactionToken: interaction.token, interactionAt: Date.now() };
      this.widget = { kind: "webhook", token: interaction.token, messageId: "@original", at: Date.now() };
      this.displayed = showPoll ? poll : null;
      this.emit("connected", { channelId });
    } else if (name === "disconnect") {
      if (!this.session) {
        this.emit("disconnect-ignored");
        await interactionCallback(interaction.id, interaction.token, {
          type: 4,
          data: { embeds: [infoEmbed("Not connected.")], flags: 64 },
        });
        return;
      }
      this.session = null;
      const shown = this.displayed;
      this.enqueue(async () => {
        if (shown) {
          await this.editWidget({
            embeds: [pollEmbed(shown.snapshot, this.knownOpponent, "DISCONNECTED")],
            components: [],
          });
        }
        this.widget = null;
        this.displayed = null;
      });
      await interactionCallback(interaction.id, interaction.token, {
        type: 4,
        data: { embeds: [infoEmbed("Disconnected from the battle.")], flags: 64 }, // ephemeral
      });
      this.emit("disconnected");
    }
  }

  private async onComponent(interaction: any): Promise<void> {
    const customId = String(interaction?.data?.custom_id || "");
    const parts = customId.split("|");
    const kind = parts[0];
    if (kind !== "bl" && kind !== "bls") return;
    // Ack before any other work: the 3-second callback window is the whole
    // budget on a phone network, and a late ack renders as "This interaction
    // failed" even when the decision goes through.
    let acked = true;
    try {
      await interactionCallback(interaction.id, interaction.token, { type: 6 }); // DEFERRED_UPDATE_MESSAGE
    } catch (error) {
      // Stale replay after a gateway resume, or network flake: the click is
      // still a decision - only this token is unusable for edits.
      acked = false;
      this.emit("interaction-ack-failed", { message: String(error) });
    }
    if (this.session && acked) {
      // Any acknowledged interaction refreshes the webhook fallback token.
      this.session.interactionToken = interaction.token;
      this.session.interactionAt = Date.now();
    }
    const [, battleId, turnText, attemptText] = parts;
    const code = kind === "bl" ? Number(parts[4]) : Number(interaction?.data?.values?.[0]);
    const poll = this.matchPoll({ battleId, turn: Number(turnText), attempt: Number(attemptText) });
    if (!poll || poll.decided || !Number.isInteger(code)) return; // stale click, already acked
    const action = poll.snapshot.legalActions.find((candidate) => candidate.code === code);
    if (!action) return;
    poll.decided = true;
    this.events.sendDecision({
      battleId: poll.snapshot.battleId,
      turn: poll.snapshot.turn,
      attempt: poll.snapshot.attempt,
      action: code,
    });
    this.emit("decision", { code });
    this.enqueue(async () => {
      if (acked && this.widget?.kind === "webhook") {
        // Chain the widget onto the click's token: after a deferred update
        // ack, @original is the component's own message, and every click
        // buys another 15-minute editing window.
        this.widget = { kind: "webhook", token: interaction.token, messageId: "@original", at: Date.now() };
      }
      if (this.displayed === poll) {
        await this.editWidget({
          embeds: [pollEmbed(poll.snapshot, this.knownOpponent, `CHOSEN: ${actionLabel(action, poll.snapshot)}`)],
          components: [],
        });
      }
    });
  }

  // ---- Message delivery -----------------------------------------------------

  private webhookTokenFresh(): boolean {
    return this.session !== null && Date.now() - this.session.interactionAt < INTERACTION_TOKEN_TTL_MS;
  }

  private enqueue(task: () => Promise<void>): void {
    this.queue = this.queue.then(async () => {
      try {
        await task();
      } catch (error) {
        this.emit("widget-op-failed", { message: String(error) });
      }
    });
  }

  /** Render a new decision poll onto the widget (creating it if needed). */
  private schedulePoll(poll: PendingPoll): void {
    this.enqueue(async () => {
      // Superseded before it reached the front of the queue.
      if (poll !== this.pendingPoll || poll.decided) return;
      const payload = pollMessage(poll.snapshot, this.knownOpponent);
      if ((await this.editWidget(payload)) || (await this.createWidget(payload))) {
        this.displayed = poll;
      } else {
        this.emit("poll-post-failed", { battleId: poll.snapshot.battleId, turn: poll.snapshot.turn });
      }
    });
  }

  /** Close out a poll's components with a footer, if the widget shows it. */
  private finalizePoll(poll: PendingPoll, footer: string): void {
    this.enqueue(async () => {
      if (this.displayed !== poll) return;
      await this.editWidget({
        embeds: [pollEmbed(poll.snapshot, this.knownOpponent, footer)],
        components: [],
      });
    });
  }

  private async editWidget(payload: unknown): Promise<boolean> {
    const widget = this.widget;
    if (!widget || !this.applicationId) return false;
    if (widget.kind === "webhook" && Date.now() - widget.at >= INTERACTION_TOKEN_TTL_MS) return false;
    try {
      if (widget.kind === "channel") {
        await editChannelMessage(this.token, widget.channelId, widget.messageId, payload);
      } else {
        await editWebhookMessage(this.applicationId, widget.token, widget.messageId, payload);
      }
      return true;
    } catch (error) {
      this.emit("widget-edit-failed", { message: String(error) });
      return false;
    }
  }

  private async createWidget(payload: unknown): Promise<boolean> {
    const session = this.session;
    if (!session || !this.applicationId) return false;
    if (session.channelId) {
      try {
        const message = await createChannelMessage(this.token, session.channelId, payload);
        this.widget = { kind: "channel", channelId: session.channelId, messageId: message.id };
        return true;
      } catch (error) {
        this.emit("channel-post-failed", { message: String(error) });
        // A user-installed app has no channel access; that never heals
        // mid-session, so stop burning a round trip on it every turn.
        if (error instanceof DiscordRestError && (error.status === 403 || error.status === 404)) {
          session.channelId = null;
        }
      }
    }
    if (this.webhookTokenFresh()) {
      try {
        const message = await createFollowup(this.applicationId, session.interactionToken, payload);
        this.widget = {
          kind: "webhook",
          token: session.interactionToken,
          messageId: message.id,
          at: session.interactionAt,
        };
        return true;
      } catch (error) {
        this.emit("followup-post-failed", { message: String(error) });
      }
    }
    return false;
  }
}
