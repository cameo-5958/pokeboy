import { Router, type Response } from "express";

import type { BattleSnapshot } from "../discord/battleLink.js";
import { discordBot, initDiscordBot } from "../discord/manager.js";

const LONG_POLL_MS = 28_000;
const PENDING_TTL_MS = 2 * 60_000;
const DECISION_TTL_MS = 60_000;
const MAX_REQUESTS = 100;

type LegalAction = {
  code: number;
  type: "move" | "item" | "switch";
  [key: string]: unknown;
};

type BattleState = {
  protocol: number;
  battleId: string;
  turn: number;
  attempt: number;
  legalActions: LegalAction[];
  [key: string]: unknown;
};

type DecisionRequest = {
  key: string;
  state: BattleState;
  receivedAt: number;
  waiters: Set<Response>;
  decision?: number;
  decidedAt?: number;
};

const requests = new Map<string, DecisionRequest>();

function requestKey(state: BattleState): string {
  return `${state.battleId}:${state.turn}:${state.attempt}`;
}

/** Settle a request: remember the decision and answer every open long-poll. */
function resolveRequest(request: DecisionRequest, action: number): void {
  request.decision = action;
  request.decidedAt = Date.now();
  for (const waiter of request.waiters) {
    if (!waiter.headersSent) waiter.json({ action });
  }
  request.waiters.clear();
}

/** True when a decision state carries the full snapshot the widget renders. */
function isFullSnapshot(state: BattleState): state is BattleState & BattleSnapshot {
  const trainer = state.trainer as BattleSnapshot["trainer"] | undefined;
  const opponent = state.opponent as BattleSnapshot["opponent"] | undefined;
  return Boolean(
    trainer && Array.isArray(trainer.party) && trainer.active &&
    opponent && Array.isArray(opponent.party) && opponent.active,
  );
}

/**
 * Wires the Discord bot into this decision pipeline. The bot mirrors pending
 * requests as an editable widget message; a component click resolves the
 * request exactly like a console /command. Call once at server start.
 */
export function initBattleLinkDiscord(): void {
  initDiscordBot({
    sendDecision: (decision) => {
      const request = requests.get(`${decision.battleId}:${decision.turn}:${decision.attempt}`);
      if (!request || request.decision !== undefined) return;
      if (!request.state.legalActions.some((candidate) => candidate.code === decision.action)) return;
      resolveRequest(request, decision.action);
    },
    onEvent: (kind, detail) =>
      console.log("battle-link discord", kind, detail === undefined ? "" : JSON.stringify(detail)),
  });
}

function cleanup(now = Date.now()): void {
  for (const [key, request] of requests) {
    const expired = request.decision === undefined
      ? now - request.receivedAt > PENDING_TTL_MS
      : now - (request.decidedAt ?? now) > DECISION_TTL_MS;
    if (!expired) continue;
    for (const waiter of request.waiters) {
      if (!waiter.headersSent) waiter.status(204).end();
    }
    requests.delete(key);
  }
  while (requests.size > MAX_REQUESTS) {
    const oldest = requests.keys().next().value as string | undefined;
    if (!oldest) break;
    requests.delete(oldest);
  }
}

function decodeState(value: unknown): BattleState {
  if (typeof value !== "string" || value.length === 0 || value.length > 64_000) {
    throw new Error("state must be a base64url string");
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(Buffer.from(value, "base64url").toString("utf8"));
  } catch {
    throw new Error("state is not valid base64url JSON");
  }
  if (!parsed || typeof parsed !== "object") throw new Error("state must be an object");
  const state = parsed as Partial<BattleState>;
  if (state.protocol !== 1) throw new Error("unsupported Battle Link protocol");
  if (typeof state.battleId !== "string" || !/^[A-Za-z0-9-]{16,128}$/.test(state.battleId)) {
    throw new Error("battleId is invalid");
  }
  if (!Number.isInteger(state.turn) || (state.turn as number) < 0 ||
      !Number.isInteger(state.attempt) || (state.attempt as number) < 0) {
    throw new Error("turn and attempt must be non-negative integers");
  }
  if (!Array.isArray(state.legalActions) || state.legalActions.length === 0 ||
      state.legalActions.length > 32) {
    throw new Error("legalActions must contain 1..32 actions");
  }
  const codes = new Set<number>();
  for (const action of state.legalActions) {
    if (!action || typeof action !== "object" ||
        !Number.isInteger(action.code) || !["move", "item", "switch"].includes(action.type)) {
      throw new Error("legalActions contains an invalid action");
    }
    if (codes.has(action.code)) throw new Error("legal action codes must be unique");
    codes.add(action.code);
  }
  return state as BattleState;
}

const consoleHtml = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Breadwinner: Battle Link</title><style>
:root{color-scheme:dark;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;background:#11150f;color:#d9e7bd}
body{max-width:960px;margin:0 auto;padding:24px}h1{font-size:22px;margin:0 0 4px}.sub{color:#8ca276;margin:0 0 22px}
.empty,.card{border:1px solid #425037;border-radius:10px;background:#192016;padding:16px;margin:12px 0}.meta{color:#98ad82;font-size:12px}
.match{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:12px 0}.mon{background:#11150f;padding:10px;border-radius:7px}
.actions{display:flex;flex-wrap:wrap;gap:8px}button{font:inherit;color:#11150f;background:#b8d28f;border:0;border-radius:6px;padding:9px 12px;cursor:pointer}
button:hover{background:#d9efb4}button:disabled{opacity:.45;cursor:wait}.error{color:#ff9c9c}@media(max-width:600px){.match{grid-template-columns:1fr}}
</style></head><body><h1>BREADWINNER: BATTLE LINK</h1><p class="sub">Live trainer command console · pokeboy.cameo.moe</p>
<div id="status" class="meta">Connecting…</div><main id="requests"></main><script>
const root=document.getElementById('requests'),status=document.getElementById('status');
const mon=p=>p?((p.nickname||('SPECIES '+p.species))+' · HP '+p.hp+'/'+p.maxHp+' · LV '+p.level):'UNKNOWN';
const actionLabel=a=>a.type==='move'?('MOVE '+(a.slot>=0?a.slot+1:'STRUGGLE')+' · #'+a.move):a.type==='switch'?('SWITCH · SLOT '+(a.partySlot+1)):('ITEM · '+(a.itemName||('#'+a.item)));
async function command(key,action,button){button.disabled=true;try{const r=await fetch('/battle-link/command',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({key,action})});if(!r.ok)throw new Error((await r.json()).error||r.statusText);await refresh()}catch(e){status.textContent=e.message;status.className='meta error'}finally{button.disabled=false}}
function render(items){root.replaceChildren();if(!items.length){const e=document.createElement('div');e.className='empty';e.textContent='Waiting for a trainer decision request…';root.append(e);return}
for(const item of items){const s=item.state,c=document.createElement('section');c.className='card';const h=document.createElement('strong');h.textContent='BATTLE '+s.battleId+' · TURN '+s.turn+' · ATTEMPT '+s.attempt;c.append(h);
const m=document.createElement('div');m.className='meta';m.textContent='Received '+new Date(item.receivedAt).toLocaleTimeString();c.append(m);const match=document.createElement('div');match.className='match';
for(const [label,p] of [['TRAINER',s.trainer&&s.trainer.active],['OPPONENT',s.opponent&&s.opponent.active]]){const d=document.createElement('div');d.className='mon';d.textContent=label+'\n'+mon(p);match.append(d)}c.append(match);
const actions=document.createElement('div');actions.className='actions';for(const a of s.legalActions){const b=document.createElement('button');b.textContent=actionLabel(a);b.onclick=()=>command(item.key,a.code,b);actions.append(b)}c.append(actions);root.append(c)}}
async function refresh(){try{const r=await fetch('/battle-link/pending',{cache:'no-store'});if(!r.ok)throw new Error(r.statusText);const body=await r.json();render(body.requests);status.textContent=body.requests.length?body.requests.length+' decision request(s) waiting':'Connected';status.className='meta'}catch(e){status.textContent='Connection error: '+e.message;status.className='meta error'}}
refresh();setInterval(refresh,1000);
</script></body></html>`;

export const battleLinkRouter = Router();

battleLinkRouter.get("/", (_req, res) => {
  res.type("html").send(consoleHtml);
});

battleLinkRouter.get("/health", (_req, res) => {
  cleanup();
  res.json({ ok: true, pending: [...requests.values()].filter((item) => item.decision === undefined).length });
});

battleLinkRouter.get("/pending", (_req, res) => {
  cleanup();
  res.set("cache-control", "no-store").json({
    requests: [...requests.values()]
      .filter((request) => request.decision === undefined)
      .map(({ key, state, receivedAt }) => ({ key, state, receivedAt })),
  });
});

battleLinkRouter.get("/decision", (req, res) => {
  cleanup();
  let state: BattleState;
  try {
    state = decodeState(req.query.state);
  } catch (error) {
    res.status(400).json({ error: (error as Error).message });
    return;
  }
  const key = requestKey(state);
  let request = requests.get(key);
  if (!request) {
    request = { key, state, receivedAt: Date.now(), waiters: new Set() };
    requests.set(key, request);
    // A brand-new pending decision: hand the snapshot to the Discord bot so
    // it can render (or refresh) the widget. Long-poll re-entries of the same
    // key don't re-notify - the state within one attempt never changes.
    if (isFullSnapshot(state)) discordBot()?.handleRequest(state);
  } else {
    request.state = state;
    request.receivedAt = Date.now();
  }
  res.set({ "cache-control": "no-store", "x-accel-buffering": "no" });
  if (request.decision !== undefined) {
    res.json({ action: request.decision });
    return;
  }
  request.waiters.add(res);
  const timer = setTimeout(() => {
    request?.waiters.delete(res);
    if (!res.headersSent) res.status(204).end();
  }, LONG_POLL_MS);
  res.on("close", () => {
    clearTimeout(timer);
    request?.waiters.delete(res);
  });
});

battleLinkRouter.post("/command", (req, res) => {
  cleanup();
  const key = req.body?.key;
  const action = req.body?.action;
  if (typeof key !== "string" || !Number.isInteger(action)) {
    res.status(400).json({ error: "key and integer action are required" });
    return;
  }
  const request = requests.get(key);
  if (!request) {
    res.status(404).json({ error: "decision request is no longer pending" });
    return;
  }
  if (!request.state.legalActions.some((candidate) => candidate.code === action)) {
    res.status(400).json({ error: "action is not legal for this request" });
    return;
  }
  resolveRequest(request, action);
  res.json({ ok: true, key, action });
});

// Emulator-side lifecycle events (posted fire-and-forget by the mod core).
// They keep the Discord widget honest - cancelled turns lose their buttons,
// resolved turns show what actually happened - and clean up the pending map
// so the web console never shows a decision the emulator already abandoned.
battleLinkRouter.post("/event", (req, res) => {
  cleanup();
  const type = req.body?.type;
  const detail = (req.body?.detail && typeof req.body.detail === "object" ? req.body.detail : {}) as {
    battleId?: unknown; turn?: unknown; attempt?: unknown;
  };
  const bot = discordBot();
  const key = `${String(detail.battleId)}:${String(detail.turn)}:${String(detail.attempt)}`;
  if (type === "cancel") {
    bot?.handleCancel(detail);
    const request = requests.get(key);
    if (request && request.decision === undefined) {
      for (const waiter of request.waiters) {
        if (!waiter.headersSent) waiter.status(204).end();
      }
      requests.delete(key);
    }
  } else if (type === "resolved") {
    bot?.handleResolved(detail);
    requests.delete(key);
  } else if (type === "battle-end") {
    bot?.handleBattleEnd(detail);
  } else if (type === "emulator-reset") {
    bot?.handleEmulatorReset();
  } else {
    res.status(400).json({ error: "unknown event type" });
    return;
  }
  res.json({ ok: true });
});
