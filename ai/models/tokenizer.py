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

With dmg_feats=True (skill-probe remediation), four more channels on
MY_ACTIVE move tokens: dmg_frac (approx. L100 standard-stat damage as a
fraction of the defender's max HP), kills (min roll KOs at current HP),
acc (accuracy/100), wasted (pure status move that must fail — target
already statused or type-immune — or heal at full HP). All computable
from public info; the model previously had to memorize these per move id.
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
DMG_CHANNELS = ["dmg_frac", "kills", "acc", "wasted"]

# gen1 damage model at L100 with standard competitive stats (max DV + stat exp)
_PHYSICAL_TYPES = {"Normal", "Fighting", "Flying", "Ground", "Rock", "Bug", "Ghost", "Poison"}
_STATUS_EFFECTS = {"Sleep", "Poison", "Paralyze"}
_HEAL_EFFECTS = {"Heal"}
_FIXED_DAMAGE = {"Seismic Toss": 100, "Night Shade": 100, "Sonic Boom": 20,
                 "Dragon Rage": 40, "Psywave": 50, "Super Fang": 0}  # SuperFang handled by hp


def _l100_stat(base: int) -> int:
    return 2 * base + 98


def _l100_hp(base: int) -> int:
    return 2 * base + 203

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
    def __init__(self, seq_len: int | None = None, hist_k: int = 0, dmg_feats: bool = False):
        self.hist_k = hist_k
        self.dmg_feats = dmg_feats
        self.seq_len = seq_len if seq_len is not None else (HIST_SEQ_LEN if hist_k else SEQ_LEN)
        self._values = _build_value_vocab(hist=bool(hist_k))
        fields = FIELDS + (HIST_FIELDS if hist_k else [])
        slots = SLOTS + [f"HIST_-{i}" for i in range(1, hist_k + 1)]
        channels = CONT_CHANNELS + (DMG_CHANNELS if dmg_feats else [])
        self._value_ix = {v: i for i, v in enumerate(self._values)}
        self._field_ix = {f: i for i, f in enumerate(fields)}
        self._slot_ix = {s: i for i, s in enumerate(slots)}
        self._cont_ix = {c: i for i, c in enumerate(channels)}

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
        return len(self._cont_ix)

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
        # preallocated fast path: token emission is the rollout hot loop
        # (~3ms/state as list appends + a np.zeros per token); arrays are
        # zero-padded up front so emission is pure indexed writes
        field_arr = np.zeros(self.seq_len, dtype=np.int16)
        value_arr = np.zeros(self.seq_len, dtype=np.int16)
        slot_arr = np.zeros(self.seq_len, dtype=np.int16)
        cont_arr = np.zeros((self.seq_len, len(self._cont_ix)), dtype=np.float32)
        pos = 0

        def emit(field: str, value: str, slot: str, **channels: float) -> None:
            nonlocal pos
            if pos >= self.seq_len:
                raise ValueError(f"sequence exceeds {self.seq_len}")
            field_arr[pos] = self._field_ix[field]
            value_arr[pos] = self._value_ix[value]
            slot_arr[pos] = self._slot_ix[slot]
            for k, v in channels.items():
                cont_arr[pos, self._cont_ix[k]] = v
            pos += 1

        emit(
            "GLB",
            NONE,
            "GLOBAL",
            turn=min(state.get("turn", 0), 200) / 200.0,
            force_switch=1.0 if state.get("request_kind") == "force_switch" else 0.0,
        )

        opp_active = self._active_mon(state["opp_side"])
        my_active = self._active_mon(state["my_side"])

        self._emit_side(emit, state["my_side"], "MY", mine=True, opp_active=opp_active)
        self._emit_side(emit, state["opp_side"], "OPP", mine=False, opp_active=my_active)

        if self.hist_k:
            for entry in state.get("history_tail", [])[: self.hist_k]:
                self._emit_hist(emit, entry)

        return {
            "field_ids": field_arr,
            "value_ids": value_arr,
            "slot_ids": slot_arr,
            "cont": cont_arr,
            "length": pos,
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
    def _active_mon(side: dict[str, Any]) -> dict[str, Any] | None:
        mons = side["pokemon"]
        ix = side.get("active_ix", 0)
        return mons[ix] if 0 <= ix < len(mons) else None

    def _emit_side(self, emit, side: dict[str, Any], prefix: str, mine: bool, opp_active) -> None:
        mons = list(side["pokemon"])
        ix = side.get("active_ix", 0)
        if 0 <= ix < len(mons):
            mons = [mons[ix]] + mons[:ix] + mons[ix + 1 :]
        for i, mon in enumerate(mons[:6]):
            slot = f"{prefix}_ACTIVE" if i == 0 else f"{prefix}_BENCH_{i}"
            self._emit_mon(emit, mon, slot, mine=mine, opp_active=opp_active if i == 0 else None)

    def _emit_mon(self, emit, mon: dict[str, Any], slot: str, mine: bool, opp_active) -> None:
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
                opp_species = (opp_active or {}).get("species")
                if mine and slot == "MY_ACTIVE" and opp_species is not None:
                    channels["type_eff"] = self._effectiveness(name, opp_species) / 4.0
                    channels["stab"] = 1.0 if self._is_stab(name, mon["species"]) else 0.0
                    if self.dmg_feats:
                        channels.update(self._dmg_channels(name, mon, opp_active))
                emit("MOVE", f"MOVE:{name}", slot, **channels)
            else:
                emit("MOVE", NONE if mine else UNREVEALED, slot)

    def _dmg_channels(self, move: str, attacker: dict[str, Any], defender: dict[str, Any]) -> dict[str, float]:
        """dmg_frac / kills / acc / wasted for one of my active's moves vs the
        opp active — approximate L100 standard-stat gen1 damage, public info only."""
        if move not in MOVES:
            return {}
        _, _, bp, acc, mtype, effect = MOVES[move]
        d_species = defender["species"]
        out = {"acc": acc / 100.0}
        eff = self._effectiveness(move, d_species)
        if effect in _STATUS_EFFECTS:
            out["wasted"] = 1.0 if (defender.get("status") or eff == 0) else 0.0
            return out
        if effect in _HEAL_EFFECTS:
            out["wasted"] = 1.0 if (attacker.get("hp_fraction") or 0.0) >= 0.99 else 0.0
            return out
        sp_d = SPECIES[d_species]
        hp_max = _l100_hp(sp_d[1])
        if effect == "SuperFang":
            dmg = (defender.get("hp_fraction") or 0.0) * hp_max / 2 if eff else 0.0
        elif move in _FIXED_DAMAGE:
            dmg = float(_FIXED_DAMAGE[move])  # gen1: fixed damage ignores type
        elif bp <= 0:
            dmg = 0.0
        else:
            sp_a = SPECIES[attacker["species"]]
            phys = mtype in _PHYSICAL_TYPES
            a = _l100_stat(sp_a[2] if phys else sp_a[5])
            d = _l100_stat(sp_d[3] if phys else sp_d[5])
            if effect == "Explode":
                d = max(1, d // 2)
            stab = 1.5 if self._is_stab(move, attacker["species"]) else 1.0
            dmg = ((42 * bp * a / d) / 50 + 2) * stab * eff
        frac = dmg / hp_max
        out["dmg_frac"] = min(1.0, frac)
        cur = defender.get("hp_fraction")
        if cur is not None and frac * 0.85 >= cur > 0:
            out["kills"] = 1.0
        return out

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
