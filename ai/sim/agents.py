"""Seat agents: anything with choose(state) -> action can occupy a seat."""

from __future__ import annotations

import json
import random
from typing import Protocol

from sim.gen1data import MOVES, SPECIES, TYPE_CHART
from sim.schema import ACTION_PASS, ACTION_SWITCH_BASE, State


class Agent(Protocol):
    def choose(self, state: State) -> int: ...


class RandomBot:
    def __init__(self, seed: int = 0):
        self._rng = random.Random(seed)

    def choose(self, state: State) -> int:
        return self._rng.choice(state.legal_actions)


def _types(species: str | None) -> tuple[str, str] | None:
    if not species or species not in SPECIES:
        return None
    sp = SPECIES[species]
    return (sp[6], sp[7])


def _dmg_score(move: str, attacker_types, defender_types) -> float:
    """bp * type-effectiveness * STAB - the family damage heuristic."""
    _, _, bp, _, mtype, _ = MOVES[move]
    score = float(bp)
    if score and defender_types:
        eff = TYPE_CHART[mtype][defender_types[0]]
        if defender_types[1] != defender_types[0]:
            eff *= TYPE_CHART[mtype][defender_types[1]]
        score *= eff
    if score and attacker_types and mtype in attacker_types:
        score *= 1.5
    return score


def _best_move_score(mon: dict, defender_types) -> float:
    my_types = _types(mon["species"])
    return max(
        (_dmg_score(m["id"], my_types, defender_types) for m in mon["moves"]),
        default=0.0,
    )


def _switch_actions(state: State) -> list[tuple[int, dict]]:
    """(action, mon) for each legal switch; action 4+j = j-th bench entry of
    the listing minus the active (schema_v1 contract)."""
    mons = state.my_side["pokemon"]
    active_ix = state.my_side["active_ix"]
    bench = [m for i, m in enumerate(mons) if i != active_ix]
    return [
        (ACTION_SWITCH_BASE + j, mon)
        for j, mon in enumerate(bench)
        if ACTION_SWITCH_BASE + j in state.legal_actions
    ]


def _fallback(state: State) -> int:
    non_pass = [a for a in state.legal_actions if a != ACTION_PASS]
    return non_pass[0] if non_pass else ACTION_PASS


class MaxDamageBot:
    """Calc-aware greedy: highest bp * STAB * type-effectiveness legal move;
    switches only when forced."""

    def choose(self, state: State) -> int:
        me = state.my_side["pokemon"][state.my_side["active_ix"]]
        opp = state.opp_side["pokemon"][state.opp_side["active_ix"]]
        opp_types = _types(opp["species"])
        my_types = _types(me["species"])

        best, best_score = None, -1.0
        for action in state.legal_actions:
            if not 0 <= action <= 3:
                continue
            move = me["moves"][action]["id"] if action < len(me["moves"]) else None
            if move is None:
                continue
            score = _dmg_score(move, my_types, opp_types)
            if score > best_score:
                best, best_score = action, score
        if best is not None:
            return best
        return _fallback(state)


class UniversalMaxDamageBot:
    """MaxDamage over the WHOLE team: if a bench mon's best move out-damages
    the active's best, switch to it; otherwise use the active's best move."""

    def choose(self, state: State) -> int:
        opp = state.opp_side["pokemon"][state.opp_side["active_ix"]]
        opp_types = _types(opp["species"])
        me = state.my_side["pokemon"][state.my_side["active_ix"]]
        switches = _switch_actions(state)

        active_best = _best_move_score(me, opp_types) if me["moves"] else 0.0
        bench_scored = sorted(
            ((_best_move_score(mon, opp_types), action) for action, mon in switches),
            key=lambda t: (-t[0], t[1]),
        )
        can_move = any(0 <= a <= 3 for a in state.legal_actions)
        if bench_scored and (not can_move or bench_scored[0][0] > active_best):
            return bench_scored[0][1]
        if can_move:
            return MaxDamageBot().choose(state)
        return _fallback(state)


class LessEffectiveMaxDamageBot:
    """Defensive pivot + attack: switch to the team member taking the least
    damage from the opponent's best attack (revealed moves, else a STAB
    estimate from its typing), then use the max-damage move."""

    _STAB_ESTIMATE_BP = 90.0

    def _opp_best_dmg(self, opp: dict, defender: dict) -> float:
        d_types = _types(defender["species"])
        o_types = _types(opp["species"])
        revealed = opp.get("revealed_moves") or []
        if revealed:
            return max(_dmg_score(m, o_types, d_types) for m in revealed)
        if not o_types or not d_types:
            return 0.0
        best = 0.0
        for mtype in set(o_types):
            eff = TYPE_CHART[mtype][d_types[0]]
            if d_types[1] != d_types[0]:
                eff *= TYPE_CHART[mtype][d_types[1]]
            best = max(best, self._STAB_ESTIMATE_BP * eff * 1.5)
        return best

    def choose(self, state: State) -> int:
        opp = state.opp_side["pokemon"][state.opp_side["active_ix"]]
        me = state.my_side["pokemon"][state.my_side["active_ix"]]
        switches = _switch_actions(state)
        can_move = any(0 <= a <= 3 for a in state.legal_actions)

        taken_active = self._opp_best_dmg(opp, me) if can_move else float("inf")
        bench_taken = sorted(
            ((self._opp_best_dmg(opp, mon), action) for action, mon in switches),
            key=lambda t: (t[0], t[1]),
        )
        if bench_taken and bench_taken[0][0] < taken_active:
            return bench_taken[0][1]
        if can_move:
            return MaxDamageBot().choose(state)
        return _fallback(state)


class HumanCLI:
    """Interactive seat: prints the state as JSON and prompts for an action."""

    def choose(self, state: State) -> int:
        print(json.dumps(state.to_json(), indent=2))
        while True:
            raw = input(f"action {state.legal_actions}> ").strip()
            if raw.isdigit() and int(raw) in state.legal_actions:
                return int(raw)
            print(f"illegal; pick one of {state.legal_actions}")
