"""Rebuild a playable engine battle from a public schema_v1 state.

The serve-time-search foundation: my side comes fully from the state; the
opponent's hidden slots and movesets are filled from a caller-provided
candidate team (see sample_opponent_team for a usage-realistic sampler).
Both sides are rotated so the active mon sits at slot 0, and hp/status/pp
patches land on the packed buffer BEFORE the engine's initial switch-in —
so paralysis/burn stat modifiers apply to the reconstructed active mon.

Known approximation: volatile state (stat stages, Reflect, confusion,
Substitute, trap/recharge locks) is not in the public state and resets.
The model itself never sees those either, so search and policy share one
blind spot rather than gaining a new one.
"""

from __future__ import annotations

import random
from typing import Any

from sim.battle import _POKE, _SIDE, _TURN_OFF, Battle
from sim.pack import PokemonSpec, pack_battle

# gen1 status byte: sleep counter in bits 0-2, then PSN/BRN/FRZ/PAR bits.
# An unknown sleep counter becomes "2 turns left" — middle of the road.
_STATUS_BITS = {"SLP": 0x02, "PSN": 0x08, "BRN": 0x10, "FRZ": 0x20, "PAR": 0x40}


def _mon_norm(mon: dict[str, Any], mine: bool) -> dict[str, Any]:
    """Normalize sim-shape (hp/max_hp, move dicts) and bridge-shape
    (hp_fraction, move-name lists + pp list) mons to one row."""
    if mine:
        moves: list[tuple[str, int | None]] = []
        for i, m in enumerate(mon.get("moves") or []):
            if isinstance(m, dict):
                moves.append((m["id"], m.get("pp")))
            else:
                pp = mon.get("pp")
                moves.append((m, pp[i] if pp and i < len(pp) else None))
    else:
        moves = [(m, None) for m in (mon.get("revealed_moves") or [])]
    if mon.get("hp_fraction") is not None:
        frac = float(mon["hp_fraction"])
    elif mon.get("max_hp"):
        frac = mon.get("hp", 0) / mon["max_hp"]
    else:
        frac = None  # unrevealed
    fainted = bool(mon.get("fainted")) or frac == 0.0
    return {"species": mon.get("species"), "frac": frac, "fainted": fainted,
            "status": mon.get("status"), "moves": moves}


def _patch_mon(packed: bytearray, side: int, pos: int, row: dict[str, Any]) -> None:
    o = _SIDE[side] + pos * _POKE
    max_hp = int.from_bytes(packed[o : o + 2], "little")
    frac = 1.0 if row["frac"] is None else row["frac"]
    hp = 0 if row["fainted"] or frac <= 0 else max(1, round(frac * max_hp))
    packed[o + 18 : o + 20] = hp.to_bytes(2, "little")
    packed[o + 20] = 0 if hp == 0 else _STATUS_BITS.get(row["status"], 0)
    for m, (_, pp) in enumerate(row["moves"][:4]):
        if pp is not None:
            packed[o + 11 + 2 * m] = pp


def battle_from_state(state: dict[str, Any], opp_team: list[PokemonSpec],
                      seed: int) -> tuple[Battle, dict[int, int]]:
    """Build a Battle at the state's position; player 1 is the state owner.

    opp_team must align with the state's opp listing (slot i fills listed
    mon i; unrevealed slots take the candidate as-is). Returns the battle
    plus {reconstructed action int -> original action int} — identity here
    because both sides keep the state's listing order minus the active.
    """
    my_side, opp_side = state["my_side"], state["opp_side"]
    my_rows = [_mon_norm(m, True) for m in my_side["pokemon"]]
    opp_rows = [_mon_norm(m, False) for m in opp_side["pokemon"]]
    opp_rows = opp_rows[: len(opp_team)]
    a_my = my_side.get("active_ix", 0)
    a_opp = opp_side.get("active_ix", 0)

    my_order = [a_my] + [i for i in range(len(my_rows)) if i != a_my]
    opp_order = [a_opp] + [i for i in range(len(opp_team)) if i != a_opp]

    my_specs = [
        PokemonSpec(species=my_rows[i]["species"],
                    moves=[n for n, _ in my_rows[i]["moves"]])
        for i in my_order
    ]
    opp_specs = [opp_team[i] for i in opp_order]

    packed = bytearray(pack_battle(my_specs, opp_specs, seed))
    for pos, i in enumerate(my_order):
        _patch_mon(packed, 0, pos, my_rows[i])
    for pos, i in enumerate(opp_order):
        if i < len(opp_rows):
            _patch_mon(packed, 1, pos, opp_rows[i])
    b = Battle(my_specs, opp_specs, seed=seed, packed=bytes(packed))
    # the turn must be patched AFTER construction (a nonzero turn makes the
    # engine treat the init update(0,0) as an in-progress pass-turn and the
    # leads never switch in) and IN PLACE (the request state lives in the
    # wrapper's last_result, which rebuilding would reset).
    b.raw._buf[_TURN_OFF : _TURN_OFF + 2] = int(state.get("turn", 0)).to_bytes(2, "little")
    amap = {a: a for a in b.state(1).legal_actions}
    return b, amap


# -- opponent-team sampling (usage-realistic hidden-info fill) -------------

_POOL: tuple[dict[str, list[list[str]]], list[str]] | None = None


def _pool_index() -> tuple[dict[str, list[list[str]]], list[str]]:
    """species -> candidate movesets, plus a frequency-weighted species list
    drawn from the same team pools the benchmark distribution uses."""
    global _POOL
    if _POOL is None:
        from sim.teams import STANDARD_SETS
        from sim.teamsets import TeamSampler

        sets: dict[str, list[list[str]]] = {}
        freq: list[str] = []
        for spec in STANDARD_SETS:
            sets.setdefault(spec.species, []).append(list(spec.moves))
            freq.append(spec.species)
        for pool in TeamSampler().pools.values():
            for team in pool:
                for spec in team:
                    sets.setdefault(spec.species, []).append(list(spec.moves))
                    freq.append(spec.species)
        _POOL = (sets, freq)
    return _POOL


def sample_opponent_team(state: dict[str, Any], rng: random.Random) -> list[PokemonSpec]:
    """A full 6-mon opponent team consistent with the state's revelations."""
    sets, freq = _pool_index()
    listed = state["opp_side"]["pokemon"][:6]
    team: list[PokemonSpec | None] = []
    used: set[str] = set()

    for slot in listed:
        species = slot.get("species")
        if species is None:
            team.append(None)
            continue
        revealed = list(slot.get("revealed_moves") or [])
        cands = [ms for ms in sets.get(species, [])
                 if set(revealed) <= set(ms)]
        if cands:
            moves = list(rng.choice(cands))
        else:  # revealed moves not matching any known set: keep them, top up
            moves = list(revealed)
            extra = [m for ms in sets.get(species, []) for m in ms
                     if m not in moves]
            rng.shuffle(extra)
            moves += extra[: 4 - len(moves)]
        team.append(PokemonSpec(species=species, moves=moves or ["Tackle"]))
        used.add(species)

    while len(team) < 6:
        team.append(None)
    for i, entry in enumerate(team):
        if entry is not None:
            continue
        species = rng.choice(freq)
        while species in used:
            species = rng.choice(freq)
        used.add(species)
        team[i] = PokemonSpec(species=species, moves=list(rng.choice(sets[species])))
    return team  # type: ignore[return-value]
