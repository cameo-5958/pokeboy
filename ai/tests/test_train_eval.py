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


def test_tail_augmentation_varies_history_length():
    import json as _json
    import random as _random

    from models.train_imitation import make_batches
    from tests.test_tokenizer import FIXTURE_STATE

    tok = Tokenizer(hist_k=20)
    s = _json.loads(_json.dumps(FIXTURE_STATE))
    s["history_tail"] = [
        {"o": -(i + 1), "my": "M:Body Slam", "op": None, "dm": 0, "do": 0, "ev": []}
        for i in range(20)
    ]
    rows = [{"state_json": _json.dumps(s), "action": 0} for _ in range(32)]
    batch, _, _, _ = next(make_batches(rows, tok, 32, "cpu"))
    assert len(set(batch["lengths"].tolist())) == 1  # no augment → constant

    batch, _, _, _ = next(make_batches(rows, tok, 32, "cpu", augment_rng=_random.Random(0)))
    lengths = batch["lengths"].tolist()
    assert len(set(lengths)) > 3, f"tail augment did not vary lengths: {lengths}"


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
