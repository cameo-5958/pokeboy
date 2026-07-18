"""Phase 3 PPO league self-play (SPECS §5.3): GAE over episodic win/loss
returns on the win-prob scale, legality-masked PPO updates, batched rollout
collection with league opponents."""

import random

import pytest

torch = pytest.importorskip("torch")

from models.encoder import FieldValueEncoder
from models.ppo import (
    LEAGUE_SCRIPTED,
    collect_rollouts,
    gae,
    make_league,
    masked_dist,
    ppo_update,
)
from models.tiers import TIERS
from models.tokenizer import Tokenizer


def _model(tok):
    torch.manual_seed(0)
    return FieldValueEncoder(TIERS["snack"], tok, value_bins=32)


def test_gae_terminal_only_reward():
    # 3-step episode, win (return 1.0), gamma=1, lam=1: advantages are the
    # suffix sums of TD deltas and returns equal the final outcome everywhere
    values = [0.5, 0.6, 0.8]
    adv, ret = gae(values, final_reward=1.0, gamma=1.0, lam=1.0)
    assert [round(a, 6) for a in adv] == [0.5, 0.4, 0.2]
    assert [round(r, 6) for r in ret] == [1.0, 1.0, 1.0]


def test_gae_loss_and_lambda_discounting():
    values = [0.7, 0.7]
    adv, ret = gae(values, final_reward=0.0, gamma=1.0, lam=0.5)
    # deltas: [0.7-0.7, 0.0-0.7] = [0, -0.7]; adv = [0 + 0.5*-0.7, -0.7]
    assert round(adv[0], 6) == -0.35 and round(adv[1], 6) == -0.7
    assert round(ret[1], 6) == 0.0


def test_masked_dist_zeroes_illegal():
    logits = torch.zeros(2, 10)
    legal = torch.zeros(2, 10, dtype=torch.bool)
    legal[0, [0, 1]] = True
    legal[1, [4, 9]] = True
    dist = masked_dist(logits, legal)
    probs = dist.probs
    assert torch.allclose(probs[0, [0, 1]], torch.tensor([0.5, 0.5]))
    assert probs[0, 2:].sum() == 0
    assert probs[1, [4, 9]].sum() == pytest.approx(1.0)
    # entropy only over legal actions
    assert dist.entropy()[0] == pytest.approx(torch.log(torch.tensor(2.0)).item(), rel=1e-5)


def test_make_league_deterministic_and_mixed():
    tok = Tokenizer()
    model = _model(tok)
    league = make_league(model, tok, frozen=[], rng=random.Random(5))
    kinds = [league.pick(random.Random(i)).kind for i in range(40)]
    assert kinds.count("mirror") > 10  # mirror games dominate
    assert any(k in LEAGUE_SCRIPTED for k in kinds)


def test_collect_rollouts_smoke_and_update():
    tok = Tokenizer()
    model = _model(tok)
    league = make_league(model, tok, frozen=[], rng=random.Random(3))
    buf = collect_rollouts(model, tok, league, n_battles=3, device="cpu",
                           rng=random.Random(7), max_turns=100)
    assert len(buf["action"]) > 10
    assert set(buf["reward_return"].tolist()) <= {0.0, 0.5, 1.0}
    assert buf["legal"].dtype == torch.bool
    assert buf["old_logp"].isfinite().all()
    # every stored action was legal
    assert buf["legal"].gather(1, buf["action"].unsqueeze(1)).all()

    before = [p.clone() for p in model.parameters()]
    opt = torch.optim.AdamW(model.parameters(), lr=1e-4)
    stats = ppo_update(model, opt, buf, device="cpu", epochs=1, minibatch=32)
    assert all(torch.isfinite(torch.tensor(v)) for v in stats.values())
    assert any((p != q).any() for p, q in zip(before, model.parameters()))


def test_collect_rollouts_concurrent_matches_contract():
    tok = Tokenizer()
    model = _model(tok)
    league = make_league(model, tok, frozen=[], rng=random.Random(3))
    buf = collect_rollouts(model, tok, league, n_battles=5, device="cpu",
                           rng=random.Random(11), max_turns=100, concurrent=3)
    n = len(buf["action"])
    assert n > 10
    assert set(buf["reward_return"].tolist()) <= {0.0, 0.5, 1.0}
    assert buf["legal"].gather(1, buf["action"].unsqueeze(1)).all()
    assert buf["old_logp"].isfinite().all()
    assert buf["advantage"].isfinite().all()
    assert buf["field_ids"].shape[0] == n


def test_update_snapshot_keeps_single_reused_copy():
    from models.ppo import update_snapshot

    tok = Tokenizer()
    model = _model(tok)
    frozen: list = []
    update_snapshot(frozen, model, tok, "snack", 32, "cpu", seed=1)
    first_obj = frozen[0].model
    with torch.no_grad():
        for p in model.parameters():
            p.add_(1.0)
    update_snapshot(frozen, model, tok, "snack", 32, "cpu", seed=2)
    assert len(frozen) == 1
    assert frozen[0].model is first_obj  # reused, not reallocated
    assert all(torch.equal(a, b) for a, b in
               zip(frozen[0].model.parameters(), model.parameters()))
