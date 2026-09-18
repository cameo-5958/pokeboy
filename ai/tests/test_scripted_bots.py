"""UniversalMaxDamageBot: best damage across the WHOLE team - switches to the
bench mon with the strongest move vs the opponent's active if it beats the
active's best. LessEffectiveMaxDamageBot: pivots to the team member taking
the least damage from the opponent's best (revealed or type-inferred) attack,
then attacks with its own max-damage move."""

import random

from sim.agents import LessEffectiveMaxDamageBot, MaxDamageBot, UniversalMaxDamageBot
from sim.schema import State


def _mon(species, moves, hp=1.0, fainted=False):
    return {
        "species": species,
        "hp_fraction": hp,
        "status": None,
        "fainted": fainted,
        "moves": [{"id": m, "pp": 10} for m in moves],
    }


def _opp(species, revealed=()):
    return {
        "species": species,
        "hp_fraction": 1.0,
        "status": None,
        "fainted": False,
        "revealed_moves": list(revealed),
    }


def _state(mine, opp, legal, kind="turn"):
    return State(
        battle_id="t", turn=5, request_kind=kind,
        my_side={"active_ix": 0, "pokemon": mine},
        opp_side={"active_ix": 0, "pokemon": [opp]},
        legal_actions=legal,
    )


def test_umd_attacks_when_active_is_best():
    # Tauros Blizzard vs Rhydon (2x, 120bp) beats bench Alakazam's Psychic
    s = _state(
        [_mon("Tauros", ["Body Slam", "Blizzard"]),
         _mon("Alakazam", ["Psychic", "Recover"])],
        _opp("Rhydon"),
        legal=[0, 1, 4],
    )
    assert UniversalMaxDamageBot().choose(s) == 1  # Blizzard, no switch


def test_umd_switches_to_stronger_bench_attacker():
    # Active Rhydon has only Body Slam into Gengar (immune, score 0);
    # bench Starmie's Surf hits - universal max damage means switching.
    s = _state(
        [_mon("Rhydon", ["Body Slam"]),
         _mon("Starmie", ["Surf", "Recover"])],
        _opp("Gengar"),
        legal=[0, 4],
    )
    assert UniversalMaxDamageBot().choose(s) == 4
    assert MaxDamageBot().choose(s) == 0  # the old bot stays and plinks


def test_umd_forced_switch_picks_best_attacker():
    s = _state(
        [_mon("Chansey", ["Ice Beam"], hp=0.0, fainted=True),
         _mon("Jynx", ["Blizzard", "Psychic"]),
         _mon("Alakazam", ["Seismic Toss"])],
        _opp("Rhydon"),
        legal=[4, 5],
        kind="force_switch",
    )
    assert UniversalMaxDamageBot().choose(s) == 4  # Jynx: Blizzard 2x 120bp


def test_lemd_pivots_to_the_wall():
    # Opp Zapdos revealed Thunderbolt: Starmie takes 2x, Rhydon is immune.
    s = _state(
        [_mon("Starmie", ["Surf", "Blizzard"]),
         _mon("Rhydon", ["Earthquake", "Rock Slide"])],
        _opp("Zapdos", revealed=["Thunderbolt"]),
        legal=[0, 1, 4],
    )
    assert LessEffectiveMaxDamageBot().choose(s) == 4


def test_lemd_attacks_once_wall_is_in():
    # Rhydon active vs Zapdos w/ Thunderbolt: nothing takes less than immune.
    # Rock Slide (2x vs Flying, 75bp, STAB) is Rhydon's max-damage attack.
    s = _state(
        [_mon("Rhydon", ["Earthquake", "Rock Slide"]),
         _mon("Starmie", ["Surf", "Blizzard"])],
        _opp("Zapdos", revealed=["Thunderbolt"]),
        legal=[0, 1, 4],
    )
    assert LessEffectiveMaxDamageBot().choose(s) == 1


def test_lemd_infers_from_types_when_nothing_revealed():
    # No revealed moves: assume STAB from Zapdos's Electric/Flying typing.
    s = _state(
        [_mon("Starmie", ["Surf"]),
         _mon("Rhydon", ["Earthquake"])],
        _opp("Zapdos"),
        legal=[0, 4],
    )
    assert LessEffectiveMaxDamageBot().choose(s) == 4  # Rhydon walls Electric


def test_bots_stay_legal_in_random_battles():
    from sim.battle import run_battle
    from sim.teams import sample_team

    rng = random.Random(11)
    for bot in (UniversalMaxDamageBot(), LessEffectiveMaxDamageBot()):
        for _ in range(3):
            rec = run_battle(bot, MaxDamageBot(), sample_team(rng), sample_team(rng),
                             seed=rng.randrange(2**63), max_turns=500)
            assert rec.winner in ("p1", "p2", "tie", "unfinished")
