"""Team-general team sampling (SPECS §5.0.4).

Parses Showdown-export team files from the metamon-teams corpus and mixes
three sources per sampled team: the 10 built-in standard sets (continuity
with historical evals), curated competitive teams, and the paper_variety
pool (off-meta species/movesets - the corpus's stand-in for "random legal
teams"). Teams failing canon species/move validation are skipped at load.
"""

from __future__ import annotations

import random
import tarfile
from pathlib import Path

from sim.pack import PokemonSpec
from sim.teams import sample_team

TEAMS_ROOT = Path(__file__).resolve().parents[1] / "datasets" / "raw" / "metamon" / "jakegrigsby__metamon-teams"

_POOL_TARS = {
    "competitive": "competitive/gen1ou.tar.gz",
    "variety": "paper_variety/gen1ou.tar.gz",
    "replays": "modern_replays/gen1ou.tar.gz",
}
_SKIP_PREFIXES = ("Ability:", "EVs:", "IVs:", "Level:", "Shiny:", "Happiness:")


def _species_from_header(line: str) -> str:
    name = line.split(" @ ")[0].strip().rstrip("@").strip()
    if name.endswith(")") and "(" in name:  # "Nickname (Species)"
        inner = name[name.rfind("(") + 1 : -1].strip()
        if inner not in ("M", "F"):
            return inner
        name = name[: name.rfind("(")].strip()
    return name


def parse_team_export(text: str) -> list[tuple[str, list[str]]]:
    """One Showdown-export team file -> raw (species, moves) pairs.
    PokemonSpec construction happens after canon validation - PokemonSpec
    rejects unknown species eagerly."""
    team: list[tuple[str, list[str]]] = []
    for block in text.split("\n\n"):
        lines = [ln.strip() for ln in block.strip().splitlines() if ln.strip()]
        if not lines:
            continue
        moves = [ln[2:].strip() for ln in lines if ln.startswith("- ")]
        header = lines[0]
        if header.startswith("- ") or header.startswith(_SKIP_PREFIXES):
            continue  # continuation garbage, not a mon header
        if ":" in header.split(" @ ")[0]:
            continue  # metadata line (e.g. "filename: ..."), not a species
        team.append((_species_from_header(header), moves[:4]))
    return team


def _validated(team: list[tuple[str, list[str]]]) -> list[PokemonSpec] | None:
    from data.normalize import canon_move, canon_species

    if len(team) != 6:
        return None
    out = []
    for raw_species, raw_moves in team:
        species = canon_species(raw_species)
        if species is None or not raw_moves:
            return None
        moves = [canon_move(m) for m in raw_moves]
        if any(m is None for m in moves):
            return None
        out.append(PokemonSpec(species, moves))
    return out


def load_pool(tar_path: Path) -> list[list[PokemonSpec]]:
    pool = []
    with tarfile.open(tar_path, "r:gz") as tar:
        for member in sorted(tar.getmembers(), key=lambda m: m.name):
            if not member.isfile():
                continue
            f = tar.extractfile(member)
            if f is None:
                continue
            team = _validated(parse_team_export(f.read().decode("utf-8", "replace")))
            if team is not None:
                pool.append(team)
    return pool


def pools_available(root: Path | str = TEAMS_ROOT) -> bool:
    """True when every pool tarball is on disk. The corpus is downloaded, not
    committed (`python -m data pull --source metamon --only teams`), so callers
    and tests that need it check here instead of failing on the first open."""
    root = Path(root)
    return all((root / rel).exists() for rel in _POOL_TARS.values())


class TeamSampler:
    """sample(rng): 20% built-in standard sets, 40% competitive, 40% variety.
    Deterministic given the rng; pool order is sorted at load."""

    def __init__(self, root: Path | str = TEAMS_ROOT, standard_frac: float = 0.2):
        root = Path(root)
        self.standard_frac = standard_frac
        self.pools: dict[str, list[list[PokemonSpec]]] = {}
        for name, rel in _POOL_TARS.items():
            path = root / rel
            if not path.exists():
                raise FileNotFoundError(f"team pool missing: {path}")
            pool = load_pool(path)
            if not pool:
                raise ValueError(f"no valid teams in {path}")
            self.pools[name] = pool

    def sample(self, rng: random.Random) -> list[PokemonSpec]:
        r = rng.random()
        if r < self.standard_frac:
            return sample_team(rng)
        pool = self.pools["competitive"] if r < self.standard_frac + 0.4 else self.pools["variety"]
        team = rng.choice(pool)
        return rng.sample(team, len(team))  # shuffled copy: lead varies


_SAMPLER: TeamSampler | None = None


def mixed_sample_team(rng: random.Random) -> list[PokemonSpec]:
    """Module-level sampler with lazy per-process pool loading (safe for
    multiprocessing workers)."""
    global _SAMPLER
    if _SAMPLER is None:
        _SAMPLER = TeamSampler()
    return _SAMPLER.sample(rng)
