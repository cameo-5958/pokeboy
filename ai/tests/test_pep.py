"""CPU tests for the PEP on-device policy, its data decoder and trainer."""
from __future__ import annotations

import numpy as np
import pytest

torch = pytest.importorskip("torch")
pa = pytest.importorskip("pyarrow")
import pyarrow.parquet as pq  # noqa: E402

from models import pep_data  # noqa: E402
from models.pep import (  # noqa: E402
    N_ACTIONS,
    PEP,
    PEPConfig,
    features_to_tensors,
    load_checkpoint,
    save_checkpoint,
)
from models.pep_data import (  # noqa: E402
    FEATURES_BYTES,
    MAX_TOKENS,
    TOKEN_TYPES,
    FeaturesDataset,
    collate_battles,
    decode_features,
    encode_features,
)
from models.train_pep import build_parser, iter_windows, train  # noqa: E402

torch.set_num_threads(2)


# --------------------------------------------------------------------------- synthetic features


def random_decision(rng: np.random.Generator, n_switch: int = 2) -> dict:
    """One plausible Features record: 4 own moves, 1+n_switch own mons, 1-3 player mons, no items."""
    present = np.zeros(MAX_TOKENS, np.uint8)
    candidate = np.zeros(MAX_TOKENS, np.uint8)
    cat = np.zeros((MAX_TOKENS, 4), np.uint16)
    f = rng.integers(-127, 128, size=(MAX_TOKENS, 48)).astype(np.int8)

    present[0] = 1
    cat[0] = [rng.integers(0, 48), rng.integers(1, 4), 0, 0]
    own = 1 + n_switch
    for i in range(own):
        present[1 + i] = 1
        sp = rng.integers(0, 191)
        cat[1 + i] = [sp, rng.integers(1, 28), rng.integers(0, 28), sp * 191 + rng.integers(0, 191)]
        if i > 0:
            candidate[1 + i] = 5 + (i - 1)
    for i in range(rng.integers(1, 4)):
        present[7 + i] = 1
        sp = rng.integers(0, 191)
        cat[7 + i] = [sp, rng.integers(1, 28), rng.integers(0, 28), sp * 191 + rng.integers(0, 191)]
    for i in range(4):
        present[13 + i] = 1
        cat[13 + i] = [rng.integers(0, 166), rng.integers(0, 90), rng.integers(1, 28), 0]
        candidate[13 + i] = 1 + i
    for i in range(rng.integers(0, 4)):
        present[17 + i] = 1
        cat[17 + i] = [rng.integers(0, 166), rng.integers(0, 90), rng.integers(1, 28), 0]
    legal = 0
    for a in range(4):
        if rng.random() < 0.85 or a == 0:
            legal |= 1 << a
    for a in range(n_switch):
        if rng.random() < 0.6:
            legal |= 1 << (4 + a)
    return dict(present=present, candidate=candidate, cat=cat, f=f, legal=legal, request_kind=int(cat[0, 1] - 1))


def teacher_rule(d: dict) -> np.ndarray:
    """Teacher: prefers legal moves by f[0] of their token (learnable from the pointer head)."""
    logits = np.full(N_ACTIONS, -np.inf, np.float32)
    for a in range(4):
        if d["legal"] >> a & 1:
            logits[a] = 4.0 * d["f"][13 + a, 0] / 127.0
    for a in range(4, 10):
        if d["legal"] >> a & 1:
            logits[a] = -1.5
    p = np.exp(logits - logits[np.isfinite(logits)].max())
    return (p / p.sum()).astype(np.float32)


def write_synthetic_parquet(path, n_battles=20, n_decisions=10, seed=0) -> None:
    rng = np.random.default_rng(seed)
    rows = {k: [] for k in ("battle_id", "round", "seq", "features", "legal", "request_kind", "event",
                            "teacher_probs", "teacher_value", "action", "won")}
    for b in range(n_battles):
        won = bool(rng.random() < 0.5)
        order = rng.permutation(n_decisions)  # rows deliberately shuffled on disk
        for t in order:
            d = random_decision(rng)
            tp = teacher_rule(d)
            rows["battle_id"].append(f"battle-{b:03d}")
            rows["round"].append(int(t))
            rows["seq"].append(int(t))
            rows["features"].append(encode_features(**d))
            rows["legal"].append(d["legal"])
            rows["request_kind"].append(d["request_kind"])
            rows["event"].append(np.zeros(64, np.float32).tobytes())
            rows["teacher_probs"].append(None if (b == 0 and t < 3) else tp.tolist())
            rows["teacher_value"].append(-1.0)
            rows["action"].append(int(tp.argmax()))
            rows["won"].append(won)
    table = pa.table(
        {
            "battle_id": pa.array(rows["battle_id"], pa.string()),
            "round": pa.array(rows["round"], pa.int32()),
            "seq": pa.array(rows["seq"], pa.int32()),
            "features": pa.array(rows["features"], pa.binary()),
            "legal": pa.array(rows["legal"], pa.uint16()),
            "request_kind": pa.array(rows["request_kind"], pa.uint8()),
            "event": pa.array(rows["event"], pa.binary()),
            "teacher_probs": pa.array(rows["teacher_probs"], pa.list_(pa.float32())),
            "teacher_value": pa.array(rows["teacher_value"], pa.float32()),
            "action": pa.array(rows["action"], pa.int8()),
            "won": pa.array(rows["won"], pa.bool_()),
        }
    )
    pq.write_table(table, path)


def make_batch(n: int, seed: int = 0) -> tuple[dict, dict]:
    rng = np.random.default_rng(seed)
    ds = [random_decision(rng) for _ in range(n)]
    arrays = pep_data.decode_features_batch([encode_features(**d) for d in ds])
    return features_to_tensors(arrays), {"raw": ds, "arrays": arrays}


# --------------------------------------------------------------------------- data


def test_decode_roundtrip():
    rng = np.random.default_rng(1)
    d = random_decision(rng)
    blob = encode_features(**d)
    assert len(blob) == FEATURES_BYTES == 1626
    out = decode_features(blob)
    assert out["present"].tolist() == d["present"].tolist()
    assert out["candidate"].tolist() == d["candidate"].tolist()
    np.testing.assert_array_equal(out["cat"], d["cat"])
    np.testing.assert_array_equal(out["f"], d["f"])
    assert int(out["legal"]) == d["legal"]
    assert int(out["request_kind"]) == d["request_kind"]
    assert out["type"].tolist() == TOKEN_TYPES.tolist()


def test_dataset_groups_and_orders(tmp_path):
    p = tmp_path / "rows.parquet"
    write_synthetic_parquet(p, n_battles=5, n_decisions=7)
    ds = FeaturesDataset(p)
    assert len(ds) == 5 and ds.num_decisions == 35
    for b in ds:
        assert len(b) == 7
        assert b.round.tolist() == list(range(7))  # sorted by seq despite shuffled rows
        assert b.event.shape == (7, 64) and b.teacher_probs.shape == (7, 16)
    assert np.isnan(ds[0].teacher_probs[:3]).all() and np.isfinite(ds[0].teacher_probs[3:]).all()
    batch = collate_battles(ds.battles[:3])
    assert batch["mask"].shape == (3, 7) and batch["mask"].all()


# --------------------------------------------------------------------------- model


def test_shapes_and_masks():
    torch.manual_seed(0)
    model = PEP(PEPConfig(d=64, layers=2, heads=4, ffn=128, gru=32))
    feats, meta = make_batch(6)
    logits, value, h = model(feats)
    assert logits.shape == (6, N_ACTIONS) and value.shape == (6,) and h.shape == (6, 32)
    legal = PEP.legal_mask(feats["legal"])
    cand_actions = torch.zeros(6, N_ACTIONS, dtype=torch.bool)
    for i, d in enumerate(meta["raw"]):
        for tok in range(MAX_TOKENS):
            if d["present"][tok] and d["candidate"][tok]:
                cand_actions[i, d["candidate"][tok] - 1] = True
    allowed = legal & cand_actions
    assert torch.isinf(logits[~allowed]).all() and (logits[~allowed] < 0).all()
    assert torch.isfinite(logits[allowed]).all()
    probs = torch.softmax(logits, dim=-1)
    assert torch.allclose(probs.sum(-1), torch.ones(6), atol=1e-6)
    assert (probs[~allowed] == 0).all()
    # sequence API agrees with stepping the single-step API
    seq = {k: v.view(2, 3, *v.shape[1:]) for k, v in feats.items()}
    ls, vs, hs = model.forward_seq(seq, None, None, torch.ones(2, 3, dtype=torch.bool))
    h = None
    for t in range(3):
        step = {k: v[:, t] for k, v in seq.items()}
        lt, vt, h = model(step, None, h)
        assert torch.allclose(lt, ls[:, t], atol=1e-5, equal_nan=True)
        assert torch.allclose(vt, vs[:, t], atol=1e-5)
        assert torch.allclose(h, hs[:, t], atol=1e-5)
    # absent tokens do not influence the output
    feats2 = {k: v.clone() for k, v in feats.items()}
    absent = feats2["present"] == 0
    feats2["f"][absent] = 77
    feats2["cat"][absent] = 5
    l2, v2, _ = model(feats2)
    assert torch.allclose(l2, logits, atol=1e-5, equal_nan=True) and torch.allclose(v2, value, atol=1e-5)


def test_pointer_permutation_equivariance():
    torch.manual_seed(0)
    model = PEP(PEPConfig(d=64, layers=2, ffn=128, gru=32))
    with torch.no_grad():  # make ReZero paths live so attention matters
        for layer in model.layers:
            layer.alpha_attn.fill_(0.5)
            layer.alpha_ffn.fill_(0.5)
    feats, _ = make_batch(5, seed=3)
    feats["legal"] = torch.full_like(feats["legal"], 0b1011)  # moves 0,1,3 legal, 2 illegal
    base, vbase, _ = model(feats)
    perm = torch.tensor([2, 0, 3, 1])  # new slot i holds old slot perm[i]

    # (a) relabel candidate ids of the 4 own-move tokens + permute legal bits -> logits permute
    relabel = {k: v.clone() for k, v in feats.items()}
    inv = torch.argsort(perm)
    # token in slot j (old action j) now advertises action inv[j]
    relabel["candidate"][:, 13:17] = inv[None, :] + 1
    legal_bits = PEP.legal_mask(feats["legal"])
    new_bits = legal_bits.clone()
    new_bits[:, :4] = legal_bits[:, :4][:, perm]
    relabel["legal"] = (new_bits.long() * (1 << torch.arange(N_ACTIONS))).sum(-1)
    out, vout, _ = model(relabel)
    assert torch.allclose(out[:, :4], base[:, :4][:, perm], atol=1e-5, equal_nan=True)
    assert torch.allclose(out[:, 4:], base[:, 4:], atol=1e-5, equal_nan=True)
    assert torch.allclose(vout, vbase, atol=1e-5)

    # (b) physically moving the tokens (content + candidate ids) leaves the logits unchanged
    moved = {k: v.clone() for k, v in feats.items()}
    for k in ("present", "candidate", "cat", "f"):
        moved[k][:, 13:17] = feats[k][:, 13:17][:, perm]
    out2, _, _ = model(moved)
    assert torch.allclose(out2, base, atol=1e-5, equal_nan=True)


def test_param_budget():
    cfg = PEPConfig()
    m = PEP(cfg)
    n = m.num_params(include_matchup=False)
    assert 700_000 <= n <= 1_400_000, n
    assert m.num_params(True) - m.num_params(False) == 36481 * cfg.emb_matchup


# --------------------------------------------------------------------------- trainer


def test_iter_windows():
    wins = list(iter_windows(50, 32, 8))
    assert wins == [(0, 32, 0), (24, 50, 32)]
    assert list(iter_windows(10, 32, 8)) == [(0, 10, 0)]
    covered = sorted(t for s, e, ls in iter_windows(100, 32, 8) for t in range(ls, e))
    assert covered == list(range(100))


def test_train_synthetic_and_checkpoint(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    write_synthetic_parquet(data / "part-0.parquet", n_battles=20, n_decisions=10)
    args = build_parser().parse_args(
        [
            "--data", str(data), "--run", "t", "--ckpt-dir", str(tmp_path / "ckpt"),
            "--d", "64", "--layers", "2", "--ffn", "128", "--gru", "32", "--heads", "4",
            "--steps", "30", "--batch", "8", "--window", "6", "--burnin", "2",
            "--lr", "3e-3", "--warmup", "3", "--holdout-frac", "0.1",
            "--eval-every", "15", "--save-every", "0", "--log-every", "10", "--device", "cpu",
        ]
    )
    res = train(args)
    hist = res["history"]
    assert res["steps"] == 30 and len(hist) == 30
    first = np.mean([h["loss"] for h in hist[:5]])
    last = np.mean([h["loss"] for h in hist[-5:]])
    assert last < first, (first, last)
    assert all(np.isfinite(h["loss"]) for h in hist)
    assert len(res["evals"]) == 3 and all(np.isfinite(e["kl"]) for e in res["evals"])
    assert all("grad_norm" in h for h in hist)

    ckpt = tmp_path / "ckpt" / "t" / "model.pt"
    assert ckpt.exists()
    blob = torch.load(ckpt, map_location="cpu", weights_only=False)
    assert set(blob) >= {"model", "config", "steps", "feature_schema"}
    assert blob["feature_schema"] == "pkai-features-v1" and blob["steps"] == 30
    assert blob["config"] == {"d": 64, "layers": 2, "heads": 4, "ffn": 128, "gru": 32,
                              "emb_species": 32, "emb_move": 32, "emb_matchup": 8, "emb_small": 8}
    loaded, _ = load_checkpoint(str(ckpt))
    feats, _ = make_batch(4, seed=9)
    a = res["model"].eval()(feats)
    b = loaded.eval()(feats)
    assert torch.allclose(a[0], b[0], equal_nan=True) and torch.allclose(a[1], b[1])

    save_checkpoint(loaded, str(tmp_path / "ckpt" / "copy.pt"), 31)
    again, blob2 = load_checkpoint(str(tmp_path / "ckpt" / "copy.pt"))
    assert blob2["steps"] == 31
    for (k1, p1), (k2, p2) in zip(loaded.state_dict().items(), again.state_dict().items()):
        assert k1 == k2 and torch.equal(p1, p2)
