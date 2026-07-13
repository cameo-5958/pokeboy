"""High-level Battle facade over RawBattle: schema_v1 states, revealed-info
tracking, and the SPECS action-int mapping."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from sim import engine
from sim.engine import MOVE, PASS, RESULT_NONE, SWITCH, RawBattle
from sim.gen1data import MOVES, SPECIES, TYPE_CHART
from sim.pack import PokemonSpec, max_pp, pack_battle
from sim.schema import ACTION_PASS, ACTION_SWITCH_BASE, State

HIST_K = 20


def _dmg_bucket(frac: float) -> int:
    # same buckets as data/convert_showdown.py (SPECS §4.1); duplicated to
    # keep sim free of data/ imports
    if frac <= 0:
        return 0
    return min(7, 1 + int(min(frac, 0.6999) * 10))

SPECIES_BY_ID = {v[0]: k for k, v in SPECIES.items()}
MOVES_BY_ID = {v[0]: k for k, v in MOVES.items()}

_SIDE = (0, 184)
_POKE = 24
_ORDER_OFF = 176
_TURN_OFF = 368

STATUS_NAMES = {0x08: "PSN", 0x10: "BRN", 0x20: "FRZ", 0x40: "PAR"}


def _status_name(status: int) -> str | None:
    if status == 0:
        return None
    if status & 0x07:
        return "SLP"
    for bit, name in STATUS_NAMES.items():
        if status & bit:
            return name
    return None


def _parse_pokemon(buf: bytes, side: int, ix: int) -> dict[str, Any]:
    o = _SIDE[side] + ix * _POKE
    hp_max = int.from_bytes(buf[o : o + 2], "little")
    moves = []
    for m in range(4):
        mid, pp = buf[o + 10 + m * 2], buf[o + 11 + m * 2]
        if mid:
            moves.append({"id": MOVES_BY_ID[mid], "pp": pp})
    hp = int.from_bytes(buf[o + 18 : o + 20], "little")
    return {
        "species": SPECIES_BY_ID.get(buf[o + 21]),
        "level": buf[o + 23],
        "hp": hp,
        "max_hp": hp_max,
        "status": _status_name(buf[o + 20]),
        "moves": moves,
        "fainted": hp == 0,
    }


@dataclass
class BattleRecord:
    winner: str
    turns: list[dict[str, Any]] = field(default_factory=list)


class Battle:
    """Two-seat gen1 battle. Players are 1 and 2 in the public API."""

    def __init__(self, team1: list[PokemonSpec], team2: list[PokemonSpec], seed: int):
        self.teams = (team1, team2)
        packed = pack_battle(team1, team2, seed)
        self.battle_id = "b_" + hashlib.sha1(packed).hexdigest()[:8]
        self.raw = RawBattle(packed, seed=seed)
        # revealed[side] = set of party indexes seen; revealed_moves[side][ix]
        self._revealed: tuple[set[int], set[int]] = (set(), set())
        self._revealed_moves: tuple[dict[int, set[str]], dict[int, set[str]]] = ({}, {})
        self._initial_pp = tuple(
            {ix: {m: max_pp(m) for m in p.moves} for ix, p in enumerate(team)}
            for team in (team1, team2)
        )
        self._hist: list[dict] = []  # one merged entry per game turn
        self.raw.update(0, 0)  # initial switch-in of both leads
        self._track_reveals()

    # -- revealed-information tracking (engine-truth based, no protocol logs) --

    def _order(self, side: int) -> bytes:
        """order[pos] = 1-based party id of the mon at battle position pos+1;
        position 1 is the active mon (engine data.zig get()/switchIn)."""
        o = _SIDE[side] + _ORDER_OFF
        return self.raw.bytes[o : o + 6]

    def _active_ix(self, side: int) -> int:
        first = self._order(side)[0]
        return first - 1 if first else 0

    def _track_reveals(self) -> None:
        buf = self.raw.bytes
        for side in (0, 1):
            active = self._active_ix(side)
            self._revealed[side].add(active)
            for ix in list(self._revealed[side]):
                seen = self._revealed_moves[side].setdefault(ix, set())
                o = _SIDE[side] + ix * _POKE
                for m in range(4):
                    mid, pp = buf[o + 10 + m * 2], buf[o + 11 + m * 2]
                    if not mid:
                        continue
                    name = MOVES_BY_ID[mid]
                    if pp < self._initial_pp[side].get(ix, {}).get(name, 0):
                        seen.add(name)

    # -- SPECS action ints <-> engine choices --

    def _choice_map(self, side: int) -> dict[int, int]:
        request = self.raw.requests()[side]
        order = self._order(side)
        active = self._active_ix(side)
        mapping: dict[int, int] = {}
        for choice in self.raw.choices(side, request):
            kind = engine.choice_type(choice)
            data = engine.choice_data(choice)
            if kind == MOVE:
                mapping[data - 1 if data else ACTION_PASS] = choice
            elif kind == SWITCH:
                # schema_v1: action 4+j = j-th mon of the party listing minus
                # the active one; engine switch data d targets order[d-1]
                target = order[data - 1] - 1
                j = target - 1 if target > active else target
                mapping[ACTION_SWITCH_BASE + j] = choice
            elif kind == PASS:
                mapping[ACTION_PASS] = choice
        return mapping

    # -- per-turn history from engine-observable diffs (SPECS §4.1 HIST) --

    def _turn_no(self) -> int:
        return int.from_bytes(self.raw.bytes[_TURN_OFF : _TURN_OFF + 2], "little")

    def _side_snapshot(self, side: int) -> dict[str, Any]:
        mons = [_parse_pokemon(self.raw.bytes, side, i) for i in range(len(self.teams[side]))]
        active = self._active_ix(side)
        return {
            "active": active,
            "species": [m["species"] for m in mons],
            "frac": [m["hp"] / m["max_hp"] if m["max_hp"] else 0.0 for m in mons],
            "status": [m["status"] for m in mons],
            "pp": {mv["id"]: mv["pp"] for mv in mons[active]["moves"]},
        }

    @staticmethod
    def _observed_action(pre: dict, post: dict) -> str | None:
        """What the opponent could see this side do: a switch (active species
        changed) or a move (PP visibly spent); blocked/idle turns are None."""
        if post["active"] != pre["active"]:
            return f"S:{post['species'][post['active']]}"
        for name, pp0 in pre["pp"].items():
            if post["pp"].get(name, pp0) < pp0:
                return f"M:{name}"
        return None

    def _record_step(self, turn: int, pre: tuple[dict, dict], post: tuple[dict, dict]) -> None:
        if not self._hist or self._hist[-1]["turn"] != turn:
            self._hist.append(
                {"turn": turn, "act": [None, None], "dmg": [0.0, 0.0],
                 "ko": [False, False], "ev": set()}
            )
        t = self._hist[-1]
        for s in (0, 1):
            obs = self._observed_action(pre[s], post[s])
            if t["act"][s] is None:  # first observable action of the turn wins
                t["act"][s] = obs
            t["dmg"][s] += sum(
                max(0.0, a - b) for a, b in zip(pre[s]["frac"], post[s]["frac"])
            )
            if any(a > 0 and b == 0 for a, b in zip(pre[s]["frac"], post[s]["frac"])):
                t["ko"][s] = True
                t["ev"].add("ft")
            if any(
                a is None and b is not None for a, b in zip(pre[s]["status"], post[s]["status"])
            ):
                t["ev"].add("st")
            if obs and obs.startswith("M:"):
                self._effectiveness_events(t, obs[2:], pre[1 - s])

    @staticmethod
    def _effectiveness_events(t: dict, move: str, defender_pre: dict) -> None:
        if MOVES[move][2] <= 0:  # status move, no matchup to report
            return
        species = defender_pre["species"][defender_pre["active"]]
        if species is None:
            return
        mtype = MOVES[move][4]
        t1, t2 = SPECIES[species][6], SPECIES[species][7]
        eff = TYPE_CHART[mtype][t1] * (TYPE_CHART[mtype][t2] if t2 != t1 else 1.0)
        if eff == 0:
            t["ev"].add("im")
        elif eff > 1:
            t["ev"].add("se")
        elif eff < 1:
            t["ev"].add("re")

    def _history_tail(self, side: int, k: int = HIST_K) -> list[dict[str, Any]]:
        cur = self._turn_no()
        done = [t for t in self._hist if t["turn"] < cur]
        out = []
        for i, t in enumerate(reversed(done[-k:])):
            me, opp = side, 1 - side
            out.append(
                {
                    "o": -(i + 1),
                    "my": t["act"][me],
                    "op": t["act"][opp],
                    "dm": 8 if t["ko"][me] else _dmg_bucket(t["dmg"][me]),
                    "do": 8 if t["ko"][opp] else _dmg_bucket(t["dmg"][opp]),
                    "ev": sorted(t["ev"]),
                }
            )
        return out

    @property
    def winner(self) -> str | None:
        return self.raw.winner()

    def state(self, player: int) -> State:
        side = player - 1
        opp = 1 - side
        buf = self.raw.bytes
        request = self.raw.requests()[side]
        my_pokemon = [_parse_pokemon(buf, side, i) for i in range(len(self.teams[side]))]
        opp_pokemon = []
        for i in range(len(self.teams[opp])):
            p = _parse_pokemon(buf, opp, i)
            if i in self._revealed[opp]:
                opp_pokemon.append(
                    {
                        "species": p["species"],
                        "hp_fraction": round(p["hp"] / p["max_hp"], 4) if p["max_hp"] else 0.0,
                        "status": p["status"],
                        "revealed_moves": sorted(self._revealed_moves[opp].get(i, set())),
                        "fainted": p["fainted"],
                    }
                )
            else:
                opp_pokemon.append(
                    {"species": None, "hp_fraction": None, "status": None, "revealed_moves": []}
                )
        request_kind = {PASS: "wait", MOVE: "turn", SWITCH: "force_switch"}[request]
        return State(
            battle_id=self.battle_id,
            turn=int.from_bytes(buf[_TURN_OFF : _TURN_OFF + 2], "little"),
            request_kind=request_kind,
            my_side={"active_ix": self._active_ix(side), "pokemon": my_pokemon},
            opp_side={"active_ix": self._active_ix(opp), "pokemon": opp_pokemon},
            legal_actions=sorted(self._choice_map(side)),
            history_tail=self._history_tail(side),
        )

    def step(self, a1: int, a2: int) -> None:
        if self.raw.result_type() != RESULT_NONE:
            raise RuntimeError("battle is over")
        maps = (self._choice_map(0), self._choice_map(1))
        choices = []
        for side, action in ((0, a1), (1, a2)):
            if action not in maps[side]:
                raise ValueError(
                    f"illegal action {action} for p{side + 1}; legal: {sorted(maps[side])}"
                )
            choices.append(maps[side][action])
        turn = self._turn_no()
        pre = (self._side_snapshot(0), self._side_snapshot(1))
        self.raw.update(choices[0], choices[1])
        self._record_step(turn, pre, (self._side_snapshot(0), self._side_snapshot(1)))
        self._track_reveals()


def run_battle(agent1, agent2, team1, team2, seed: int, max_turns: int = 1000) -> BattleRecord:
    b = Battle(team1, team2, seed)
    turns: list[dict[str, Any]] = []
    for _ in range(max_turns):
        if b.winner:
            break
        s1, s2 = b.state(1), b.state(2)
        a1, a2 = agent1.choose(s1), agent2.choose(s2)
        turns.append({"state_p1": s1.to_json(), "state_p2": s2.to_json(), "a1": a1, "a2": a2})
        b.step(a1, a2)
    return BattleRecord(winner=b.winner or "unfinished", turns=turns)
