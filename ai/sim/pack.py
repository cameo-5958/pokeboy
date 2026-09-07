"""Serialize teams into the engine's 384-byte Gen 1 battle structure.

Layout per vendor/engine/src/lib/gen1/README.md#layout (showdown build):
  0-184   side 1  (6x24 pokemon, 32 active, 6 order, 2 last-move bytes)
  184-368 side 2
  368-370 turn (u16 LE)
  370-372 last_damage (u16 LE)
  372-376 last_moves (showdown: selected/counterable per side)
  376-384 RNG seed (u64 LE)
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field

from sim.gen1data import MOVES, SPECIES, TYPES

BATTLE_SIZE = 384
SIDE_SIZE = 184
POKEMON_SIZE = 24


@dataclass(frozen=True)
class PokemonSpec:
    species: str
    moves: list[str]
    level: int = 100
    # (hp, atk, def, spe, spc) — hp DV is normally derived from the others in
    # real games; 15s across the board is the standard competitive assumption.
    dvs: tuple[int, int, int, int, int] = (15, 15, 15, 15, 15)
    statexp: tuple[int, int, int, int, int] = field(
        default=(65535, 65535, 65535, 65535, 65535)
    )

    def __post_init__(self) -> None:
        if self.species not in SPECIES:
            raise ValueError(f"unknown species {self.species!r}")
        if not 1 <= len(self.moves) <= 4:
            raise ValueError("1-4 moves required")
        for m in self.moves:
            if m not in MOVES:
                raise ValueError(f"unknown move {m!r}")


def _stat(base: int, dv: int, statexp: int, level: int, hp: bool) -> int:
    core = ((base + dv) * 2 + math.ceil(math.sqrt(statexp)) // 4) * level // 100
    return core + level + 10 if hp else core + 5


def compute_stats(spec: PokemonSpec) -> dict[str, int]:
    _, bhp, batk, bdef, bspe, bspc, _, _ = SPECIES[spec.species]
    bases = {"hp": bhp, "atk": batk, "def": bdef, "spe": bspe, "spc": bspc}
    out = {}
    for i, name in enumerate(("hp", "atk", "def", "spe", "spc")):
        out[name] = _stat(
            bases[name], spec.dvs[i], spec.statexp[i], spec.level, hp=(name == "hp")
        )
    return out


def max_pp(move: str) -> int:
    """PP with 3 PP Ups applied (Showdown default)."""
    return min(61, MOVES[move][1] * 8 // 5)


def pack_pokemon(spec: PokemonSpec) -> bytes:
    sid, _, _, _, _, _, t1, t2 = SPECIES[spec.species]
    stats = compute_stats(spec)
    buf = struct.pack(
        "<HHHHH", stats["hp"], stats["atk"], stats["def"], stats["spe"], stats["spc"]
    )
    for i in range(4):
        if i < len(spec.moves):
            buf += bytes([MOVES[spec.moves[i]][0], max_pp(spec.moves[i])])
        else:
            buf += bytes([0, 0])
    types_byte = TYPES.index(t1) | (TYPES.index(t2) << 4)
    buf += struct.pack("<H", stats["hp"])  # current hp = max
    buf += bytes([0, sid, types_byte, spec.level])
    assert len(buf) == POKEMON_SIZE
    return buf


def pack_side(team: list[PokemonSpec]) -> bytes:
    if not 1 <= len(team) <= 6:
        raise ValueError("1-6 pokemon per side")
    buf = b"".join(pack_pokemon(p) for p in team)
    buf += bytes(POKEMON_SIZE) * (6 - len(team))
    buf += bytes(32)  # active pokemon: filled by the engine on switch-in
    order = [i + 1 if i < len(team) else 0 for i in range(6)]
    buf += bytes(order)
    buf += bytes([0, 0])  # last_selected_move, last_used_move
    assert len(buf) == SIDE_SIZE
    return buf


def pack_battle(
    team1: list[PokemonSpec], team2: list[PokemonSpec], seed: int
) -> bytes:
    buf = pack_side(team1) + pack_side(team2)
    buf += struct.pack("<HH", 0, 0)  # turn, last_damage
    buf += bytes(4)  # last_moves (showdown layout)
    buf += struct.pack("<Q", seed & 0xFFFFFFFFFFFFFFFF)
    assert len(buf) == BATTLE_SIZE
    return buf
