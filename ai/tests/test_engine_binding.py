from sim.engine import RawBattle
from sim.pack import PokemonSpec, pack_battle

TEAM = [PokemonSpec("Tauros", ["Body Slam", "Hyper Beam", "Blizzard", "Earthquake"])]


def test_pack_battle_size():
    buf = pack_battle(TEAM, TEAM, seed=1)
    assert len(buf) == 384


def test_battle_runs_to_completion():
    b = RawBattle(pack_battle(TEAM, TEAM, seed=123), seed=123)
    result, turns = b.play_out_random(max_turns=1000)
    assert result in ("p1", "p2", "tie")
    assert turns > 0


def test_determinism():
    outs = []
    for _ in range(2):
        b = RawBattle(pack_battle(TEAM, TEAM, seed=42), seed=42)
        outs.append(b.play_out_random(max_turns=1000, policy_seed=7))
    assert outs[0] == outs[1]


def test_different_seeds_diverge():
    results = set()
    for seed in range(20):
        b = RawBattle(pack_battle(TEAM, TEAM, seed=seed), seed=seed)
        results.add(b.play_out_random(max_turns=1000, policy_seed=seed))
    assert len(results) > 1
