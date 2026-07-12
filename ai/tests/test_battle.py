from sim.agents import MaxDamageBot, RandomBot
from sim.battle import Battle, run_battle
from sim.pack import PokemonSpec

T1 = [
    PokemonSpec("Snorlax", ["Body Slam", "Reflect", "Rest", "Ice Beam"]),
    PokemonSpec("Tauros", ["Body Slam", "Hyper Beam", "Blizzard", "Earthquake"]),
]
T2 = [
    PokemonSpec("Chansey", ["Ice Beam", "Thunderbolt", "Soft-Boiled", "Thunder Wave"]),
    PokemonSpec("Alakazam", ["Psychic", "Seismic Toss", "Recover", "Thunder Wave"]),
]


def test_state_shape_and_hiding():
    b = Battle(T1, T2, seed=1)
    s = b.state(player=1)
    assert s.schema_v == 1
    assert s.turn == 1
    assert s.request_kind == "turn"
    assert s.legal_actions
    mine = s.my_side["pokemon"]
    assert mine[0]["species"] == "Snorlax"
    assert mine[0]["hp"] == mine[0]["max_hp"] > 0
    assert [m["id"] for m in mine[0]["moves"]] == ["Body Slam", "Reflect", "Rest", "Ice Beam"]
    opp = s.opp_side["pokemon"]
    # lead is revealed on switch-in, but its moves are not
    assert opp[0]["species"] == "Chansey"
    assert opp[0]["revealed_moves"] == []
    # bench is fully hidden
    assert opp[1]["species"] is None


def test_moves_get_revealed_by_use():
    b = Battle(T1, T2, seed=5)
    for _ in range(10):
        if b.winner:
            break
        # both spam their first move slot when legal, else first legal action
        acts = [
            0 if 0 in b.state(p).legal_actions else b.state(p).legal_actions[0]
            for p in (1, 2)
        ]
        b.step(*acts)
    s = b.state(player=1)
    assert "Ice Beam" in s.opp_side["pokemon"][0]["revealed_moves"]


def test_state_json_roundtrip():
    import json

    s = Battle(T1, T2, seed=2).state(player=2)
    d = json.loads(json.dumps(s.to_json()))
    assert d["schema_v"] == 1
    assert d["my_side"]["pokemon"][0]["species"] == "Chansey"


def test_run_battle_random_vs_random():
    rec = run_battle(RandomBot(3), RandomBot(4), T1, T2, seed=99)
    assert rec.winner in ("p1", "p2", "tie")
    assert len(rec.turns) > 0


def test_maxdamage_beats_random_usually():
    wins = 0
    for i in range(30):
        rec = run_battle(MaxDamageBot(), RandomBot(i), T1, T2, seed=i)
        wins += rec.winner == "p1"
    assert wins >= 18
