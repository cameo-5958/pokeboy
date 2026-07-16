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
  /** "turn" for a whole-turn decision, "faint-switch" for a forced send-out. */
  phase?: string;
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
