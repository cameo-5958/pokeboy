"""Distillation ladder: teacher-label sidecars, KD trainer mode, micro tiers.

Sidecar contract: for every labeled part, models/distill writes a parquet
with IDENTICAL row order (battle_id echoed for verification), kd_probs =
legality-masked temp-1 teacher policy (10 float32), kd_v = value-head win
prob (or -1.0 when the teacher has no value head). The trainer discovers
labeled parts FROM the sidecar tree, so partial labeling just narrows the
corpus instead of erroring.
"""

import json

import numpy as np
import pytest

torch = pytest.importorskip("torch")
pq = pytest.importorskip("pyarrow.parquet")

from models.encoder import FieldValueEncoder
from models.tiers import TIERS
from models.tokenizer import Tokenizer
from sim.generate import generate_corpus


def _tiny_corpus(root, battles=2, seed=11):
    src = root / "datasets" / "processed" / "tinysrc"
    generate_corpus(src, battles=battles, seed=seed, workers=1, depth=1,
                    rolls=1, part_rows=40)
    return src


def _label(root, value_bins=32, seed=0):
    from models.distill import label_corpus

    torch.manual_seed(seed)
    tok = Tokenizer()
    model = FieldValueEncoder(TIERS["snack"], tok, value_bins=value_bins)
    model.eval()
    out = root / "datasets" / "distill" / "t0"
    stats = label_corpus(model, tok, sources=["tinysrc"], out=out,
                         data_root=root / "datasets" / "processed",
                         batch_size=16, device="cpu")
    return out, stats


def test_micro_tiers_are_small():
    tok = Tokenizer()
    sizes = {}
    for name in ("crumb", "bite", "snack"):
        m = FieldValueEncoder(TIERS[name], tok, value_bins=32)
        sizes[name] = sum(p.numel() for p in m.parameters())
    assert sizes["crumb"] < 600_000, sizes
    assert sizes["bite"] < 1_500_000, sizes
    assert sizes["crumb"] < sizes["bite"] < sizes["snack"]


def test_micro_tier_forward_runs():
    from tests.test_tokenizer import FIXTURE_STATE

    tok = Tokenizer()
    m = FieldValueEncoder(TIERS["crumb"], tok, value_bins=32)
    enc = tok.encode(FIXTURE_STATE)
    batch = dict(
        field_ids=torch.tensor(np.asarray(enc["field_ids"])[None], dtype=torch.long),
        value_ids=torch.tensor(np.asarray(enc["value_ids"])[None], dtype=torch.long),
        slot_ids=torch.tensor(np.asarray(enc["slot_ids"])[None], dtype=torch.long),
        cont=torch.tensor(np.asarray(enc["cont"])[None], dtype=torch.float32),
        lengths=torch.tensor([enc["length"]], dtype=torch.long),
    )
    logits, value = m(**batch, return_value=True)
    assert logits.shape == (1, 10) and value.shape == (1, 32)


def test_label_sidecars_align_and_mask(tmp_path):
    src = _tiny_corpus(tmp_path)
    out, stats = _label(tmp_path)
    data_parts = sorted(src.glob("part-*.parquet"))
    side_parts = sorted((out / "tinysrc").glob("part-*.parquet"))
    assert [p.name for p in side_parts] == [p.name for p in data_parts]
    assert stats["rows"] > 0 and stats["parts"] == len(side_parts)
    for dp, sp in zip(data_parts, side_parts):
        drows = pq.read_table(dp).to_pylist()
        srows = pq.read_table(sp).to_pylist()
        assert len(drows) == len(srows)
        for dr, sr in zip(drows, srows):
            assert sr["battle_id"] == dr["battle_id"]
            probs = np.asarray(sr["kd_probs"], dtype=np.float32)
            assert probs.shape == (10,)
            assert abs(float(probs.sum()) - 1.0) < 1e-4
            legal = set(json.loads(dr["state_json"])["legal_actions"])
            for a in range(10):
                if a not in legal:
                    assert probs[a] == 0.0, (a, legal, probs)
            assert 0.0 <= sr["kd_v"] <= 1.0


def test_label_no_value_head_marks_v_negative(tmp_path):
    _tiny_corpus(tmp_path)
    out, _ = _label(tmp_path, value_bins=0)
    srows = [r for p in sorted((out / "tinysrc").glob("part-*.parquet"))
             for r in pq.read_table(p).to_pylist()]
    assert srows and all(r["kd_v"] == -1.0 for r in srows)


def test_label_skips_existing_sidecars(tmp_path):
    _tiny_corpus(tmp_path)
    out, s1 = _label(tmp_path)
    _, s2 = _label(tmp_path)
    assert s2["parts"] == 0 and s2["skipped"] == s1["parts"]


def test_load_rows_attaches_kd_and_drives_from_sidecars(tmp_path, monkeypatch):
    import models.train_imitation as ti

    src = _tiny_corpus(tmp_path)
    out, _ = _label(tmp_path)
    monkeypatch.setattr(ti, "ROOT", tmp_path)
    rows = ti.load_rows(10_000, ["tinysrc"], seed=0, distill_root=out)
    assert rows and all("kd_probs" in r and "kd_v" in r for r in rows)
    side_parts = sorted((out / "tinysrc").glob("part-*.parquet"))
    if len(side_parts) > 1:  # partial labeling narrows, never errors
        side_parts[0].unlink()
        fewer = ti.load_rows(10_000, ["tinysrc"], seed=0, distill_root=out)
        assert 0 < len(fewer) < len(rows)
    for p in src.glob("part-*.parquet"):  # orphan sidecar = hard error
        p.unlink()
    with pytest.raises(FileNotFoundError):
        ti.load_rows(10_000, ["tinysrc"], seed=0, distill_root=out)


def test_make_batches_distill_yields_kd_tensors():
    from models.train_imitation import make_batches
    from tests.test_tokenizer import FIXTURE_STATE

    tok = Tokenizer()
    probs = [0.0] * 10
    probs[0], probs[1] = 0.7, 0.3
    rows = [
        {"state_json": json.dumps(FIXTURE_STATE), "action": 0, "won": True,
         "kd_probs": list(probs), "kd_v": 0.6}
        for _ in range(8)
    ]
    batch, labels, w, won, kd = next(make_batches(rows, tok, 8, "cpu", distill=True))
    assert kd["probs"].shape == (8, 10) and kd["v"].shape == (8,)
    assert torch.allclose(kd["probs"].sum(-1), torch.ones(8))
    assert float(kd["v"][0]) == pytest.approx(0.6)


def test_kd_loss_onehot_matches_hard_ce():
    from models.train_imitation import kd_policy_loss

    torch.manual_seed(0)
    logits = torch.randn(6, 10)
    labels = torch.randint(0, 10, (6,))
    onehot = torch.zeros(6, 10)
    onehot[torch.arange(6), labels] = 1.0
    kd = kd_policy_loss(logits, onehot, temp=1.0)
    ce = torch.nn.functional.cross_entropy(logits, labels)
    assert torch.allclose(kd, ce, atol=1e-6)


def test_kd_loss_temp_retempers():
    from models.train_imitation import kd_policy_loss

    torch.manual_seed(1)
    logits = torch.randn(4, 10)
    probs = torch.softmax(torch.randn(4, 10), -1)
    l1 = kd_policy_loss(logits, probs, temp=1.0)
    l4 = kd_policy_loss(logits, probs, temp=4.0)
    assert torch.isfinite(l1) and torch.isfinite(l4)
    assert not torch.allclose(l1, l4)
