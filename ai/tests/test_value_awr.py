"""Value head (SPECS §4.2: [VAL] readout, two-hot over 32 win-prob bins) and
AWR-filtered BC (SPECS §5.2: CE weighted by exp(A/beta), A = return - V(s),
returns on the +1/-1 scale, beta ~ 3)."""

import json

import pytest

torch = pytest.importorskip("torch")

from models.encoder import FieldValueEncoder
from models.tiers import TIERS
from models.tokenizer import Tokenizer
from models.train_imitation import awr_weights, make_batches, two_hot, value_estimate
from tests.test_tokenizer import FIXTURE_STATE


def _batch(tok, n=4):
    rows = [
        {"state_json": json.dumps(FIXTURE_STATE), "action": 0, "won": i % 2 == 0}
        for i in range(n)
    ]
    return next(make_batches(rows, tok, n, "cpu"))


def test_value_head_shapes():
    tok = Tokenizer()
    model = FieldValueEncoder(TIERS["snack"], tok, value_bins=32)
    batch, labels, _, _ = _batch(tok)
    logits = model(**batch)
    assert isinstance(logits, torch.Tensor) and logits.shape == (4, 10)
    pi, val = model(**batch, return_value=True)
    assert pi.shape == (4, 10) and val.shape == (4, 32)


def test_value_head_absent_by_default():
    tok = Tokenizer()
    model = FieldValueEncoder(TIERS["snack"], tok)
    batch, *_ = _batch(tok)
    assert model(**batch).shape == (4, 10)
    with pytest.raises(ValueError):
        model(**batch, return_value=True)
    bigger = FieldValueEncoder(TIERS["snack"], tok, value_bins=32)
    assert bigger.num_params() > model.num_params()


def test_two_hot_targets():
    t = two_hot(torch.tensor([0.0, 1.0, 0.5]), 32)
    assert t.shape == (3, 32)
    assert torch.allclose(t.sum(-1), torch.ones(3))
    assert t[0, 0] == 1.0 and t[1, -1] == 1.0  # extremes are one-hot
    centers = torch.linspace(0.0, 1.0, 32)
    assert abs((t[2] * centers).sum().item() - 0.5) < 1e-6  # expectation preserved


def test_value_estimate_inverts_two_hot():
    for p in (0.0, 0.31, 0.5, 1.0):
        t = two_hot(torch.tensor([p]), 32)
        # logits that softmax to the two-hot distribution
        v = value_estimate(torch.log(t.clamp_min(1e-9)))
        assert abs(v.item() - p) < 1e-4
    uniform = value_estimate(torch.zeros(1, 32))
    assert abs(uniform.item() - 0.5) < 1e-6


def test_awr_weights_direction_and_clip():
    import math

    won = torch.tensor([1.0, 0.0, 1.0, 0.0])
    v = torch.tensor([0.0, 1.0, 1.0, 0.0])  # win prob estimates
    w = awr_weights(won, v, beta=3.0)
    assert abs(w[0].item() - math.exp(2 / 3)) < 1e-5  # upset win: upweighted
    assert abs(w[1].item() - math.exp(-2 / 3)) < 1e-5  # blown lead: downweighted
    assert abs(w[2].item() - 1.0) < 1e-5  # expected outcomes: neutral
    assert abs(w[3].item() - 1.0) < 1e-5
    assert awr_weights(won, v, beta=0.05).max().item() <= 20.0  # clipped


def test_make_batches_yields_won_labels():
    tok = Tokenizer()
    batch, labels, w, wons = _batch(tok)
    assert wons.dtype == torch.float32 and wons.shape == labels.shape
    assert wons.tolist() == [1.0, 0.0, 1.0, 0.0]


def test_joint_loss_trains_value_head():
    import torch.nn.functional as F

    torch.manual_seed(0)
    tok = Tokenizer()
    model = FieldValueEncoder(TIERS["snack"], tok, value_bins=32)
    batch, labels, _, wons = _batch(tok)
    pi, val = model(**batch, return_value=True)
    loss = F.cross_entropy(pi, labels) + F.cross_entropy(val, two_hot(wons, 32))
    loss.backward()
    vgrads = [p.grad for p in model.value.parameters()]
    assert all(g is not None and g.abs().sum() > 0 for g in vgrads)


def test_model_agent_loads_value_checkpoint(tmp_path):
    from models.agent import ModelAgent
    from tests.test_model_agent import _state

    tok = Tokenizer()
    model = FieldValueEncoder(TIERS["snack"], tok, value_bins=32)
    path = tmp_path / "model.pt"
    torch.save(
        {"model": model.state_dict(), "tier": "snack", "steps": 0,
         "hist_k": 0, "seq_len": tok.seq_len, "value_bins": 32},
        path,
    )
    agent = ModelAgent(path, seed=5, device="cpu")
    legal = [0, 1, 9]
    assert all(agent.choose(_state(legal)) in legal for _ in range(10))
