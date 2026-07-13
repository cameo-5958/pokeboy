"""Convert Metamon parsed-replay trajectories (gen1 slice) to schema_v1 rows.

Metamon format (jakegrigsby/metamon-parsed-replays, verified 2026-07-12):
tarballs of <battle>.json.lz4 with {"states": [...], "actions": [...]},
already first-person. Actions: 0-3 move slots, 4-8 switches (index into
available_switches), -1 = no observable decision (skipped).

Filename carries outcome and rating:
  smogtours-gen1ou-732991_Unrated_<p1>_vs_<p2>_<date>_WIN.json.lz4
"""

from __future__ import annotations

import json
import re
import tarfile
from pathlib import Path
from typing import Any, Iterator

import lz4.frame

from data.convert_showdown import RejectedReplay
from data.trajectory import dumps_state, mechanics_flags_hash
from data.normalize import canon_move, canon_species, canon_status

_FNAME = re.compile(r"(?P<id>[^/]+?)_(?P<rating>Unrated|\d+)_.*_(?P<outcome>WIN|LOSS)\.json\.lz4$")


def _mon(p: dict[str, Any], mine: bool) -> dict[str, Any]:
    species = canon_species(p.get("base_species") or p.get("name") or "")
    if species is None:
        raise RejectedReplay(f"unknown species {p.get('base_species')!r}")
    moves = []
    for m in p.get("moves", []):
        name = canon_move(m["name"])
        if name is None:
            raise RejectedReplay(f"unknown move {m['name']!r}")
        moves.append({"id": name, "pp": m.get("current_pp", 0)})
    status = canon_status(p.get("status"))
    out: dict[str, Any] = {
        "species": species,
        "hp_fraction": round(float(p.get("hp_pct", 0.0)), 4),
        "status": None if status == "FNT" else status,
        "fainted": status == "FNT" or p.get("hp_pct", 0.0) <= 0,
    }
    if mine:
        out["moves"] = [m["id"] for m in moves]
        out["pp"] = [m["pp"] for m in moves]
    else:
        out["revealed_moves"] = [m["id"] for m in moves]
    return out


def _state_json(s: dict[str, Any]) -> dict[str, Any]:
    mine = [_mon(s["player_active_pokemon"], mine=True)]
    mine += [_mon(p, mine=True) for p in s.get("available_switches", [])]
    opp_active = _mon(s["opponent_active_pokemon"], mine=False)
    unrevealed = max(0, int(s.get("opponents_remaining", 1)) - 1)
    opp = [opp_active] + [
        {"species": None, "hp_fraction": None, "status": None, "revealed_moves": []}
    ] * unrevealed
    return {
        "schema_v": 1,
        "request_kind": "force_switch" if s.get("forced_switch") else "turn",
        "my_side": {"active_ix": 0, "pokemon": mine},
        "opp_side": {"active_ix": 0, "pokemon": opp},
    }


def convert_trajectory(name: str, blob: bytes, source: str) -> list[dict]:
    m = _FNAME.search(name)
    if not m:
        raise RejectedReplay(f"unparseable filename {name!r}")
    won = m.group("outcome") == "WIN"
    elo = 0 if m.group("rating") == "Unrated" else int(m.group("rating"))
    try:
        d = json.loads(lz4.frame.decompress(blob))
    except Exception as exc:  # noqa: BLE001
        raise RejectedReplay(f"undecodable: {exc}") from exc
    states, actions = d.get("states"), d.get("actions")
    if not states or actions is None or len(states) != len(actions):
        raise RejectedReplay("states/actions mismatch")
    flags_hash = mechanics_flags_hash()
    rows = []
    for turn, (state, action) in enumerate(zip(states, actions)):
        if action < 0 or action > 8:
            continue
        snap = _state_json(state)
        detail = ""
        me = snap["my_side"]["pokemon"]
        if action <= 3:
            moves = me[0]["moves"]
            if action >= len(moves):
                continue  # move slot not present in parsed state
            detail = moves[action]
        else:
            bench_ix = action - 4
            if bench_ix >= len(me) - 1:
                continue  # switch target not present
            detail = me[1 + bench_ix]["species"]
        snap["turn"] = turn
        rows.append(
            {
                "battle_id": m.group("id"),
                "source": source,
                "elo": elo,
                "mechanics_flags_hash": flags_hash,
                "turn": turn,
                "player": 1,
                "state_json": dumps_state(snap),
                "action": action,
                "action_detail": detail,
                "request_kind": snap["request_kind"],
                "won": won,
            }
        )
    if not rows:
        raise RejectedReplay("no usable decisions")
    return rows


def iter_tarball(path: Path) -> Iterator[tuple[str, bytes]]:
    with tarfile.open(path, "r:gz") as tar:
        for member in tar:
            if member.isfile() and member.name.endswith(".json.lz4"):
                f = tar.extractfile(member)
                if f is not None:
                    yield member.name, f.read()
