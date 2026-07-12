"""Seat agents: anything with choose(state) -> action can occupy a seat."""

from __future__ import annotations

import json
import random
from typing import Protocol

from sim.gen1data import MOVES, SPECIES, TYPE_CHART
from sim.schema import ACTION_PASS, State


class Agent(Protocol):
    def choose(self, state: State) -> int: ...


class RandomBot:
    def __init__(self, seed: int = 0):
        self._rng = random.Random(seed)

    def choose(self, state: State) -> int:
        return self._rng.choice(state.legal_actions)


class MaxDamageBot:
    """Calc-aware greedy: highest bp * STAB * type-effectiveness legal move;
    switches only when forced."""

    def choose(self, state: State) -> int:
        me = state.my_side["pokemon"][state.my_side["active_ix"]]
        opp = state.opp_side["pokemon"][state.opp_side["active_ix"]]
        opp_types = None
        if opp["species"]:
            sp = SPECIES[opp["species"]]
            opp_types = (sp[6], sp[7])
        my_types = (SPECIES[me["species"]][6], SPECIES[me["species"]][7])

        best, best_score = None, -1.0
        for action in state.legal_actions:
            if not 0 <= action <= 3:
                continue
            move = me["moves"][action]["id"] if action < len(me["moves"]) else None
            if move is None:
                continue
            _, _, bp, _, mtype, _ = MOVES[move]
            score = float(bp)
            if score and opp_types:
                eff = TYPE_CHART[mtype][opp_types[0]]
                if opp_types[1] != opp_types[0]:
                    eff *= TYPE_CHART[mtype][opp_types[1]]
                score *= eff
                if mtype in my_types:
                    score *= 1.5
            if score > best_score:
                best, best_score = action, score
        if best is not None:
            return best
        non_pass = [a for a in state.legal_actions if a != ACTION_PASS]
        return non_pass[0] if non_pass else ACTION_PASS


class HumanCLI:
    """Interactive seat: prints the state as JSON and prompts for an action."""

    def choose(self, state: State) -> int:
        print(json.dumps(state.to_json(), indent=2))
        while True:
            raw = input(f"action {state.legal_actions}> ").strip()
            if raw.isdigit() and int(raw) in state.legal_actions:
                return int(raw)
            print(f"illegal; pick one of {state.legal_actions}")
