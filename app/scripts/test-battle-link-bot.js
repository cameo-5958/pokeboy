// Discord Battle Link bot: single-widget delivery, ack-first interactions.
//
// Loads the backend's discord/battleLink.ts (sucrase TS->CJS) with ./rest.js
// and ./gateway.js mocked, then drives the emulator- and Discord-side events
// the way the telemetry showed them arriving on device: user-install context
// (channel posts 403), stale interaction replays (callback 404), multi-turn
// battles. The bot lives on the backend now; this harness stays here with
// the other Battle Link test tooling.

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { transform } = require("sucrase");

// ---- ./rest mock ------------------------------------------------------------

class DiscordRestError extends Error {
  constructor(status, body, restPath) {
    super(`Discord REST ${status} on ${restPath}: ${body}`);
    this.name = "DiscordRestError";
    this.status = status;
    this.body = body;
  }
}

const calls = [];
let messageSeq = 0;
const behavior = {
  interactionCallbackError: null,
  channelPostError: null,
  webhookEditError: null,
};

const restMock = {
  DiscordRestError,
  registerCommands: async () => {
    calls.push(["registerCommands"]);
    return null;
  },
  interactionCallback: async (id, token, payload) => {
    calls.push(["interactionCallback", { id, token, payload }]);
    if (behavior.interactionCallbackError) throw behavior.interactionCallbackError;
    return null;
  },
  createChannelMessage: async (token, channelId, payload) => {
    calls.push(["createChannelMessage", { channelId, payload }]);
    if (behavior.channelPostError) throw behavior.channelPostError;
    messageSeq += 1;
    return { id: `m${messageSeq}` };
  },
  editChannelMessage: async (token, channelId, messageId, payload) => {
    calls.push(["editChannelMessage", { channelId, messageId, payload }]);
    return null;
  },
  createFollowup: async (applicationId, interactionToken, payload) => {
    calls.push(["createFollowup", { interactionToken, payload }]);
    messageSeq += 1;
    return { id: `m${messageSeq}` };
  },
  editWebhookMessage: async (applicationId, interactionToken, messageId, payload) => {
    calls.push(["editWebhookMessage", { interactionToken, messageId, payload }]);
    if (behavior.webhookEditError) throw behavior.webhookEditError;
    return null;
  },
};

// ---- ./gateway mock ---------------------------------------------------------

let gatewayEvents = null;
class DiscordGateway {
  constructor(token, events) {
    gatewayEvents = events;
  }
  start() {
    gatewayEvents.onReady("app1");
  }
  stop() {}
}

// ---- Load Battle Link modules ----------------------------------------------

function loadTsModule(filename, requireModule) {
  const source = fs.readFileSync(
    path.join(path.dirname(module.filename), "../../backend/src/discord", filename),
    "utf8",
  );
  const { code } = transform(source, { transforms: ["typescript", "imports"] });
  const moduleExports = {};
  const context = {
    Date,
    Math,
    Promise,
    console,
    exports: moduleExports,
    module: { exports: moduleExports },
    require: requireModule,
  };
  vm.createContext(context);
  vm.runInContext(code, context, { filename });
  return context.module.exports;
}

const presentation = loadTsModule("battleLinkPresentation.ts", (name) => {
  throw new Error(`unexpected presentation import: ${name}`);
});
const { DiscordBattleLinkBot } = loadTsModule(
  "battleLink.ts",
  (name) => {
    if (name === "./rest.js") return restMock;
    if (name === "./gateway.js") return { DiscordGateway };
    if (name === "./battleLinkPresentation.js") return presentation;
    throw new Error(`unexpected import: ${name}`);
  },
);

// ---- Fixtures ---------------------------------------------------------------

const mon = (slot, nickname, hp) => ({
  slot,
  species: 1,
  nickname,
  hp,
  maxHp: 20,
  level: 10,
  status: 0,
  moves: [{ slot: 0, move: 33, current: 10, ppUps: 0 }],
});

const snapshot = (turn, attempt = 0) => ({
  protocol: 1,
  battleId: "battle-1",
  turn,
  attempt,
  timeoutMs: 30000,
  trainer: {
    class: 0x22,
    party: [mon(0, "GEODUDE", 20), mon(1, "ONIX", 20)],
    active: { ...mon(0, "GEODUDE", 20), partySlot: 0 },
  },
  opponent: {
    party: [mon(0, "PIKACHU", 20)],
    active: { ...mon(0, "PIKACHU", 20), partySlot: 0 },
  },
  legalActions: [
    { code: 0, type: "move", slot: 0, move: 33, current: 10 },
    { code: 17, type: "switch", partySlot: 1 },
  ],
});

const settle = () => new Promise((resolve) => setImmediate(resolve));
const drain = async () => {
  for (let i = 0; i < 10; i += 1) await settle();
};
const callsOf = (kind) => calls.filter((call) => call[0] === kind);

const decisions = [];
const events = [];
const bot = new DiscordBattleLinkBot("bot-token", {
  sendDecision: (decision) => decisions.push(decision),
  onEvent: (kind, detail) => events.push([kind, detail]),
});
bot.start();

async function run() {
  // /connect with no battle: acknowledged ephemerally, no session.
  await gatewayEvents.onInteraction({ type: 2, id: "i0", token: "t0", channel_id: "chan1", data: { name: "connect" } });
  await drain();
  const declined = callsOf("interactionCallback").at(-1)[1];
  assert.equal(declined.payload.type, 4, "no-battle /connect is acknowledged");
  assert.equal(declined.payload.data.flags, 64, "no-battle /connect reply is ephemeral");
  assert.equal(bot.connected, false, "no-battle /connect does not open a session");

  // A pending decision, then /connect: the response is the poll widget.
  bot.handleRequest(snapshot(0));
  await gatewayEvents.onInteraction({ type: 2, id: "i1", token: "t1", channel_id: "chan1", data: { name: "connect" } });
  await drain();
  assert.equal(bot.connected, true, "/connect during a battle opens the session");
  const connectResponse = callsOf("interactionCallback").at(-1)[1];
  assert.equal(connectResponse.payload.type, 4);
  assert.ok(connectResponse.payload.data.components.length > 0, "poll widget carries components");
  assert.ok(
    connectResponse.payload.data.components.some((row) => row.components?.[0]?.type === 3),
    "switch select menu is offered",
  );

  // Component click: ack lands before the decision is delivered, the widget
  // is edited via the click's own token (@original), and no new message
  // appears anywhere.
  calls.length = 0;
  await gatewayEvents.onInteraction({ type: 3, id: "i2", token: "t2", data: { custom_id: "bl|battle-1|0|0|0" } });
  await drain();
  assert.equal(calls[0][0], "interactionCallback", "click is acknowledged first");
  assert.equal(calls[0][1].payload.type, 6, "click ack is a deferred update");
  assert.deepEqual(
    { ...decisions.at(-1) },
    { battleId: "battle-1", turn: 0, attempt: 0, action: 0 },
    "click delivers the decision",
  );
  const chosenEdit = callsOf("editWebhookMessage").at(-1)[1];
  assert.equal(chosenEdit.interactionToken, "t2", "widget chains onto the click token");
  assert.equal(chosenEdit.messageId, "@original");
  assert.match(chosenEdit.payload.embeds[0].footer.text, /^CHOSEN:/);
  assert.equal(callsOf("createChannelMessage").length, 0, "no extra message for the click");
  assert.equal(callsOf("createFollowup").length, 0);

  // The emulator consumes the click through the decision long-poll and
  // reports it back as source "remote": the CHOSEN edit stands, no second
  // edit happens.
  calls.length = 0;
  bot.handleResolved({ battleId: "battle-1", turn: 0, attempt: 0, code: 0, source: "remote" });
  await drain();
  assert.equal(calls.length, 0, "a clicked-and-consumed resolution does not edit again");

  // Next turn: the SAME widget is edited in place - no new message.
  calls.length = 0;
  bot.handleRequest(snapshot(1));
  await drain();
  const turnEdit = callsOf("editWebhookMessage").at(-1)[1];
  assert.equal(turnEdit.interactionToken, "t2", "next turn reuses the chained token");
  assert.equal(turnEdit.messageId, "@original");
  assert.ok(turnEdit.payload.components.length > 0, "next turn re-arms the components");
  assert.equal(callsOf("createChannelMessage").length, 0, "next turn creates no channel message");
  assert.equal(callsOf("createFollowup").length, 0, "next turn creates no followup");

  // Ack failure (stale replay after a gateway resume): the decision still
  // counts, and the dead token is not chained.
  calls.length = 0;
  behavior.interactionCallbackError = new DiscordRestError(404, '{"code": 10062}', "/interactions");
  await gatewayEvents.onInteraction({ type: 3, id: "i3", token: "t3", data: { custom_id: "bl|battle-1|1|0|0" } });
  behavior.interactionCallbackError = null;
  await drain();
  assert.deepEqual(
    { ...decisions.at(-1) },
    { battleId: "battle-1", turn: 1, attempt: 0, action: 0 },
    "decision is delivered even when the ack fails",
  );
  const failedAckEdit = callsOf("editWebhookMessage").at(-1)[1];
  assert.equal(failedAckEdit.interactionToken, "t2", "dead token is not chained");
  bot.handleResolved({ battleId: "battle-1", turn: 1, attempt: 0, code: 0, source: "remote" });
  await drain();

  // Widget edit fails (token aged out server-side): fall back to creating a
  // message - channel first, and a 403 there disables channel mode for good.
  calls.length = 0;
  behavior.webhookEditError = new DiscordRestError(404, '{"code": 10015}', "/webhooks");
  behavior.channelPostError = new DiscordRestError(403, '{"code": 50001}', "/channels");
  bot.handleRequest(snapshot(2));
  await drain();
  assert.equal(callsOf("createChannelMessage").length, 1, "channel post is attempted once");
  assert.equal(callsOf("createFollowup").length, 1, "falls back to a webhook followup");
  assert.equal(callsOf("createFollowup")[0][1].interactionToken, "t2", "followup uses the freshest acked token");

  calls.length = 0;
  bot.handleRequest(snapshot(3));
  await drain();
  assert.equal(callsOf("createChannelMessage").length, 0, "403 channel is never retried");
  assert.equal(callsOf("createFollowup").length, 1, "turn 3 replaces the dead widget");
  behavior.webhookEditError = null;
  behavior.channelPostError = null;

  // The new followup widget (real message id) is edited on the next turn.
  calls.length = 0;
  bot.handleRequest(snapshot(4));
  await drain();
  const followupEdit = callsOf("editWebhookMessage").at(-1)[1];
  assert.match(followupEdit.messageId, /^m\d+$/, "followup widget is edited by message id");
  assert.equal(callsOf("createFollowup").length, 0);

  // Battle end: the widget is closed out in place and the session drops.
  calls.length = 0;
  bot.handleBattleEnd({ battleId: "battle-1", reason: "battle-over" });
  await drain();
  const endEdit = callsOf("editWebhookMessage").at(-1)[1];
  assert.match(endEdit.payload.embeds[0].footer.text, /BATTLE ENDED/);
  assert.equal(endEdit.payload.components.length, 0, "battle end disarms the components");
  assert.equal(callsOf("createChannelMessage").length, 0, "battle end posts no extra message");
  assert.equal(callsOf("createFollowup").length, 0);
  assert.equal(bot.connected, false, "battle end auto-disconnects");

  console.log("Battle Link Discord bot tests passed");
}

run().catch((error) => {
  console.error(error);
  process.exit(1);
});
