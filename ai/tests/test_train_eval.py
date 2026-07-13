import random

import pytest

torch = pytest.importorskip("torch")

from models.agent import ModelAgent
from models.encoder import FieldValueEncoder
from models.tiers import TIERS
from models.tokenizer import Tokenizer
from models.train_imitation import periodic_eval, row_weights


def test_model_agent_from_model_no_checkpoint():
    tok = Tokenizer()
    model = FieldValueEncoder(TIERS["snack"], tok)
    agent = ModelAgent.from_model(model, tok, seed=3, device="cpu")
    from tests.test_model_agent import _state

    legal = [0, 1, 9]
    assert all(agent.choose(_state(legal)) in legal for _ in range(10))


def test_row_weights_elo():
    rows = [{"elo": 0, "won": True}, {"elo": 1100, "won": False}, {"elo": 1600, "won": True}]
    w = row_weights(rows, "elo")
    assert len(w) == 3
    assert w[2] > w[1]  # higher elo weighs more
    assert all(x > 0 for x in w)  # unrated rows keep a floor weight


def test_row_weights_winners():
    rows = [{"elo": 1500, "won": True}, {"elo": 1500, "won": False}]
    w = row_weights(rows, "winners")
    assert w[0] == 1.0 and w[1] == 0.0


def test_row_weights_none_uniform():
    rows = [{"elo": 0, "won": False}, {"elo": 1700, "won": True}]
    assert row_weights(rows, "none") == [1.0, 1.0]


def test_periodic_eval_returns_metrics():
    torch.manual_seed(0)
    tok = Tokenizer()
    model = FieldValueEncoder(TIERS["snack"], tok)
    import json

    from tests.test_tokenizer import FIXTURE_STATE

    hold = [
        {"state_json": json.dumps(FIXTURE_STATE), "action": 0, "battle_id": "x"}
        for _ in range(8)
    ]
    m = periodic_eval(model, tok, hold, device="cpu", battles=2, seed=1)
    assert set(m) >= {"holdout_top1", "wr_random", "wr_maxdamage"}
    assert 0.0 <= m["holdout_top1"] <= 1.0
    assert 0.0 <= m["wr_random"] <= 1.0
    assert model.training, "periodic_eval must restore train mode"
