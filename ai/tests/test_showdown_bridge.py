"""Showdown transport bridge (serve/showdown.py): poke-env Battle -> schema_v1
state + action-int -> order mapping. Translation is duck-typed so these tests
run without poke-env installed; the Player subclass imports poke-env lazily."""

import random

from serve.showdown import state_from_battle, team_to_showdown
from sim.teams import STANDARD_SETS


class Move:
    def __init__(self, mid, pp=10, base_power=0):
        self.id = mid
        self.current_pp = pp
        self.base_power = base_power


class Status:
    def __init__(self, name):
        self.name = name


class Mon:
    def __init__(self, species, hp=1.0, status=None, moves=(), fainted=False):
        self.species = species
        self.base_species = species
        self.current_hp_fraction = hp
        self.status = Status(status) if status else None
        self.moves = {m.id: m for m in moves}
        self.fainted = fainted


class FakeBattle:
    def __init__(self, active, bench, opp_active, opp_bench=(), available_moves=None,
                 force_switch=False, turn=7, tag="b1"):
        self.active_pokemon = active
        self.available_switches = list(bench)
        self.opponent_active_pokemon = opp_active
        self.team = {m.species: m for m in [active] + list(bench)}
        self.opponent_team = {m.species: m for m in [opp_active] + list(opp_bench)}
        self.available_moves = (list(active.moves.values())
                                if available_moves is None else available_moves)
        self.force_switch = force_switch
        self.turn = turn
        self.battle_tag = tag


def _tauros(**kw):
    return Mon("tauros", moves=[Move("bodyslam"), Move("hyperbeam", pp=5),
                                Move("blizzard"), Move("earthquake")], **kw)


def test_translation_basic():
    b = FakeBattle(
        active=_tauros(hp=0.62, status="PAR"),
        bench=[Mon("snorlax", moves=[Move("bodyslam")]),
               Mon("chansey", hp=0.3, moves=[Move("softboiled", pp=2)])],
        opp_active=Mon("starmie", hp=0.8, moves=[Move("psychic")]),
        opp_bench=[Mon("rhydon", hp=0.0, fainted=True)],
    )
    state, orders = state_from_battle(b)
    me = state["my_side"]["pokemon"]
    assert [m["species"] for m in me] == ["Tauros", "Snorlax", "Chansey"]
    assert me[0]["status"] == "PAR" and me[0]["hp_fraction"] == 0.62
    assert me[0]["moves"] == ["Body Slam", "Hyper Beam", "Blizzard", "Earthquake"]
    assert me[0]["pp"] == [10, 5, 10, 10]
    opp = state["opp_side"]["pokemon"]
    assert opp[0]["species"] == "Starmie" and opp[0]["revealed_moves"] == ["Psychic"]
    assert opp[1]["species"] == "Rhydon" and opp[1]["fainted"] is True
    assert sum(1 for m in opp if m["species"] is None) == 4  # unrevealed padding
    assert state["request_kind"] == "turn"
    # actions: 4 moves + 2 switches
    assert sorted(orders) == [0, 1, 2, 3, 4, 5]
    assert orders[1].id == "hyperbeam"
    assert orders[4].species == "snorlax"


def test_translation_disabled_move_and_force_switch():
    active = _tauros()
    b = FakeBattle(
        active=active,
        bench=[Mon("snorlax", moves=[Move("bodyslam")])],
        opp_active=Mon("starmie"),
        available_moves=[active.moves["blizzard"]],  # others disabled/out of pp
    )
    state, orders = state_from_battle(b)
    assert sorted(orders) == [2, 4]  # only Blizzard + the one switch
    assert set(state["legal_actions"]) == {2, 4}

    b2 = FakeBattle(active=active, bench=[Mon("snorlax", moves=[Move("bodyslam")])],
                    opp_active=Mon("starmie"), available_moves=[], force_switch=True)
    state2, orders2 = state_from_battle(b2)
    assert state2["request_kind"] == "force_switch"
    assert sorted(orders2) == [4]


def test_translation_struggle_maps_to_pass_action():
    active = _tauros()
    struggle = Move("struggle")
    b = FakeBattle(active=active, bench=[], opp_active=Mon("starmie"),
                   available_moves=[struggle])
    state, orders = state_from_battle(b)
    assert list(orders) == [9]
    assert orders[9] is struggle
    assert state["legal_actions"] == [9]


def test_team_export_round_trips_through_our_parser():
    from sim.teamsets import parse_team_export

    team = random.Random(3).sample(STANDARD_SETS, 6)
    paste = team_to_showdown(team)
    parsed = parse_team_export(paste)
    assert [sp for sp, _ in parsed] == [m.species for m in team]
    assert [mv for _, mv in parsed] == [m.moves for m in team]


def test_team_builder_resamples_per_battle():
    from serve.showdown import make_team_builder

    builder = make_team_builder(seed=1)
    packed = [builder.yield_team() for _ in range(4)]
    for team in packed:
        assert len(team.split("]")) == 6  # six mons, packed format
        assert all(mon.split("|")[4] for mon in team.split("]"))  # moves present
    assert len(set(packed)) > 1  # a fresh sample each battle, not one fixed team


def test_translation_handles_unrevealed_opponent_active():
    active = _tauros()
    b = FakeBattle(active=active, bench=[], opp_active=Mon("starmie"))
    b.opponent_active_pokemon = None
    b.opponent_team = {}
    state, orders = state_from_battle(b)
    opp = state["opp_side"]["pokemon"]
    assert opp[0]["species"] is None
    assert len(opp) == 6
    assert sorted(orders) == [0, 1, 2, 3]
