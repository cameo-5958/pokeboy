"""Serve-time search overdrive: policy-shortlisted expectation search over
determinized reconstructions (serve/overdrive.py + SearchTeacher.action_scores)."""

import random
from pathlib import Path

import pytest

from sim.agents import MaxDamageBot
from sim.battle import Battle
from sim.search import SearchTeacher
from sim.teams import sample_team

CKPT = Path(__file__).resolve().parents[1] / "checkpoints/banquet/latest/model.pt"


def _mid_battle(turns: int = 8) -> Battle:
    rng = random.Random(9)
    b = Battle(sample_team(rng), sample_team(rng), seed=21)
    bot1, bot2 = MaxDamageBot(), MaxDamageBot()
    for _ in range(turns):
        if b.winner:
            break
        b.step(bot1.choose(b.state(1)), bot2.choose(b.state(2)))
    while len(b.state(1).legal_actions) < 3 and b.winner is None:
        b.step(bot1.choose(b.state(1)), bot2.choose(b.state(2)))
    assert b.winner is None
    return b


def test_action_scores_matches_choose_full_and_restricts():
    b = _mid_battle()
    scores = SearchTeacher(seed=4).action_scores(b, 1)
    assert len(scores) > 1
    # same seed, fresh RNG stream: choose_full is argmax of action_scores
    assert SearchTeacher(seed=4).choose_full(b, 1) == \
        min(scores, key=lambda a: (-scores[a], a))
    sub = set(sorted(scores)[:2])
    assert set(SearchTeacher(seed=4).action_scores(b, 1, actions=sub)) == sub


@pytest.mark.skipif(not CKPT.exists(), reason="needs banquet checkpoint")
def test_overdrive_agent_legal_and_deterministic():
    import torch

    from serve.overdrive import OverdriveAgent

    # multi-threaded CPU reductions reorder float sums and can flip
    # near-tied policy probs; single-thread makes the pick reproducible
    torch.set_num_threads(1)
    agent = OverdriveAgent(str(CKPT), determinizations=2, depth=1, rolls=1,
                           seed=3, device="cpu")
    b = _mid_battle()
    st = b.state(1)
    a1 = agent.choose(st)
    assert a1 in st.legal_actions
    agent2 = OverdriveAgent(str(CKPT), determinizations=2, depth=1, rolls=1,
                            seed=3, device="cpu")
    assert agent2.choose(st) == a1  # same seed, same pick
