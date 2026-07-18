"""Mid-battle engine reconstruction from public states (sim/reconstruct.py):
the foundation of serve-time search over hidden information. A battle built
from player 1's public state plus a candidate opponent team must agree with
the original on everything player 1 could see, and must be playable.

Sim states carry my side as raw hp/max_hp with move dicts; bridge states
carry hp_fraction with move-name lists. The builder accepts both (like the
tokenizer); the round trip here uses the sim shape, where my side must
reconstruct exactly.
"""

import random

from sim.agents import MaxDamageBot
from sim.battle import Battle
from sim.reconstruct import battle_from_state, sample_opponent_team
from sim.teams import sample_team


def _play(b: Battle, turns: int) -> None:
    bot1, bot2 = MaxDamageBot(), MaxDamageBot()
    for _ in range(turns):
        if b.winner:
            break
        b.step(bot1.choose(b.state(1)), bot2.choose(b.state(2)))


def _mid_battle(seed: int = 11, turns: int = 12) -> Battle:
    rng = random.Random(3)
    b = Battle(sample_team(rng), sample_team(rng), seed=seed)
    _play(b, turns)
    assert b.winner is None
    return b


def _active(side_json):
    return side_json["pokemon"][side_json["active_ix"]]


def test_round_trip_matches_public_state():
    b = _mid_battle()
    st = b.state(1).to_json()
    # perfect determinization: hand the true opponent team to the builder
    recon, amap = battle_from_state(st, opp_team=b.teams[1], seed=99)
    rst = recon.state(1).to_json()

    me0, me1 = _active(st["my_side"]), _active(rst["my_side"])
    for key in ("species", "hp", "max_hp", "status"):
        assert me1[key] == me0[key]
    assert me1["moves"] == me0["moves"]  # ids and pp, in order

    def rows(side_json):
        return sorted(
            (m["species"], m["hp"], m["status"],
             tuple((mv["id"], mv["pp"]) for mv in m["moves"]))
            for m in side_json["pokemon"])
    assert rows(rst["my_side"]) == rows(st["my_side"])

    opp0, opp1 = _active(st["opp_side"]), _active(rst["opp_side"])
    assert opp1["species"] == opp0["species"]
    assert abs(opp1["hp_fraction"] - opp0["hp_fraction"]) < 0.01
    assert opp1["status"] == opp0["status"]

    assert rst["turn"] == st["turn"]
    # translated action set is exactly the original legal set
    assert sorted(amap.values()) == st["legal_actions"]
    assert sorted(amap) == rst["legal_actions"]


def test_reconstructed_battle_is_playable():
    b = _mid_battle()
    recon, _ = battle_from_state(b.state(1).to_json(), opp_team=b.teams[1], seed=7)
    _play(recon, 40)  # must not crash; usually finishes
    assert recon.state(1) is not None


def test_sample_opponent_team_respects_revelations():
    b = _mid_battle(turns=8)
    st = b.state(1).to_json()
    rng = random.Random(5)
    team = sample_opponent_team(st, rng)
    assert len(team) == 6
    listed = st["opp_side"]["pokemon"]
    for slot, spec in zip(listed, team):
        if slot["species"] is None:
            continue  # unrevealed: anything plausible goes
        assert spec.species == slot["species"]
        assert set(slot["revealed_moves"]) <= set(spec.moves)
    # species clause: no duplicates
    assert len({s.species for s in team}) == 6
