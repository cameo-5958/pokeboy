"""Export a PEP checkpoint to `pkai.weights` (§9.4) plus C++ acceptance vectors.

    cd ai && uv run python -m tools.export_weights \\
        --checkpoint checkpoints/pep/stone-v1/model.pt --calib datasets/trainer/v1 \\
        --out checkpoints/pep/stone-v1/pkai.weights --vectors checkpoints/pep/stone-v1/vectors

Pipeline: load fp32 checkpoint -> calibrate activation scales on real decisions
(models.pep_quant.quantize) -> write the container (models.pep_weights) -> reload it with
the numpy-only reader -> run the integer reference (models.pep_int.IntPEP) -> dump
`<vectors>/inputs.npz`, `intermediates.npz`, `outputs.npz`, `fp32.npz`, `sequence.npz`
and `manifest.json`.

Places where §8.3 was interpreted (kept here so the spec can be updated):
  * Mixed-scale GEMM inputs (features ⊕ embeddings, event ⊕ c_s, c_s ⊕ h): the per-piece
    input scale is folded into the weight columns before per-channel quantisation; the
    kernel sees one int8 vector.  The token-type embedding is folded into the projection
    bias (token type is fixed per slice).
  * One residual-stream scale for the whole encoder (int16 has the headroom); "per-layer"
    would need a stream requant at every layer boundary.
  * ReZero: alpha_q14 = round(alpha * s_branch / s_res * 2^14): the Q1.14 alpha is relative
    to the branch's int16 scale, which is calibrated (max * 1.25) rather than tied to s_res.
  * Q/K (and pointer) scales are rounded *up* to the nearest value on the sqrt(2) grid that
    makes s^2 * 256 / sqrt(dh) a power of two, so max-x -> 1/256 nat is a pure shift.
  * Requant = VQRDMULH-style high multiply (ties toward +inf) then round-half-to-even shift;
    shifts are per channel and may be negative (saturating left shift before the multiply).
  * Interpolated LUTs carry 257 entries (256 + sentinel); interpolation shifts floor.
  * GRU requant multipliers are per output channel (superset of "one per gate").
  * Probabilities are uint8 with 255 = 255/256 (1.0 not representable).
  * The event vector is int8 at 1/127 (dataset values are fractions / flags).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np
import torch

from models.pep import EV_DIM, features_to_tensors, load_checkpoint
from models.pep_data import Battle, collate_battles
from models.pep_int import IntPEP
from models.pep_quant import load_calibration, quantize
from models.pep_weights import describe, load_weights, write_weights


def _flat(battles: list[Battle], picks: list[tuple[int, int]]) -> dict[str, np.ndarray]:
    """Stack decisions (battle index, step) into (N, ...) arrays."""
    keys = ("type", "present", "candidate", "cat", "f", "legal", "request_kind", "event")
    return {k: np.stack([getattr(battles[b], k)[t] for b, t in picks]) for k in keys}


def pick_vector_rows(battles: list[Battle], n: int, warm: int = 2, seed: int = 0) -> list[tuple[int, int]]:
    """Prefer decisions preceded by `warm` steps (so the hidden state is non-trivial)."""
    rng = np.random.default_rng(seed)
    cands = [(i, warm) for i, b in enumerate(battles) if len(b) > warm]
    if len(cands) < n:
        cands += [(i, 0) for i, b in enumerate(battles) if len(b) <= warm]
    idx = rng.permutation(len(cands))[:n]
    return [cands[int(i)] for i in sorted(idx)]


@torch.no_grad()
def fp32_reference(model, feats: dict[str, np.ndarray], ev: np.ndarray, h0: torch.Tensor):
    t = features_to_tensors(feats)
    logits, value, h = model(t, torch.as_tensor(ev), h0)
    probs = torch.softmax(logits, dim=-1)
    probs_t = torch.softmax(logits / 0.5, dim=-1)
    return logits.numpy(), probs.numpy(), probs_t.numpy(), value.numpy(), h.numpy()


def dump_vectors(model, ip: IntPEP, battles: list[Battle], out_dir: str, n_rows: int, seq_steps: int = 5) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    picks = pick_vector_rows(battles, n_rows)
    # warm-up: run the integer model over the preceding decisions to get a real h0 per row
    h0 = np.zeros((len(picks), ip.gru), np.int16)
    h0_f = np.zeros((len(picks), ip.gru), np.float32)
    for r, (b, t) in enumerate(picks):
        if t > 0:
            bt = battles[b]
            step = {k: getattr(bt, k)[:t] for k in ("type", "present", "candidate", "cat", "f", "legal")}
            outs = ip.run_battle(step, bt.event[:t])
            h0[r] = outs[-1].h[0]
            with torch.no_grad():
                tt = features_to_tensors(step)
                _, _, hf = model.forward_seq({k: v[None] for k, v in tt.items()}, torch.as_tensor(bt.event[:t])[None])
            h0_f[r] = hf[0, -1].numpy()
    feats = _flat(battles, picks)
    ev8 = ip.quantize_event(feats["event"])
    out = ip.forward(feats, ev8, h0, record=True)
    lg, pr, prt, va, hf = fp32_reference(model, feats, feats["event"], torch.as_tensor(h0_f))

    np.savez(
        os.path.join(out_dir, "inputs.npz"),
        type=feats["type"], present=feats["present"], candidate=feats["candidate"], cat=feats["cat"], f=feats["f"],
        legal=feats["legal"], request_kind=feats["request_kind"], event=feats["event"], ev8=ev8, h0=h0,
        legal_mask=((feats["legal"][:, None].astype(np.int64) >> np.arange(16)) & 1).astype(np.uint8),
    )
    np.savez(os.path.join(out_dir, "intermediates.npz"), **out.trace)
    np.savez(
        os.path.join(out_dir, "outputs.npz"),
        logits_q8=out.logits_q8, logits_t=out.logits_t, probs=out.probs, value_acc=out.value_acc, value=out.value, h=out.h,
    )
    np.savez(os.path.join(out_dir, "fp32.npz"), logits=lg, probs=pr, probs_t=prt, value=va, h=hf, h0=h0_f)

    # recurrent sequence: seq_steps decisions of the longest battles from h = 0
    long_ = sorted(range(len(battles)), key=lambda i: -len(battles[i]))[: min(8, len(battles))]
    seq = {"h": [], "logits_q8": [], "probs": []}
    seq_inputs = {k: [] for k in ("type", "present", "candidate", "cat", "f", "legal", "event")}
    for i in long_:
        bt = battles[i]
        T = min(seq_steps, len(bt))
        step = {k: getattr(bt, k)[:T] for k in ("type", "present", "candidate", "cat", "f", "legal")}
        outs = ip.run_battle(step, bt.event[:T])
        pad = lambda a, T0=T: np.concatenate([a, np.zeros((seq_steps - T0, *a.shape[1:]), a.dtype)]) if T0 < seq_steps else a
        seq["h"].append(pad(np.stack([o.h[0] for o in outs])))
        seq["logits_q8"].append(pad(np.stack([o.logits_q8[0] for o in outs])))
        seq["probs"].append(pad(np.stack([o.probs[0] for o in outs])))
        for k in seq_inputs:
            seq_inputs[k].append(pad(getattr(bt, k)[:T]))
    np.savez(
        os.path.join(out_dir, "sequence.npz"),
        steps=np.array([min(seq_steps, len(battles[i])) for i in long_], np.int32),
        **{k: np.stack(v) for k, v in seq_inputs.items()},
        **{k: np.stack(v) for k, v in seq.items()},
    )

    legal = np.isfinite(lg)
    agree = float(np.mean([(out.logits_q8[i][legal[i]].argmax() == lg[i][legal[i]].argmax()) for i in range(len(picks)) if legal[i].any()]))
    p_int = out.probs.astype(np.float64) / 256.0
    mad = float(np.abs(p_int - prt)[legal].mean())
    manifest = {
        "rows": len(picks),
        "picks": picks,
        "argmax_agreement_fp32": agree,
        "mean_abs_prob_diff_T0.5": mad,
        "intermediates": {k: {"dtype": str(v.dtype), "shape": list(v.shape)} for k, v in out.trace.items()},
    }
    with open(os.path.join(out_dir, "manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=1)
    return manifest


def export(checkpoint: str, calib: str, out: str, vectors: str | None, calib_rows: int, vector_rows: int,
           rom_crc32: int = 0) -> dict:
    t0 = time.time()
    model, blob = load_checkpoint(checkpoint)
    model.eval()
    battles = load_calibration(calib, calib_rows)
    n_dec = sum(len(b) for b in battles)
    print(f"[calib] battles={len(battles)} decisions={n_dec} ({time.time() - t0:.1f}s)", file=sys.stderr)
    qp = quantize(model, battles)
    qp.rom_crc32 = rom_crc32
    print(f"[quant] tensors={len(qp.tensors)} bytes={qp.nbytes():,} ({time.time() - t0:.1f}s)", file=sys.stderr)
    toc = write_weights(qp, out)
    qp2 = load_weights(out)
    for k, v in qp.tensors.items():
        assert np.array_equal(qp2.tensors[k], v), k
    print(f"[export] {out} size={os.path.getsize(out):,} tensors={len(toc)}", file=sys.stderr)
    report = {"checkpoint": checkpoint, "config": dict(qp.config), "out": out, "file_size": os.path.getsize(out), "tensors": toc,
              "stats": getattr(qp, "stats", {})}
    if vectors:
        ip = IntPEP(qp2)
        manifest = dump_vectors(model, ip, battles, vectors, vector_rows)
        print(f"[vectors] {vectors} rows={manifest['rows']} argmax_agree={manifest['argmax_agreement_fp32']:.3f} "
              f"mad={manifest['mean_abs_prob_diff_T0.5']:.4f} ({time.time() - t0:.1f}s)", file=sys.stderr)
        report["vectors"] = manifest
    return report


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Quantise a PEP checkpoint and write pkai.weights (+ reference vectors).")
    p.add_argument("--checkpoint", required=True)
    p.add_argument("--calib", required=True, help="parquet file/dir of decision rows for activation calibration")
    p.add_argument("--out", required=True, help="output pkai.weights path")
    p.add_argument("--vectors", default=None, help="directory for reference vectors (*.npz)")
    p.add_argument("--calib-rows", type=int, default=4096)
    p.add_argument("--vector-rows", type=int, default=64)
    p.add_argument("--rom-crc32", type=lambda s: int(s, 0), default=0)
    p.add_argument("--describe", action="store_true", help="print the TOC after writing")
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    export(args.checkpoint, args.calib, args.out, args.vectors, args.calib_rows, args.vector_rows, args.rom_crc32)
    if args.describe:
        print(describe(args.out))


if __name__ == "__main__":
    main()
