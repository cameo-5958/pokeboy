"""Integer PEP reference (models/pep_int), PTQ + fake-quant (models/pep_quant), pkai.weights (models/pep_weights).

CPU only; a trained checkpoint is reused when checkpoints/pep/stone-v1/model.pt exists,
otherwise a small pebble model is trained for a few hundred steps on real rows.
"""
from __future__ import annotations

import os
import time

import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("pyarrow")

from models import pep_int  # noqa: E402
from models.pep import PEP, PEPConfig, features_to_tensors, load_checkpoint  # noqa: E402
from models.pep_int import (  # noqa: E402
    I16_MAX,
    I32_MAX,
    I32_MIN,
    IntPEP,
    gate_lut,
    make_luts,
    requant,
    requant_params,
    rshift_round_even,
    softmax_int,
    srdmh,
)
from models.pep_quant import FakeQuantPEP, infer_tier, load_calibration, quantize  # noqa: E402
from models.pep_weights import ALIGN, TOC_SIZE, load_weights, read_header, read_toc, write_weights  # noqa: E402

torch.set_num_threads(2)

AI_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(AI_DIR, "datasets", "trainer", "v1")
STONE = os.environ.get("PEP_INT_TEST_CKPT", os.path.join(AI_DIR, "checkpoints", "pep", "stone-v1", "model.pt"))


def _first_parquet() -> str:
    if not os.path.isdir(DATA):
        pytest.skip("datasets/trainer/v1 missing")
    files = sorted(f for f in os.listdir(DATA) if f.endswith(".parquet"))
    if not files:
        pytest.skip("no parquet rows")
    return os.path.join(DATA, files[-1])  # the small last shard


# --------------------------------------------------------------------------- fixtures


@pytest.fixture(scope="module")
def battles():
    return load_calibration(_first_parquet(), rows=1200)


@pytest.fixture(scope="module")
def model(tmp_path_factory, battles):
    if os.path.exists(STONE):
        try:
            m, _ = load_checkpoint(STONE)
            return m.eval()
        except Exception:  # being written right now
            pass
    from models.train_pep import build_parser, train

    args = build_parser().parse_args(
        [
            "--data", _first_parquet(), "--run", "t", "--ckpt-dir", str(tmp_path_factory.mktemp("ckpt")),
            "--tier", "pebble", "--steps", "200", "--batch", "8", "--window", "12", "--burnin", "4",
            "--lr", "1e-3", "--warmup", "10", "--max-battles", "200", "--holdout-frac", "0.05",
            "--eval-every", "0", "--save-every", "0", "--log-every", "100", "--device", "cpu",
        ]
    )
    return train(args)["model"].eval()


@pytest.fixture(scope="module")
def qp(model, battles):
    return quantize(model, battles[:80])


@pytest.fixture(scope="module")
def weights_path(tmp_path_factory, qp):
    p = tmp_path_factory.mktemp("w") / "pkai.weights"
    write_weights(qp, p)
    return p


@pytest.fixture(scope="module")
def rows(battles):
    """>= 64 real decision rows (first decision of each battle plus later ones) with events."""
    picks = [(i, t) for i, b in enumerate(battles) for t in range(min(len(b), 3))][:96]
    keys = ("type", "present", "candidate", "cat", "f", "legal", "event")
    return {k: np.stack([getattr(battles[i], k)[t] for i, t in picks]) for k in keys}


def _fp32(model, rows, h=None):
    with torch.no_grad():
        lg, va, hh = model(features_to_tensors(rows), torch.as_tensor(rows["event"]), h)
    return lg.numpy(), va.numpy(), hh.numpy()


# --------------------------------------------------------------------------- primitives


def test_fixed_point_primitives():
    rng = np.random.default_rng(0)
    x = rng.integers(-(2**40), 2**40, size=5000)
    for s in (0, 1, 3, 14, 31):
        got = rshift_round_even(x, s)
        want = np.array([round(int(v) / (1 << s)) for v in x])  # Python round = half to even
        np.testing.assert_array_equal(got, want)
    # per-element shifts
    sh = rng.integers(0, 20, size=x.size)
    got = rshift_round_even(x, sh)
    want = np.array([round(int(v) / (1 << int(s))) for v, s in zip(x, sh)])
    np.testing.assert_array_equal(got, want)
    # VQRDMULH semantics incl. saturation
    assert srdmh(np.array([I32_MIN]), np.array([I32_MIN])).tolist() == [I32_MAX]
    a = rng.integers(I32_MIN, I32_MAX, size=1000)
    m = rng.integers(1 << 30, 1 << 31, size=1000)
    want = np.clip((a.astype(object) * m.astype(object) + (1 << 30)) // (1 << 31), I32_MIN, I32_MAX).astype(np.int64)
    np.testing.assert_array_equal(srdmh(a, m), want)
    # requant multiplier reconstruction
    for real in (1e-4, 0.0123, 0.5, 0.999, 1.0, 3.7, 300.0):
        mult, shift = requant_params(real)
        assert (1 << 30) <= mult < (1 << 31)
        assert abs(mult * 2.0**-31 * 2.0**-shift - real) <= real * 2.0**-30
        acc = np.arange(-2000, 2000) * 37
        got = requant(acc, mult, shift, -32768, 32767)
        want = np.clip(np.rint(acc * real), -32768, 32767)
        assert np.abs(got - want).max() <= 1


def test_luts_match_float_functions():
    luts = make_luts()
    v = np.arange(-32768, 32768)  # every Q3.12 input
    x = v / 4096.0
    sig = gate_lut(v, luts["lut.sigmoid_q14"]) / 16384.0
    tanh = gate_lut(v, luts["lut.tanh_q14"]) / 16384.0
    assert np.abs(sig - 1 / (1 + np.exp(-x))).max() <= 1 / 256
    assert np.abs(tanh - np.tanh(x)).max() <= 1 / 256
    d = np.arange(0, 32768)
    e = pep_int.exp_lut(d, luts["lut.exp_q15"]) / 32768.0
    assert np.abs(e - np.exp(-d / 256.0)).max() <= 1 / 256
    assert luts["lut.exp_q15"][0] == 32767 and luts["lut.exp_q15"][-1] == 0
    assert luts["lut.recip_q15"].min() >= 16384 and luts["lut.recip_q15"].max() <= 32767


def test_softmax_int_against_float():
    rng = np.random.default_rng(1)
    luts = make_luts()
    x = rng.integers(-3000, 3000, size=(200, 27))  # 1/256-nat units, shift 0
    mask = rng.random((200, 27)) < 0.7
    mask[:, 0] = True
    p = softmax_int(x, mask, 0, luts["lut.exp_q15"], luts["lut.recip_q15"]).astype(np.float64) / 256.0
    xf = np.where(mask, x / 256.0, -np.inf)
    pf = np.exp(xf - xf.max(-1, keepdims=True))
    pf /= pf.sum(-1, keepdims=True)
    assert (p[~mask] == 0).all()
    assert np.abs(p - pf).max() <= 1 / 128
    assert np.abs(p.sum(-1) - 1).max() <= 27 / 256
    # masked-out rows: single active entry gets 255/256
    one = np.zeros((1, 27), bool)
    one[0, 5] = True
    p1 = softmax_int(np.zeros((1, 27), np.int64), one, 0, luts["lut.exp_q15"], luts["lut.recip_q15"])
    assert p1[0, 5] == 255 and p1.sum() == 255
    # scaled input path (shift > 0) agrees with the pre-scaled one up to rounding
    big = x * 16
    p2 = softmax_int(big, mask, 4, luts["lut.exp_q15"], luts["lut.recip_q15"])
    assert np.array_equal(p2.astype(np.int64), (p * 256).round().astype(np.int64))


# --------------------------------------------------------------------------- (a) determinism + round trip


def test_int_forward_deterministic_and_roundtrip(qp, weights_path, rows):
    ip = IntPEP(qp)
    ev8 = ip.quantize_event(rows["event"])
    a = ip.forward(rows, ev8, None, record=True)
    b = ip.forward(rows, ev8, None, record=True)
    qp2 = load_weights(weights_path)
    assert list(qp2.tensors) == list(qp.tensors) and qp2.config == qp.config and qp2.tier == qp.tier
    for k, v in qp.tensors.items():
        assert qp2.tensors[k].dtype == v.dtype and np.array_equal(qp2.tensors[k], v), k
    assert qp2.scales == qp.scales
    c = IntPEP(qp2).forward(rows, ev8, None, record=True)
    for out in (b, c):
        assert list(out.trace) == list(a.trace)
        for k in a.trace:
            assert a.trace[k].dtype == out.trace[k].dtype and np.array_equal(a.trace[k], out.trace[k]), k
        for k in ("logits_q8", "logits_t", "probs", "value_acc", "value", "h"):
            assert np.array_equal(getattr(a, k), getattr(out, k)), k
    assert a.logits_q8.dtype == np.int16 and a.probs.dtype == np.uint8 and a.h.dtype == np.int16
    assert a.value_acc.dtype == np.int32


# --------------------------------------------------------------------------- (b) agreement with fp32


def test_int_matches_fp32_on_real_rows(model, qp, rows):
    assert rows["present"].shape[0] >= 64
    ip = IntPEP(qp)
    out = ip.forward(rows, ip.quantize_event(rows["event"]))
    lg, va, _ = _fp32(model, rows)
    legal = np.isfinite(lg)
    assert np.array_equal(legal, out.logits_q8 != -32768)  # masks identical
    n = 0
    agree = 0
    for i in range(lg.shape[0]):
        if legal[i].sum() < 2:
            continue
        n += 1
        agree += int(out.logits_q8[i][legal[i]].argmax() == lg[i][legal[i]].argmax())
    assert n >= 64
    agreement = agree / n
    p_int = out.probs.astype(np.float64) / 256.0
    p_f = torch.softmax(torch.as_tensor(lg) / 0.5, dim=-1).numpy()
    mad = float(np.abs(p_int - p_f)[legal].mean())
    print(f"\n[int-vs-fp32] rows={n} argmax_agreement={agreement:.3f} mean_abs_prob_diff(T=0.5)={mad:.4f}")
    assert agreement >= 0.95, agreement
    assert mad < 0.05, mad
    # value head (int32 accumulator, dequantised) tracks the fp32 value logit
    assert np.abs(out.value - va).mean() < 0.1


# --------------------------------------------------------------------------- (c) fake-quant == int path


def test_fake_quant_matches_int(model, qp, rows):
    ip = IntPEP(qp)
    out = ip.forward(rows, ip.quantize_event(rows["event"]))
    fqm = FakeQuantPEP(model, qp).eval()
    with torch.no_grad():
        lq, vq, hq = fqm(features_to_tensors(rows), torch.as_tensor(rows["event"]), None)
    lq = lq.numpy()
    legal = np.isfinite(lq)
    assert np.array_equal(legal, out.logits_q8 != -32768)
    d = np.abs(out.logits_q8.astype(np.float64) / 256.0 - lq)[legal]
    print(f"\n[int-vs-fakequant] logits mean_abs={d.mean():.4f} max={d.max():.4f}")
    assert d.mean() < 0.02 and d.max() < 0.25, (d.mean(), d.max())
    assert np.abs(out.h.astype(np.float64) / 16384.0 - hq.numpy()).max() < 0.02
    assert (np.abs(out.value - vq.numpy()) < 0.05 + 0.02 * np.abs(vq.numpy())).all()
    # same signature as PEP and gradients flow to the fp32 parameters (QAT-ready)
    fqm.train()
    lq, vq, _ = fqm(features_to_tensors(rows), torch.as_tensor(rows["event"]))
    loss = torch.logsumexp(lq.masked_fill(~torch.isfinite(lq), -1e9), dim=-1).mean() + vq.mean()
    loss.backward()
    assert model.layers[0].ff1.weight.grad is not None and model.layers[0].ff1.weight.grad.abs().sum() > 0
    assert model.embed.species.weight.grad is not None
    model.zero_grad(set_to_none=True)


# --------------------------------------------------------------------------- (d) recurrent path


def test_recurrent_state_carries(model, qp, battles):
    ip = IntPEP(qp)
    fqm = FakeQuantPEP(model, qp).eval()
    bt = max(battles, key=len)
    T = 5
    assert len(bt) >= T
    step = {k: getattr(bt, k)[:T] for k in ("type", "present", "candidate", "cat", "f", "legal")}
    outs = ip.run_battle(step, bt.event[:T])
    hs = np.stack([o.h[0] for o in outs]).astype(np.float64) / 16384.0
    assert hs.shape == (T, ip.gru)
    assert np.abs(hs).max() <= 1.0 + 1e-9
    for t in range(1, T):
        assert not np.array_equal(outs[t].h, outs[t - 1].h)
    # stepping one decision at a time equals feeding the carried state explicitly
    h = None
    for t in range(T):
        one = {k: v[t : t + 1] for k, v in step.items()}
        o = ip.forward(one, ip.quantize_event(bt.event[t : t + 1]), h)
        assert np.array_equal(o.h, outs[t].h) and np.array_equal(o.logits_q8, outs[t].logits_q8)
        h = o.h
    # fake-quant and fp32 sequences follow the integer state
    hq = None
    hf = None
    with torch.no_grad():
        for t in range(T):
            one = features_to_tensors({k: v[t : t + 1] for k, v in step.items()})
            ev = torch.as_tensor(bt.event[t : t + 1])
            _, _, hq = fqm(one, ev, hq)
            _, _, hf = model(one, ev, hf)
            assert np.abs(hq.numpy()[0] - hs[t]).max() < 0.03, t
            assert np.abs(hf.numpy()[0] - hs[t]).max() < 0.15, t
    # a fresh zero state differs from the carried one on the last step
    o0 = ip.forward({k: v[T - 1 : T] for k, v in step.items()}, ip.quantize_event(bt.event[T - 1 : T]), None)
    assert not np.array_equal(o0.h, outs[T - 1].h)


# --------------------------------------------------------------------------- (e) container


def test_weights_header_and_toc(qp, weights_path):
    data = open(weights_path, "rb").read()
    h = read_header(data)
    assert h["magic"] == "PKAI" and h["version"] == 1 and h["tier"] == qp.tier
    assert h["feature_schema"] == "pkai-features-v1" and h["rom_crc32"] == 0
    assert {k: h["config"][k] for k in ("d", "layers", "heads", "ffn", "gru")} == {k: qp.config[k] for k in ("d", "layers", "heads", "ffn", "gru")}
    assert h["n_tensors"] == len(qp.tensors) + 2 and h["file_size"] == len(data)
    assert h["toc_offset"] == 128 and h["data_offset"] >= h["toc_offset"] + h["n_tensors"] * TOC_SIZE
    toc = read_toc(data, h)
    names = [e["name"] for e in toc]
    assert len(set(names)) == len(names)
    assert names[: len(qp.tensors)] == list(qp.tensors) and names[-2:] == ["scales.names", "scales.values"]
    prev_end = h["data_offset"]
    for e in toc:
        assert e["offset"] % ALIGN == 0 and e["offset"] >= prev_end
        assert e["nbytes"] == int(np.prod(e["shape"], dtype=np.int64)) * np.dtype(e["dtype"]).itemsize
        prev_end = e["offset"] + e["nbytes"]
    assert prev_end <= h["file_size"]
    for lut in ("lut.exp_q15", "lut.recip_q15", "lut.sigmoid_q14", "lut.tanh_q14"):
        assert lut in names
    dtypes = {e["dtype"] for e in toc}
    assert dtypes <= {"int8", "uint8", "int16", "int32", "float64"}
    for e in toc:
        if e["name"].endswith(".w") or e["name"].startswith("emb."):
            assert e["dtype"] == "int8", e
        if e["name"].endswith(".b") or e["name"].endswith(".mult"):
            assert e["dtype"] == "int32", e
        if e["name"].endswith(".shift") or e["name"].endswith("logit_shift"):
            assert e["dtype"] == "int8", e
        if "alpha" in e["name"] or e["name"].endswith("b_type_q8"):
            assert e["dtype"] == "int16", e
    with pytest.raises(ValueError):
        read_header(b"NOPE" + data[4:])


def test_export_cli_writes_vectors(tmp_path, model):
    from models.pep import save_checkpoint
    from tools.export_weights import export

    ckpt = tmp_path / "model.pt"
    save_checkpoint(model, str(ckpt), 1, {"tier": infer_tier(model)})
    out = tmp_path / "pkai.weights"
    vec = tmp_path / "vectors"
    t0 = time.time()
    rep = export(str(ckpt), _first_parquet(), str(out), str(vec), calib_rows=300, vector_rows=32, tier=None)
    assert out.exists() and rep["file_size"] == os.path.getsize(out)
    for f in ("inputs.npz", "intermediates.npz", "outputs.npz", "fp32.npz", "sequence.npz", "manifest.json"):
        assert (vec / f).exists(), f
    inp = np.load(vec / "inputs.npz")
    assert inp["f"].shape[0] >= 32 and inp["h0"].dtype == np.int16 and inp["ev8"].dtype == np.int8
    assert inp["legal_mask"].shape == (inp["f"].shape[0], 16)
    outs = np.load(vec / "outputs.npz")
    inter = np.load(vec / "intermediates.npz")
    assert set(inter.files) >= {"embed.out16", "layers.0.attn.probs_u8", "gru.h_q14", "ptr.logits_t", "ptr.probs_u8"}
    # the vectors are reproducible from the exported file alone
    ip = IntPEP(load_weights(out))
    feats = {k: inp[k] for k in ("type", "present", "candidate", "cat", "f", "legal")}
    again = ip.forward(feats, inp["ev8"], inp["h0"], record=True)
    assert np.array_equal(again.logits_t, outs["logits_t"]) and np.array_equal(again.probs, outs["probs"])
    assert np.array_equal(again.h, outs["h"])
    for k in inter.files:
        assert np.array_equal(again.trace[k], inter[k]), k
    seq = np.load(vec / "sequence.npz")
    assert seq["h"].shape[1] == 5 and seq["steps"].max() == 5
    print(f"\n[export] {time.time() - t0:.1f}s agree={rep['vectors']['argmax_agreement_fp32']:.3f}")
