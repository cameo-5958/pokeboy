"""schema_v1 battle state - the frozen interface contract (ai/SPECS.md §1.4).

Action space (§1.2): 0-3 move slots, 4-8 switch to bench order slots 2-6,
9 pass / forced-continue / struggle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCHEMA_V = 1

ACTION_MOVE_BASE = 0  # actions 0..3
ACTION_SWITCH_BASE = 4  # actions 4..8
ACTION_PASS = 9


@dataclass
class State:
    battle_id: str
    turn: int
    request_kind: str  # "turn" | "force_switch" | "wait"
    my_side: dict[str, Any]
    opp_side: dict[str, Any]
    legal_actions: list[int]
    history_tail: list[dict[str, Any]] = field(default_factory=list)
    schema_v: int = SCHEMA_V

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_v": self.schema_v,
            "battle_id": self.battle_id,
            "turn": self.turn,
            "request_kind": self.request_kind,
            "my_side": self.my_side,
            "opp_side": self.opp_side,
            "legal_actions": self.legal_actions,
            "history_tail": self.history_tail,
        }
