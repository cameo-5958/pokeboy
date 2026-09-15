import json
from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from models.encoder import FieldValueEncoder
from models.tiers import TIERS
from models.tokenizer import Tokenizer
from tests.test_tokenizer import FIXTURE_STATE


def _batch(tok, states):
    encs = [tok.encode(s) for s in states]
    return dict(
        field_ids=torch.tensor(np.stack([e["field_ids"] for e in encs]), dtype=torch.long),
        value_ids=torch.tensor(np.stack([e["value_ids"] for e in encs]), dtype=torch.long),
        slot_ids=torch.tensor(np.stack([e["slot_ids"] for e in encs]), dtype=torch.long),
        cont=torch.tensor(np.stack([e["cont"] for e in encs]), dtype=torch.float32),
        lengths=torch.tensor([e["length"] for e in encs], dtype=torch.long),
    )


def test_forward_shape():
    tok = Tokenizer()
    model = FieldValueEncoder(TIERS["snack"], tok)
    logits = model(**_batch(tok, [FIXTURE_STATE] * 3))
    assert logits.shape == (3, 10)
    assert torch.isfinite(logits).all()


def test_snack_param_budget():
    n = FieldValueEncoder(TIERS["snack"]).num_params()
    assert 2_000_000 <= n <= 6_000_000, n


def test_overfit_tiny_batch():
    torch.manual_seed(0)
    tok = Tokenizer()
    model = FieldValueEncoder(TIERS["snack"], tok)
    states = []
    for hp in (0.1, 0.3, 0.5, 0.7, 0.9, 1.0):
        s = json.loads(json.dumps(FIXTURE_STATE))
        s["my_side"]["pokemon"][0]["hp_fraction"] = hp
        states.append(s)
    labels = torch.tensor([0, 1, 2, 3, 4, 5])
    batch = _batch(tok, states)
    opt = torch.optim.AdamW(model.parameters(), lr=3e-4)
    for _ in range(150):
        opt.zero_grad()
        loss = torch.nn.functional.cross_entropy(model(**batch), labels)
        loss.backward()
        opt.step()
    acc = (model(**batch).argmax(-1) == labels).float().mean().item()
    assert acc == 1.0, f"failed to overfit 6 states (acc {acc})"
