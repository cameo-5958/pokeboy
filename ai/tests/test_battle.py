import random

from sim.agents import MaxDamageBot, RandomBot
from sim.battle import _ORDER_OFF, _POKE, _SIDE, SPECIES_BY_ID, Battle, run_battle
from sim.pack import PokemonSpec
from sim.teams import sample_team

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


def _buffer_active_species(b: Battle, side: int) -> str | None:
    """Engine truth (gen1 README + data.zig get()): order[pos] holds the
    1-based party id of the mon at battle position pos+1; position 1 = active."""
    buf = b.raw.bytes
    party_id = buf[_SIDE[side] + _ORDER_OFF]
    o = _SIDE[side] + (party_id - 1) * _POKE
    return SPECIES_BY_ID.get(buf[o + 21])


def test_active_matches_engine_order_truth():
    rng = random.Random(0)
    for _ in range(20):
        b = Battle(sample_team(rng), sample_team(rng), seed=rng.randrange(2**63))
        for _ in range(120):
            if b.winner:
                break
            for player in (1, 2):
                s = b.state(player)
                listed = s.my_side["pokemon"][s.my_side["active_ix"]]["species"]
                assert listed == _buffer_active_species(b, player - 1)
            s1, s2 = b.state(1), b.state(2)
            b.step(rng.choice(s1.legal_actions), rng.choice(s2.legal_actions))


def test_switch_action_targets_listed_bench_mon():
    """schema_v1 contract (convert_showdown.py): action 4+j switches to the
    j-th mon of the state's pokemon listing minus the active one."""
    rng = random.Random(1)
    checks = 0
    for _ in range(20):
        b = Battle(sample_team(rng), sample_team(rng), seed=rng.randrange(2**63))
        for _ in range(120):
            if b.winner:
                break
            s1, s2 = b.state(1), b.state(2)
            switches = [a for a in s1.legal_actions if 4 <= a <= 8]
            if switches:
                a1 = rng.choice(switches)
                mons = s1.my_side["pokemon"]
                ix = s1.my_side["active_ix"]
                bench = mons[:ix] + mons[ix + 1 :]
                expected = bench[a1 - 4]["species"]
            else:
                a1 = rng.choice(s1.legal_actions)
                expected = None
            b.step(a1, rng.choice(s2.legal_actions))
            if expected is not None:
                after = b.state(1)
                got = after.my_side["pokemon"][after.my_side["active_ix"]]["species"]
                assert got == expected, f"switch {a1} brought in {got}, wanted {expected}"
                checks += 1
    assert checks > 100, f"only {checks} switch decisions exercised"


def test_maxdamage_beats_random_usually():
    wins = 0
    for i in range(30):
        rec = run_battle(MaxDamageBot(), RandomBot(i), T1, T2, seed=i)
        wins += rec.winner == "p1"
    assert wins >= 18
