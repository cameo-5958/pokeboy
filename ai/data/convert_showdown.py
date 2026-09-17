"""Convert Showdown gen1 replay logs into schema_v1 decision rows.

Replays cannot be re-simulated exactly (the RNG seed is not in the log), so
states are *reconstructed* from the line protocol and validated by internal
consistency checks; malformed replays raise RejectedReplay.

Action semantics for replay data (reveal-order, deterministic given state_json):
  moves:    action = 0..3, index of the move in the mon's revealed-move list
            (order of first use), which is exactly the order moves appear in
            state_json's revealed_moves-equivalent "moves" list.
  switches: action = 4 + index of the target in the bench list (team in reveal
            order, minus the active mon).

Decision boundaries: normal-turn decisions
are snapshotted at the START of the turn - both players' rows come from one
boundary state, so neither sees same-turn effects, reveals, or its own label.
Forced replacements decide mid-turn and keep execution-time state. A player's
decision is only emitted when it is observable AND its label is derivable from
the pre-decision state: |cant|/full-skip turns, first-use moves, and switches
to not-yet-revealed teammates produce no row.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from data.normalize import canon_move, canon_species
from data.trajectory import dumps_state, mechanics_flags_hash


class RejectedReplay(ValueError):
    pass


_HP = re.compile(r"^(\d+)(?:/(\d+))?")


class _Mon:
    def __init__(self, species: str):
        self.species = species
        self.hp = 1.0
        self.status: str | None = None
        self.moves: list[str] = []  # reveal order
        self.fainted = False


class _Side:
    def __init__(self):
        self.team: list[_Mon] = []  # reveal order
        self.active: _Mon | None = None

    def get(self, species: str) -> _Mon:
        for m in self.team:
            if m.species == species:
                return m
        mon = _Mon(species)
        self.team.append(mon)
        return mon


def _parse_hp(text: str) -> tuple[float, bool]:
    text = text.strip()
    if text.startswith("0 fnt") or text == "0":
        return 0.0, True
    m = _HP.match(text)
    if not m:
        raise RejectedReplay(f"unparseable hp {text!r}")
    num = int(m.group(1))
    den = int(m.group(2)) if m.group(2) else 100
    return num / den, False


HIST_K = 20
_EVENT_LINES = {
    "-crit": "crit",
    "-supereffective": "se",
    "-resisted": "re",
    "-immune": "im",
    "-miss": "miss",
    "-status": "st",
    "-boost": "sc",
    "-unboost": "sc",
}


def _dmg_bucket(frac: float) -> int:
    """SPECS §4.1 damage buckets: 0, (0,10%], …, (50,60%], >60% (KO=8 set by caller)."""
    if frac <= 0:
        return 0
    return min(7, 1 + int(min(frac, 0.6999) * 10))


def _new_turn_log() -> dict:
    return {"act": [None, None], "dmg": [0.0, 0.0], "ko": [False, False], "ev": set()}


def _tail(hist: list[dict], me: int, k: int = HIST_K) -> list[dict]:
    out = []
    for i, t in enumerate(reversed(hist[-k:])):
        out.append(
            {
                "o": -(i + 1),
                "my": t["act"][me],
                "op": t["act"][1 - me],
                "dm": 8 if t["ko"][me] else _dmg_bucket(t["dmg"][me]),
                "do": 8 if t["ko"][1 - me] else _dmg_bucket(t["dmg"][1 - me]),
                "ev": sorted(t["ev"]),
            }
        )
    return out


def _ident_side(ident: str) -> int:
    # "p1a: Jynx" -> 0
    if not ident.startswith(("p1", "p2")):
        raise RejectedReplay(f"bad ident {ident!r}")
    return 0 if ident[1] == "1" else 1


_STATUS = {"slp": "SLP", "par": "PAR", "brn": "BRN", "frz": "FRZ", "psn": "PSN", "tox": "TOX"}


def _snapshot(sides: list[_Side], me: int, turn: int, request_kind: str) -> dict[str, Any]:
    def my_pokemon(mon: _Mon) -> dict[str, Any]:
        return {
            "species": mon.species,
            "hp_fraction": round(mon.hp, 4),
            "status": mon.status,
            "moves": list(mon.moves),
            "fainted": mon.fainted,
        }

    def opp_pokemon(mon: _Mon) -> dict[str, Any]:
        return {
            "species": mon.species,
            "hp_fraction": round(mon.hp, 4),
            "status": mon.status,
            "revealed_moves": list(mon.moves),
            "fainted": mon.fainted,
        }

    mine, theirs = sides[me], sides[1 - me]
    return {
        "schema_v": 1,
        "turn": turn,
        "request_kind": request_kind,
        "my_side": {
            "active_ix": mine.team.index(mine.active) if mine.active else 0,
            "pokemon": [my_pokemon(m) for m in mine.team],
        },
        "opp_side": {
            "active_ix": theirs.team.index(theirs.active) if theirs.active else 0,
            "pokemon": [opp_pokemon(m) for m in theirs.team],
        },
    }


def convert_replay(replay: dict, source: str) -> list[dict]:
    log = replay.get("log")
    if not log:
        raise RejectedReplay("no log")
    battle_id = replay.get("id", "unknown")
    elo = int(replay.get("rating") or 0)
    flags_hash = mechanics_flags_hash()

    sides = [_Side(), _Side()]
    rows: list[dict] = []
    turn = 0
    winner_side: int | None = None
    started = False
    hist: list[dict] = []  # closed turns, oldest first
    cur = _new_turn_log()
    boundary: list[_Side] | None = None  # deep copy of sides at turn start

    def record_action(side_ix: int, action: str) -> None:
        # first action of the turn is the side's decision; later same-turn
        # lines (forced replacement after a KO) don't overwrite it
        if turn >= 1 and started and cur["act"][side_ix] is None:
            cur["act"][side_ix] = action

    def emit(side: int, action: int, detail: str, request_kind: str, snap: dict) -> None:
        rows.append(
            {
                "battle_id": battle_id,
                "source": source,
                "elo": elo,
                "mechanics_flags_hash": flags_hash,
                "turn": turn,
                "player": side + 1,
                "state_json": dumps_state(snap),
                "action": action,
                "action_detail": detail,
                "request_kind": request_kind,
                "won": False,  # patched at the end
            }
        )

    for line in log.splitlines():
        if not line.startswith("|"):
            continue
        parts = line.split("|")
        cmd = parts[1] if len(parts) > 1 else ""
        try:
            if cmd == "turn":
                if turn >= 1:
                    hist.append(cur)
                    cur = _new_turn_log()
                turn = int(parts[2])
                boundary = copy.deepcopy(sides)
            elif cmd == "start":
                started = True
            elif cmd == "switch":
                side_ix = _ident_side(parts[2])
                side = sides[side_ix]
                species = canon_species(parts[3].split(",")[0].strip())
                if species is None:
                    raise RejectedReplay(f"unknown species {parts[3]!r}")
                forced = side.active is not None and side.active.fainted
                if started and turn >= 1 and side.active is not None:
                    # forced replacements decide mid-turn (execution-time state);
                    # voluntary switches decide at the turn boundary
                    view = sides if forced else boundary
                    if view is not None and view[side_ix].active is not None:
                        vside = view[side_ix]
                        bench = [m for m in vside.team if m is not vside.active]
                        ix = next(
                            (i for i, m in enumerate(bench) if m.species == species), None
                        )
                        if ix is not None:  # unrevealed targets are unrecoverable
                            kind = "force_switch" if forced else "turn"
                            snap = _snapshot(view, side_ix, turn, kind)
                            snap["history_tail"] = _tail(hist, side_ix)
                            emit(side_ix, 4 + ix, species, kind, snap)
                record_action(side_ix, f"S:{species}")
                side.active = side.get(species)
                hp, fnt = _parse_hp(parts[4]) if len(parts) > 4 else (1.0, False)
                side.active.hp, side.active.fainted = hp, fnt
            elif cmd == "move":
                side_ix = _ident_side(parts[2])
                side = sides[side_ix]
                move = canon_move(parts[3].strip())
                if side.active is None:
                    raise RejectedReplay("move with no active")
                if move is not None:
                    if move not in side.active.moves and move != "Struggle":
                        side.active.moves.append(move)
                    if len(side.active.moves) > 4:
                        raise RejectedReplay(
                            f"{side.active.species} revealed >4 moves"
                        )
                    # emit from the turn boundary: the decision predates every
                    # same-turn effect, and a first use (absent from the
                    # boundary's move list) is unrecoverable → no row
                    if boundary is not None and turn >= 1 and started:
                        bactive = boundary[side_ix].active
                        if bactive is not None and bactive.species == side.active.species:
                            action = None
                            if move == "Struggle":
                                action = 9
                            elif move in bactive.moves:
                                action = bactive.moves.index(move)
                            if action is not None:
                                snap = _snapshot(boundary, side_ix, turn, "turn")
                                snap["history_tail"] = _tail(hist, side_ix)
                                emit(side_ix, action, move, "turn", snap)
                    record_action(side_ix, f"M:{move}")
            elif cmd == "-damage" or cmd == "-heal":
                # damage/heal idents always refer to the active slot in gen1 singles
                dmg_side = _ident_side(parts[2])
                mon = sides[dmg_side].active
                if mon is not None:
                    new_hp, fnt = _parse_hp(parts[3])
                    if cmd == "-damage" and turn >= 1:
                        cur["dmg"][dmg_side] += max(0.0, mon.hp - new_hp)
                    mon.hp, mon.fainted = new_hp, mon.fainted or fnt
            elif cmd == "-status":
                side = sides[_ident_side(parts[2])]
                if side.active is not None:
                    side.active.status = _STATUS.get(parts[3], parts[3].upper())
                cur["ev"].add("st")
            elif cmd == "-curestatus":
                side = sides[_ident_side(parts[2])]
                if side.active is not None:
                    side.active.status = None
            elif cmd == "faint":
                faint_side = _ident_side(parts[2])
                side = sides[faint_side]
                if side.active is not None:
                    side.active.fainted = True
                    side.active.hp = 0.0
                cur["ko"][faint_side] = True
                cur["ev"].add("ft")
            elif cmd in _EVENT_LINES:
                cur["ev"].add(_EVENT_LINES[cmd])
            elif cmd == "win":
                name = parts[2]
                players = replay.get("players") or []
                if name in players:
                    winner_side = players.index(name)
        except RejectedReplay:
            raise
        except (IndexError, ValueError) as exc:
            raise RejectedReplay(f"malformed line {line!r}: {exc}") from exc

    if not started or not rows:
        raise RejectedReplay("no decisions parsed")
    if winner_side is not None:
        for row in rows:
            row["won"] = (row["player"] - 1) == winner_side

    _validate(rows)
    return rows


def _validate(rows: list[dict]) -> None:
    import json

    for row in rows:
        if not 0 <= row["action"] <= 9:
            raise RejectedReplay(f"action out of range: {row['action']}")
        state = json.loads(row["state_json"])
        for side_key in ("my_side", "opp_side"):
            for mon in state[side_key]["pokemon"]:
                hp = mon["hp_fraction"]
                if hp is not None and not 0.0 <= hp <= 1.0:
                    raise RejectedReplay(f"hp out of range: {hp}")
