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


def _prev_move(p: dict[str, Any] | None) -> str | None:
    name = (p or {}).get("name")
    if not name or name == "nomove":
        return None
    canon = canon_move(name)
    return f"M:{canon}" if canon else None


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


HIST_K = 20
_DMG_KO = 8


def _dmg_bucket(frac: float) -> int:
    if frac <= 0:
        return 0
    return min(7, 1 + int(min(frac, 0.6999) * 10))


def _eff_events(ev: set, move: str, defender: str | None) -> None:
    from sim.gen1data import MOVES, SPECIES, TYPE_CHART

    if defender is None or move not in MOVES or defender not in SPECIES:
        return
    bp, mtype = MOVES[move][2], MOVES[move][4]
    if bp <= 0:
        return
    t1, t2 = SPECIES[defender][6], SPECIES[defender][7]
    eff = TYPE_CHART[mtype][t1] * (TYPE_CHART[mtype][t2] if t2 != t1 else 1.0)
    if eff == 0:
        ev.add("im")
    elif eff > 1:
        ev.add("se")
    elif eff < 1:
        ev.add("re")


def _my_action(snap: dict, action: int) -> str | None:
    me = snap["my_side"]["pokemon"]
    if 0 <= action <= 3:
        moves = me[0]["moves"]
        return f"M:{moves[action]}" if action < len(moves) else None
    if 4 <= action <= 8 and action - 4 < len(me) - 1:
        return f"S:{me[1 + action - 4]['species']}"
    return None


def _transition(snap: dict, nxt_snap: dict, raw_nxt: dict, action: int) -> dict:
    """History entry for the step snap → nxt_snap (metamon trajectories are
    sequential, so damage/faints/switches are hp/species diffs)."""
    ev: set[str] = set()
    my = _my_action(snap, action)
    me0 = {m["species"]: m for m in snap["my_side"]["pokemon"]}
    me1 = {m["species"]: m for m in nxt_snap["my_side"]["pokemon"]}
    dm, my_ko = 0.0, False
    for sp, m in me0.items():
        after = me1.get(sp)
        hp0 = m["hp_fraction"] or 0.0
        hp1 = (after["hp_fraction"] or 0.0) if after else 0.0
        dm += max(0.0, hp0 - hp1)
        if hp0 > 0 and (after is None or hp1 <= 0 or after.get("fainted")):
            my_ko = True
        if after and not m.get("status") and after.get("status"):
            ev.add("st")

    opp0 = snap["opp_side"]["pokemon"][0]
    opp1 = nxt_snap["opp_side"]["pokemon"][0]
    if opp1["species"] != opp0["species"]:
        op = f"S:{opp1['species']}"
    else:
        op = _prev_move(raw_nxt.get("opponent_prev_move"))
        if not opp0.get("status") and opp1.get("status"):
            ev.add("st")
    do, opp_ko = None, False
    if opp1["species"] == opp0["species"]:
        do = _dmg_bucket(max(0.0, (opp0["hp_fraction"] or 0.0) - (opp1["hp_fraction"] or 0.0)))
    n_opp0 = 1 + len(snap["opp_side"]["pokemon"][1:])
    n_opp1 = 1 + len(nxt_snap["opp_side"]["pokemon"][1:])
    if n_opp1 < n_opp0 or (opp1["species"] != opp0["species"] and opp0.get("fainted")):
        opp_ko = True
    if my_ko or opp_ko:
        ev.add("ft")
    if my and my.startswith("M:"):
        _eff_events(ev, my[2:], opp0["species"])
    if op and op.startswith("M:"):
        _eff_events(ev, op[2:], snap["my_side"]["pokemon"][0]["species"])
    return {
        "my": my,
        "op": op,
        "dm": _DMG_KO if my_ko else _dmg_bucket(dm),
        "do": _DMG_KO if opp_ko else do,
        "ev": sorted(ev),
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
    snaps = [_state_json(s) for s in states]
    trans = [
        _transition(snaps[j], snaps[j + 1], states[j + 1], actions[j])
        for j in range(len(states) - 1)
    ]
    rows = []
    for turn, (state, action) in enumerate(zip(states, actions)):
        if action < 0 or action > 8:
            continue
        snap = json.loads(json.dumps(snaps[turn]))  # rows own their snapshots
        snap["history_tail"] = [
            {"o": -(k + 1), **trans[j]}
            for k, j in enumerate(range(turn - 1, max(turn - 1 - HIST_K, -1), -1))
        ]
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
