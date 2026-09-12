"""Matchup sampling over the ROM trainer parties (spec §10, matchup distributions).

Two random trainer parties differ by 10 levels at the median, so most such battles are
decided by the draw rather than by play. `balanced` keeps only pairs whose strongest
Pokémon are within `gap` levels; `mirror` gives both seats the same party. A mix such as
"mirror:0.5,balanced:0.5" samples the mode per battle.
"""
from __future__ import annotations

import random

MODES = ("random", "balanced", "mirror", "ou")


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


class OUParty:
    """A level-100 OU team in the shape sample_pair / TrainerEnv expect from a ROM party (no trainer class)."""

    class_id = 0

    def __init__(self, specs, index: int, pool: str):
        self.specs = list(specs); self.index = index; self.pool = pool
        self.mons = self.specs   # PokemonSpec has .level

    def to_specs(self):
        return self.specs


def load_ou_parties(seed: int = 0, shuffle_leads: bool = True) -> list[OUParty]:
    """Every team from the Metamon competitive / variety / replay pools plus the built-in standard sets.
    With shuffle_leads, each team is also added in one shuffled order so leads vary. Parsing the ~14k
    replay teams takes ~100 s, so the result is pickled next to the tarballs and reused."""
    import pickle
    from sim.teams import STANDARD_SETS
    from sim.teamsets import TEAMS_ROOT, TeamSampler
    cache = TEAMS_ROOT / f"ou_parties-s{seed}-{int(shuffle_leads)}.pkl"
    if cache.exists():
        with open(cache, "rb") as fh:
            return pickle.load(fh)
    rng = random.Random(seed)
    out: list[OUParty] = []
    pools = dict(TeamSampler().pools)
    pools["standard"] = [rng.sample(STANDARD_SETS, 6) for _ in range(40)]
    for name, teams in pools.items():
        for i, team in enumerate(teams):
            out.append(OUParty(team, i, name))
            if shuffle_leads:
                out.append(OUParty(rng.sample(team, len(team)), i, name))
    with open(cache, "wb") as fh:
        pickle.dump(out, fh)
    return out


def sample_pair(rng: random.Random, parties, mode: str, gap: int = 2, ou_range: tuple[int, int] | None = None) -> tuple[int, int]:
    """(trainer party index, player party index) for one battle. `ou_range` = [start, end) of OU parties
    appended to the ROM parties; `ou` draws both sides from it, the other modes stay inside the ROM range."""
    n = len(parties) if ou_range is None else ou_range[0]
    if mode == "ou":
        if ou_range is None:
            raise ValueError("ou matchups need ou_range")
        a, b = ou_range
        return rng.randrange(a, b), rng.randrange(a, b)
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


def sample_matchup(rng: random.Random, parties, mix: list[tuple[str, float]], gap: int = 2,
                   ou_range: tuple[int, int] | None = None) -> tuple[int, int, str]:
    mode = sample_mode(rng, mix)
    t, o = sample_pair(rng, parties, mode, gap, ou_range)
    return t, o, mode


def parties_for(mix: list[tuple[str, float]], rom_parties, seed: int = 0):
    """(parties, ou_range): ROM parties plus the OU pool when the mix uses `ou`."""
    parties = list(rom_parties)
    if any(m == "ou" for m, _ in mix):
        ou = load_ou_parties(seed)
        return parties + ou, (len(rom_parties), len(rom_parties) + len(ou))
    return parties, None
