"""SearchTeacher (SPECS §5.1 synthetic teacher): depth-limited expectation-
aware maximin search over cloned engine buffers. Full-information by design —
legal for data generation (§4.3 asymmetry); the distilled student only ever
sees public tokens."""

import random

from sim.agents import MaxDamageBot, RandomBot
from sim.battle import Battle, run_battle
from sim.search import SearchTeacher, clone_raw, leaf_value
from sim.teams import sample_team


def _battle(seed=7):
    rng = random.Random(seed)
    return Battle(sample_team(rng), sample_team(rng), seed=rng.randrange(2**63))


def _first_pair(b):
    """One legal (c1, c2) engine-choice pair from the current position."""
    r1, r2 = b.raw.requests()
    return b.raw.choices(0, r1)[0], b.raw.choices(1, r2)[0]


def test_clone_same_seed_is_deterministic():
    b = _battle()
    c1, c2 = _first_pair(b)
    clones = [clone_raw(b.raw, b"\x07" * 8) for _ in range(2)]
    for c in clones:
        c.update(c1, c2)
    assert clones[0].bytes == clones[1].bytes
    assert b.raw.bytes != clones[0].bytes  # parent untouched


def test_clone_reseed_varies_rolls():
    b = _battle()
    c1, c2 = _first_pair(b)
    outcomes = set()
    for s in range(16):
        c = clone_raw(b.raw, s.to_bytes(8, "little"))
        c.update(c1, c2)
        outcomes.add(c.bytes)
    assert len(outcomes) > 1, "damage rolls did not vary across reseeded clones"


def test_leaf_value_antisymmetric():
    b = _battle(seed=11)
    rng = random.Random(0)
    for _ in range(15):
        if b.winner:
            break
        s1, s2 = b.state(1), b.state(2)
        b.step(rng.choice(s1.legal_actions), rng.choice(s2.legal_actions))
        assert abs(leaf_value(b.raw.bytes, 0) + leaf_value(b.raw.bytes, 1)) < 1e-9


def test_leaf_value_prefers_healthier_side():
    t = sample_team(random.Random(3))
    b = Battle(t, t, seed=99)  # mirror match: value starts at exactly 0
    assert abs(leaf_value(b.raw.bytes, 0)) < 1e-9
    rng = random.Random(1)
    while not b.winner:
        b.step(rng.choice(b.state(1).legal_actions), rng.choice(b.state(2).legal_actions))
    winner_side = 0 if b.winner == "p1" else 1
    if b.winner != "tie":
        assert leaf_value(b.raw.bytes, winner_side) > 0


def test_teacher_action_is_legal_and_deterministic():
    for depth in (1, 2):
        b1, b2 = _battle(seed=21), _battle(seed=21)
        t1 = SearchTeacher(depth=depth, rolls=2, seed=5)
        t2 = SearchTeacher(depth=depth, rolls=2, seed=5)
        a1, a2 = t1.choose_full(b1, 1), t2.choose_full(b2, 1)
        assert a1 == a2, f"same seed, same position, different action (depth {depth})"
        assert a1 in b1._choice_map(0)


def test_run_battle_accepts_full_info_seat():
    rng = random.Random(2)
    rec = run_battle(SearchTeacher(depth=1, rolls=1, seed=1), RandomBot(4),
                     sample_team(rng), sample_team(rng), seed=17)
    assert rec.winner in ("p1", "p2", "tie", "unfinished")
    assert rec.turns and "state_p1" in rec.turns[0]


def test_teacher_crushes_random():
    rng = random.Random(31)
    wins = 0
    for _ in range(8):
        rec = run_battle(SearchTeacher(depth=1, rolls=1, seed=rng.randrange(2**31)),
                         RandomBot(rng.randrange(2**31)),
                         sample_team(rng), sample_team(rng), seed=rng.randrange(2**63))
        wins += rec.winner == "p1"
    assert wins >= 7, f"teacher only won {wins}/8 vs RandomBot"


def test_teacher_beats_maxdamage_head_to_head():
    rng = random.Random(41)
    wins = 0
    for _ in range(8):
        rec = run_battle(SearchTeacher(depth=1, rolls=2, seed=rng.randrange(2**31)),
                         MaxDamageBot(),
                         sample_team(rng), sample_team(rng), seed=rng.randrange(2**63))
        wins += rec.winner == "p1"
    assert wins >= 5, f"teacher only won {wins}/8 vs MaxDamageBot"
