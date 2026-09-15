import random

from sim import trainers
from sim.matchups import max_level, parse_mix, sample_matchup, sample_pair


def test_parse_mix_normalises():
    mix = parse_mix("mirror:1,balanced:3")
    assert [m for m, _ in mix] == ["mirror", "balanced"]
    assert abs(sum(w for _, w in mix) - 1.0) < 1e-9 and abs(mix[1][1] - 0.75) < 1e-9
    assert parse_mix("random") == [("random", 1.0)]


def test_modes_respect_their_constraints():
    parties = trainers.load().parties
    rng = random.Random(0)
    for _ in range(200):
        t, o = sample_pair(rng, parties, "mirror")
        assert t == o
        t, o = sample_pair(rng, parties, "balanced", gap=2)
        assert abs(max_level(parties[t]) - max_level(parties[o])) <= 2
    modes = {sample_matchup(rng, parties, parse_mix("mirror:0.5,balanced:0.5"))[2] for _ in range(100)}
    assert modes == {"mirror", "balanced"}
