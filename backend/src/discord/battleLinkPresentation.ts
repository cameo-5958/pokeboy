import type { BattleSnapshot, LegalAction, SnapshotMon } from "./battleLinkTypes.js";

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

export function actionLabel(action: LegalAction, snapshot: BattleSnapshot): string {
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

export function infoEmbed(text: string) {
  return { title: "BATTLE LINK", description: text, color: EMBED_COLOR };
}

export function pollEmbed(
  snapshot: BattleSnapshot,
  knownOpponent: ReadonlyMap<number, SnapshotMon>,
  footer?: string,
) {
  const trainerParty = snapshot.trainer.party.map(
    (mon, index) => `${index + 1}. ${monLine(mon)}`,
  ).join("\n");
  const opponentParty = snapshot.opponent.party.map((mon, index) => {
    const known = typeof mon.slot === "number" ? knownOpponent.get(mon.slot) : undefined;
    return `${index + 1}. ${known ? monLine(known) : "???"}`;
  }).join("\n");
  const faint = snapshot.phase === "faint-switch";
  return {
    title: faint
      ? `BATTLE LINK — SEND OUT NEXT POKÉMON`
      : `BATTLE LINK — TURN ${snapshot.turn}${snapshot.attempt ? ` · RETRY ${snapshot.attempt}` : ""}`,
    color: footer ? EMBED_COLOR_DONE : EMBED_COLOR,
    description: `${faint ? `${snapshot.trainer.active.nickname || "Your Pokémon"} fainted` : `Trainer class ${snapshot.trainer.class}`} · decide within ${Math.round(snapshot.timeoutMs / 1000)}s`,
    fields: [
      { name: "YOUR ACTIVE", value: activeDetail(snapshot.trainer.active), inline: true },
      { name: "OPPONENT ACTIVE", value: activeDetail(snapshot.opponent.active), inline: true },
      { name: "YOUR PARTY", value: trainerParty || "—", inline: false },
      { name: "OPPONENT PARTY (REVEALED)", value: opponentParty || "—", inline: false },
    ],
    footer: { text: footer || `battle ${snapshot.battleId.slice(0, 8)}` },
  };
}

export function pollComponents(snapshot: BattleSnapshot) {
  const key = `${snapshot.battleId}|${snapshot.turn}|${snapshot.attempt}`;
  const moves = snapshot.legalActions.filter((action) => action.type === "move");
  const switches = snapshot.legalActions.filter((action) => action.type === "switch");
  const items = snapshot.legalActions.filter((action) => action.type === "item");
  const rows: unknown[] = [];
  if (moves.length) {
    rows.push({
      type: 1,
      components: moves.slice(0, 5).map((action) => ({
        type: 2, style: 1,
        label: actionLabel(action, snapshot).slice(0, 80),
        custom_id: `bl|${key}|${action.code}`,
      })),
    });
  }
  if (switches.length) {
    rows.push({
      type: 1,
      components: [{
        type: 3,
        custom_id: `bls|${key}`,
        placeholder: snapshot.phase === "faint-switch" ? "SEND OUT POKÉMON…" : "SWITCH POKÉMON…",
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
        type: 2, style: 2,
        label: actionLabel(action, snapshot).slice(0, 80),
        custom_id: `bl|${key}|${action.code}`,
      })),
    });
  }
  return rows;
}

export function pollMessage(snapshot: BattleSnapshot, knownOpponent: ReadonlyMap<number, SnapshotMon>) {
  return {
    embeds: [pollEmbed(snapshot, knownOpponent)],
    components: pollComponents(snapshot),
  };
}
