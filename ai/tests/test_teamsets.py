"""TeamSampler (SPECS §5.0.4): team-general training/eval teams from the
metamon-teams corpus (competitive + paper_variety gen1ou pools) mixed with
the built-in standard sets."""

import random
from pathlib import Path

import pytest

from sim.pack import PokemonSpec
from sim.teamsets import TEAMS_ROOT, TeamSampler, parse_team_export

CORPUS = Path(TEAMS_ROOT)
needs_corpus = pytest.mark.skipif(
    not (CORPUS / "competitive" / "gen1ou.tar.gz").exists(),
    reason="metamon-teams corpus not on disk",
)

EXPORT_PLAIN = """Alakazam
EVs: 252 HP / 252 Def / 252 SpA / 252 SpD / 252 Spe
IVs: 2 Atk
- Thunder Wave
- Seismic Toss
- Psychic
- Recover

Snorlax
- Body Slam
- Earthquake
- Hyper Beam
- Self-Destruct
"""

EXPORT_FLUFF = """Squirtle @
Ability: No Ability
EVs: 252 HP / 0 Atk / 252 Def / 252 SpA / 252 SpD / 252 Spe
Serious Nature
IVs: 31 HP / 31 Atk / 31 Def / 31 SpA / 31 SpD / 31 Spe
- Surf
- Ice Beam
- Tackle
- Bubble Beam

Zappy (Pikachu) @
Ability: No Ability
- Thunder
- Surf
"""


def test_parse_team_export_plain():
    team = parse_team_export(EXPORT_PLAIN)
    assert [sp for sp, _ in team] == ["Alakazam", "Snorlax"]
    assert team[0][1] == ["Thunder Wave", "Seismic Toss", "Psychic", "Recover"]
    assert team[1][1][-1] == "Self-Destruct"


def test_parse_team_export_skips_fluff_and_nicknames():
    team = parse_team_export(EXPORT_FLUFF)
    assert [sp for sp, _ in team] == ["Squirtle", "Pikachu"]
    assert team[0][1] == ["Surf", "Ice Beam", "Tackle", "Bubble Beam"]
    assert team[1][1] == ["Thunder", "Surf"]


@needs_corpus
def test_pools_load_and_validate():
    s = TeamSampler()
    assert len(s.pools["competitive"]) >= 20
    assert len(s.pools["variety"]) >= 500
    for pool in s.pools.values():
        for team in pool[:50]:
            assert len(team) == 6
            for mon in team:
                assert 1 <= len(mon.moves) <= 4


@needs_corpus
def test_sampler_is_deterministic_and_mixed():
    a = TeamSampler()
    b = TeamSampler()
    sa = [tuple(m.species for m in a.sample(random.Random(i))) for i in range(60)]
    sb = [tuple(m.species for m in b.sample(random.Random(i))) for i in range(60)]
    assert sa == sb  # same seed, same teams
    standard = {"Tauros", "Snorlax", "Chansey", "Alakazam", "Starmie",
                "Exeggutor", "Rhydon", "Zapdos", "Jynx", "Gengar"}
    species_seen = {sp for team in sa for sp in team}
    assert species_seen - standard, "sampler never leaves the 10 standard sets"


@needs_corpus
def test_sampled_teams_battle_cleanly():
    from sim.battle import run_battle
    from sim.agents import RandomBot

    s = TeamSampler()
    rng = random.Random(3)
    for _ in range(5):
        rec = run_battle(RandomBot(1), RandomBot(2), s.sample(rng), s.sample(rng),
                         seed=rng.randrange(2**63), max_turns=500)
        assert rec.winner in ("p1", "p2", "tie", "unfinished")
        assert rec.turns
