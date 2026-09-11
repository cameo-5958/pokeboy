"""Matchup sampling over the ROM trainer parties (spec §10, matchup distributions).

Two random trainer parties differ by 10 levels at the median, so most such battles are
decided by the draw rather than by play. `balanced` keeps only pairs whose strongest
Pokémon are within `gap` levels; `mirror` gives both seats the same party. A mix such as
"mirror:0.5,balanced:0.5" samples the mode per battle.
"""
from __future__ import annotations

import random

MODES = ("random", "balanced", "mirror")


def max_level(party) -> int:
    return max(m.level for m in party.mons)


def parse_mix(spec: str) -> list[tuple[str, float]]:
    """"mirror" or "mirror:0.5,balanced:0.5" -> [(mode, weight)] with weights summing to 1."""
    out: list[tuple[str, float]] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        mode, _, w = part.partition(":")
        if mode not in MODES:
            raise ValueError(f"unknown matchup mode {mode!r}; choose from {MODES}")
        out.append((mode, float(w) if w else 1.0))
    total = sum(w for _, w in out)
    if not out or total <= 0:
        raise ValueError(f"empty matchup mix {spec!r}")
    return [(m, w / total) for m, w in out]


def sample_mode(rng: random.Random, mix: list[tuple[str, float]]) -> str:
    r = rng.random()
    for mode, w in mix:
        r -= w
        if r < 0:
            return mode
    return mix[-1][0]


def sample_pair(rng: random.Random, parties, mode: str, gap: int = 2) -> tuple[int, int]:
    """(trainer party index, player party index) for one battle."""
    n = len(parties)
    t = rng.randrange(n)
    if mode == "mirror":
        return t, t
    if mode == "random":
        return t, rng.randrange(n)
    if mode == "balanced":
        lt = max_level(parties[t])
        for _ in range(10_000):
            o = rng.randrange(n)
            if abs(max_level(parties[o]) - lt) <= gap:
                return t, o
        return t, t
    raise ValueError(mode)


def sample_matchup(rng: random.Random, parties, mix: list[tuple[str, float]], gap: int = 2) -> tuple[int, int, str]:
    mode = sample_mode(rng, mix)
    t, o = sample_pair(rng, parties, mode, gap)
    return t, o, mode
