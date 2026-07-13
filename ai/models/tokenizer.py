"""Field–value tokenizer over schema_v1 states (ai/SPECS.md §4.1).

embedding(token) = E_field[field_id] + E_value[value_id] + E_slot[slot_id]
                   + W_cont · cont(token)

This module produces the id/continuous arrays; embeddings live in the model.
Layout (stateless snapshot; history segment is added in a later phase):

  [GLB]                                    1 token   (turn, request kind flags)
  MY_ACTIVE   SPECIES STATUS HP  MOVE×4    7 tokens
  MY_BENCH_i  same                         7×5
  OPP_ACTIVE  SPECIES STATUS HP  MOVE×4    7
  OPP_BENCH_i same                         7×5
  = 85 tokens, padded to SEQ_LEN.

Engineered continuous features on MY_ACTIVE move tokens: gen1 type-effect
multiplier vs opp active (scaled /4) and STAB flag — public info only.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from sim.gen1data import MOVES, SPECIES, TYPE_CHART
from sim.pack import max_pp

SEQ_LEN = 128
HIST_SEQ_LEN = 256  # 85 stateless + up to 100 history tokens (SPECS §4.1)

PAD, NONE, UNREVEALED = "PAD", "NONE", "UNREVEALED"
STATUSES = ["OK", "BRN", "PAR", "PSN", "TOX", "SLP", "FRZ"]
HP_BUCKETS = 16
PP_BUCKETS = ["PP_FULL", "PP_HIGH", "PP_LOW", "PP_ZERO"]

FIELDS = ["PAD", "GLB", "SPECIES", "STATUS", "HP", "MOVE"]
HIST_FIELDS = ["HIST_MY_ACTION", "HIST_OPP_ACTION", "HIST_DMG_ME", "HIST_DMG_OPP", "HIST_EVENT"]
SLOTS = (
    ["PAD", "GLOBAL", "MY_ACTIVE"]
    + [f"MY_BENCH_{i}" for i in range(1, 6)]
    + ["OPP_ACTIVE"]
    + [f"OPP_BENCH_{i}" for i in range(1, 6)]
)

CONT_CHANNELS = ["hp_frac", "pp_frac", "turn", "fainted", "type_eff", "stab", "force_switch"]

# history event flags, highest-priority first — one EV_* token per turn
EV_PRIORITY = ["ft", "crit", "se", "st", "sc", "miss", "re", "im"]


def _build_value_vocab(hist: bool = False) -> list[str]:
    vocab = [PAD, NONE, UNREVEALED]
    vocab += [f"SPECIES:{s}" for s in SPECIES]
    vocab += [f"MOVE:{m}" for m in MOVES]
    vocab += [f"STATUS:{s}" for s in STATUSES]
    vocab += [f"HP_B{i}" for i in range(HP_BUCKETS)]
    vocab += PP_BUCKETS
    if hist:  # appended after the legacy vocab so v1 ids stay a prefix
        vocab += ["PASS", "UNKNOWN"]
        vocab += [f"DMG_B{i}" for i in range(9)]
        vocab += [f"EV_{e}" for e in EV_PRIORITY] + ["EV_NONE"]
    return vocab


class Tokenizer:
    def __init__(self, seq_len: int | None = None, hist_k: int = 0):
        self.hist_k = hist_k
        self.seq_len = seq_len if seq_len is not None else (HIST_SEQ_LEN if hist_k else SEQ_LEN)
        self._values = _build_value_vocab(hist=bool(hist_k))
        fields = FIELDS + (HIST_FIELDS if hist_k else [])
        slots = SLOTS + [f"HIST_-{i}" for i in range(1, hist_k + 1)]
        self._value_ix = {v: i for i, v in enumerate(self._values)}
        self._field_ix = {f: i for i, f in enumerate(fields)}
        self._slot_ix = {s: i for i, s in enumerate(slots)}
        self._cont_ix = {c: i for i, c in enumerate(CONT_CHANNELS)}

    # -- vocab introspection --

    @property
    def n_values(self) -> int:
        return len(self._values)

    @property
    def n_fields(self) -> int:
        return len(self._field_ix)

    @property
    def n_slots(self) -> int:
        return len(self._slot_ix)

    @property
    def n_cont(self) -> int:
        return len(CONT_CHANNELS)

    def value_id(self, name: str) -> int:
        return self._value_ix[name]

    def field_id(self, name: str) -> int:
        return self._field_ix[name]

    def slot_id(self, name: str) -> int:
        return self._slot_ix[name]

    def cont_channel(self, name: str) -> int:
        return self._cont_ix[name]

    # -- encoding --

    def encode(self, state: dict[str, Any]) -> dict[str, np.ndarray | int]:
        fields, values, slots = [], [], []
        cont: list[np.ndarray] = []

        def emit(field: str, value: str, slot: str, **channels: float) -> None:
            fields.append(self._field_ix[field])
            values.append(self._value_ix[value])
            slots.append(self._slot_ix[slot])
            c = np.zeros(len(CONT_CHANNELS), dtype=np.float32)
            for k, v in channels.items():
                c[self._cont_ix[k]] = v
            cont.append(c)

        emit(
            "GLB",
            NONE,
            "GLOBAL",
            turn=min(state.get("turn", 0), 200) / 200.0,
            force_switch=1.0 if state.get("request_kind") == "force_switch" else 0.0,
        )

        opp_active_species = self._active_species(state["opp_side"])
        my_active_species = self._active_species(state["my_side"])

        self._emit_side(emit, state["my_side"], "MY", mine=True, opp_species=opp_active_species)
        self._emit_side(emit, state["opp_side"], "OPP", mine=False, opp_species=my_active_species)

        if self.hist_k:
            for entry in state.get("history_tail", [])[: self.hist_k]:
                self._emit_hist(emit, entry)

        n = len(fields)
        if n > self.seq_len:
            raise ValueError(f"sequence {n} exceeds {self.seq_len}")
        pad = self.seq_len - n
        return {
            "field_ids": np.asarray(fields + [0] * pad, dtype=np.int16),
            "value_ids": np.asarray(values + [0] * pad, dtype=np.int16),
            "slot_ids": np.asarray(slots + [0] * pad, dtype=np.int16),
            "cont": np.vstack(cont + [np.zeros((pad, len(CONT_CHANNELS)), dtype=np.float32)]),
            "length": n,
        }

    def _hist_value(self, name: str) -> str:
        return name if name in self._value_ix else "UNKNOWN"

    def _emit_hist(self, emit, entry: dict[str, Any]) -> None:
        o = entry.get("o", -1)
        if not -self.hist_k <= o <= -1:
            return
        slot = f"HIST_{o}"

        def action_value(a: str | None) -> str:
            if a is None:
                return "UNKNOWN"
            if a == "P":
                return "PASS"
            kind, _, name = a.partition(":")
            return self._hist_value(("MOVE:" if kind == "M" else "SPECIES:") + name)

        def dmg_value(d: int | None) -> str:
            return "UNKNOWN" if d is None else f"DMG_B{min(int(d), 8)}"

        ev = next((f"EV_{e}" for e in EV_PRIORITY if e in entry.get("ev", [])), "EV_NONE")
        emit("HIST_MY_ACTION", action_value(entry.get("my")), slot)
        emit("HIST_OPP_ACTION", action_value(entry.get("op")), slot)
        emit("HIST_DMG_ME", dmg_value(entry.get("dm")), slot)
        emit("HIST_DMG_OPP", dmg_value(entry.get("do")), slot)
        emit("HIST_EVENT", ev, slot)

    @staticmethod
    def _active_species(side: dict[str, Any]) -> str | None:
        mons = side["pokemon"]
        ix = side.get("active_ix", 0)
        return mons[ix]["species"] if 0 <= ix < len(mons) else None

    def _emit_side(self, emit, side: dict[str, Any], prefix: str, mine: bool, opp_species) -> None:
        mons = list(side["pokemon"])
        ix = side.get("active_ix", 0)
        if 0 <= ix < len(mons):
            mons = [mons[ix]] + mons[:ix] + mons[ix + 1 :]
        for i, mon in enumerate(mons[:6]):
            slot = f"{prefix}_ACTIVE" if i == 0 else f"{prefix}_BENCH_{i}"
            self._emit_mon(emit, mon, slot, mine=mine, opp_species=opp_species if i == 0 else None)

    def _emit_mon(self, emit, mon: dict[str, Any], slot: str, mine: bool, opp_species) -> None:
        species = mon.get("species")
        if species is None:
            emit("SPECIES", UNREVEALED, slot)
            emit("STATUS", NONE, slot)
            emit("HP", NONE, slot)
            for _ in range(4):
                emit("MOVE", UNREVEALED, slot)
            return

        emit("SPECIES", f"SPECIES:{species}", slot, fainted=1.0 if mon.get("fainted") else 0.0)
        status = mon.get("status") or "OK"
        emit("STATUS", f"STATUS:{status if status in STATUSES else 'OK'}", slot)
        hp = mon.get("hp_fraction")
        if hp is None:
            hp = mon.get("hp", 0) / mon["max_hp"] if mon.get("max_hp") else 0.0
        bucket = min(HP_BUCKETS - 1, int(hp * HP_BUCKETS))
        emit("HP", f"HP_B{bucket}", slot, hp_frac=float(hp))

        moves = mon.get("moves") if mine else mon.get("revealed_moves")
        moves = list(moves or [])
        # showdown-converted my_side rows store dicts; metamon stores names
        names = [m["id"] if isinstance(m, dict) else m for m in moves]
        pps = mon.get("pp")
        for slot_ix in range(4):
            if slot_ix < len(names):
                name = names[slot_ix]
                channels: dict[str, float] = {}
                if pps is not None and slot_ix < len(pps):
                    frac = pps[slot_ix] / max(1, max_pp(name))
                    channels["pp_frac"] = min(1.0, frac)
                elif isinstance(moves[slot_ix], dict):
                    frac = moves[slot_ix].get("pp", 0) / max(1, max_pp(name))
                    channels["pp_frac"] = min(1.0, frac)
                if mine and slot == "MY_ACTIVE" and opp_species is not None:
                    channels["type_eff"] = self._effectiveness(name, opp_species) / 4.0
                    channels["stab"] = 1.0 if self._is_stab(name, mon["species"]) else 0.0
                emit("MOVE", f"MOVE:{name}", slot, **channels)
            else:
                emit("MOVE", NONE if mine else UNREVEALED, slot)

    @staticmethod
    def _effectiveness(move: str, defender_species: str) -> float:
        mtype = MOVES[move][4]
        _, _, _, _, _, _, t1, t2 = SPECIES[defender_species]
        eff = TYPE_CHART[mtype][t1]
        if t2 != t1:
            eff *= TYPE_CHART[mtype][t2]
        return eff

    @staticmethod
    def _is_stab(move: str, attacker_species: str) -> bool:
        mtype = MOVES[move][4]
        _, _, _, _, _, _, t1, t2 = SPECIES[attacker_species]
        return mtype in (t1, t2)
