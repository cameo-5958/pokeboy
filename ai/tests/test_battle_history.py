"""Live-sim history_tail: same entry shape as the converters, built from
engine-observable diffs only (opp chosen-but-unexecuted moves stay hidden)."""

import random

from sim.battle import Battle
from sim.pack import PokemonSpec
from sim.teams import sample_team

T1 = [
    PokemonSpec("Tauros", ["Body Slam", "Hyper Beam", "Blizzard", "Earthquake"]),
    PokemonSpec("Snorlax", ["Body Slam", "Reflect", "Rest", "Ice Beam"]),
]
T2 = [
    PokemonSpec("Chansey", ["Ice Beam", "Thunderbolt", "Soft-Boiled", "Thunder Wave"]),
    PokemonSpec("Starmie", ["Surf", "Blizzard", "Thunder Wave", "Recover"]),
]


def first_legal(state, prefer):
    return prefer if prefer in state.legal_actions else state.legal_actions[0]


def test_turn_one_has_empty_tail():
    b = Battle(T1, T2, seed=3)
    assert b.state(1).history_tail == []
    assert b.state(2).history_tail == []


def test_moves_recorded_from_both_perspectives():
    b = Battle(T1, T2, seed=3)
    a1 = first_legal(b.state(1), 0)  # Body Slam
    a2 = first_legal(b.state(2), 1)  # Thunderbolt
    b.step(a1, a2)
    s1, s2 = b.state(1), b.state(2)
    e1, e2 = s1.history_tail[0], s2.history_tail[0]
    assert e1["o"] == e2["o"] == -1
    assert e1["my"] == "M:Body Slam"
    assert e2["op"] == "M:Body Slam"
    assert e1["op"] == e2["my"] == "M:Thunderbolt"
    assert set(e1) == {"o", "my", "op", "dm", "do", "ev"}
    # damage flows both ways in this exchange
    assert (e1["do"] or 0) >= 0 and (e1["dm"] or 0) >= 0
    assert e1["dm"] == e2["do"] and e1["do"] == e2["dm"]


def test_opponent_switch_recorded_as_species():
    b = Battle(T1, T2, seed=5)
    a2 = 4 if 4 in b.state(2).legal_actions else None
    if a2 is None:
        return
    b.step(first_legal(b.state(1), 0), a2)
    tail = b.state(1).history_tail
    assert tail[0]["op"] == "S:Starmie"


def test_tail_is_bounded_and_newest_first():
    rng = random.Random(0)
    b = Battle(sample_team(rng), sample_team(rng), seed=11)
    for _ in range(60):
        if b.winner:
            break
        s1, s2 = b.state(1), b.state(2)
        b.step(rng.choice(s1.legal_actions), rng.choice(s2.legal_actions))
    tail = b.state(1).history_tail
    assert len(tail) <= 20
    assert [e["o"] for e in tail] == [-(i + 1) for i in range(len(tail))]


def test_hyper_beam_recharge_not_reported_as_fresh_move():
    """Recharge turns are forced move-slot-1 with pp+hp unchanged (engine
    quirk): nothing observable happened, so the opp action must be None."""
    rng = random.Random(1)
    found = 0
    for seed in range(40):
        b = Battle(T1, T2, seed=seed)
        # p1 spams Hyper Beam (slot 1) whenever legal
        for _ in range(30):
            if b.winner:
                break
            s1, s2 = b.state(1), b.state(2)
            active_ix = s1.my_side["active_ix"]
            pp_before = {m["id"]: m["pp"] for m in s1.my_side["pokemon"][active_ix]["moves"]}
            a1 = first_legal(s1, 1)
            b.step(a1, rng.choice(s2.legal_actions))
            s1b = b.state(1)
            # only judge steps that completed exactly one turn with the same
            # active mon — otherwise tail[0] describes a different turn/mon
            if s1b.turn != s1.turn + 1 or s1b.my_side["active_ix"] != active_ix:
                continue
            act = s1b.my_side["pokemon"][active_ix]["moves"]
            pp_after = {m["id"]: m["pp"] for m in act}
            executed = any(pp_after.get(k, 0) < v for k, v in pp_before.items())
            opp_view = b.state(2).history_tail[0]["op"]
            if a1 <= 3 and not executed:
                assert opp_view is None, f"unexecuted move leaked as {opp_view}"
                found += 1
        if found:
            break
    assert found, "never observed a recharge/blocked turn; test exercised nothing"
