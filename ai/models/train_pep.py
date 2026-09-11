"""Imitation-learning trainer for PEP over parquet decision rows.

Parquet columns (one row per decision):
    battle_id str, round int32, seq int32 (order within battle),
    features binary (raw pkai::Features, 1626 B), legal uint16, request_kind uint8,
    event binary float32[64] (optional -> zeros), teacher_probs list<float32>[16] (nullable),
    teacher_value float32 (-1 unknown), action int8, won bool.

Loss: KL(teacher || softmax(logits over legal)) at T=1 (CE on `action` where the
teacher row is missing) + 0.5 * BCE(value, won).  Truncated BPTT over whole
battles: windows of `--window` decisions, the first `--burnin` steps of every
window after the first are re-run from the carried (detached) state without
contributing to the loss.  One optimizer update per window.

Usage:
    cd ai && uv run python -m models.train_pep --data datasets/pep --run stone-v1 --tier stone --steps 20000
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from dataclasses import asdict

import numpy as np
import torch
import torch.nn.functional as F

from models.pep import EV_DIM, N_ACTIONS, PEP, config_for, features_to_tensors, save_checkpoint
from models.pep_data import Battle, FeaturesDataset, collate_battles

DEFAULT_CKPT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "checkpoints", "pep")


# --------------------------------------------------------------------------- loss


def policy_value_loss(
    logits: torch.Tensor,
    value: torch.Tensor,
    teacher_probs: torch.Tensor,
    action: torch.Tensor,
    won: torch.Tensor,
    mask: torch.Tensor,
    value_weight: float = 0.5,
) -> tuple[torch.Tensor, dict[str, float]]:
    """All inputs (B,T,...).  Returns (scalar loss, metrics dict)."""
    logits = logits.float()
    legal = torch.isfinite(logits)
    has_any = legal.any(dim=-1)
    valid = mask & has_any
    safe = logits.masked_fill(~legal, -1e9)
    logp = torch.log_softmax(safe, dim=-1)

    tp = teacher_probs.float()
    has_teacher = torch.isfinite(tp).all(dim=-1) & valid
    tp = torch.nan_to_num(tp, nan=0.0).masked_fill(~legal, 0.0)
    tp_sum = tp.sum(dim=-1, keepdim=True)
    has_teacher &= tp_sum.squeeze(-1) > 1e-6
    tp = tp / tp_sum.clamp_min(1e-6)
    kl = (tp * (torch.log(tp.clamp_min(1e-8)) - logp)).sum(dim=-1)  # KL(teacher || model)

    act = action.long().clamp(0, N_ACTIONS - 1)
    ce = -logp.gather(-1, act[..., None]).squeeze(-1)
    use_ce = valid & ~has_teacher & (action >= 0)

    pol = torch.where(has_teacher, kl, torch.where(use_ce, ce, torch.zeros_like(kl)))
    n_pol = (has_teacher | use_ce).float().sum().clamp_min(1.0)
    pol_loss = pol.sum() / n_pol

    bce = F.binary_cross_entropy_with_logits(value.float(), won.float(), reduction="none")
    n_val = valid.float().sum().clamp_min(1.0)
    val_loss = (bce * valid).sum() / n_val

    loss = pol_loss + value_weight * val_loss
    with torch.no_grad():
        pred = safe.argmax(dim=-1)
        ref = torch.where(has_teacher, tp.argmax(dim=-1), act)
        agree = ((pred == ref) & (has_teacher | use_ce)).float().sum() / n_pol
        kl_mean = (kl * has_teacher).sum() / has_teacher.float().sum().clamp_min(1.0)
    metrics = {
        "loss": float(loss.detach()),
        "policy": float(pol_loss.detach()),
        "kl": float(kl_mean),
        "value": float(val_loss.detach()),
        "top1": float(agree),
        "n": int(n_pol),
    }
    return loss, metrics


# --------------------------------------------------------------------------- batching


def batch_to_device(arrays: dict[str, np.ndarray], device) -> dict[str, torch.Tensor]:
    out = features_to_tensors(arrays, device)
    out["event"] = torch.as_tensor(arrays["event"], device=device)
    out["teacher_probs"] = torch.as_tensor(arrays["teacher_probs"], device=device)
    out["action"] = torch.as_tensor(arrays["action"].astype(np.int64), device=device)
    out["won"] = torch.as_tensor(arrays["won"], device=device)
    out["mask"] = torch.as_tensor(arrays["mask"], device=device)
    return out


def _slice_t(batch: dict[str, torch.Tensor], s: int, e: int) -> dict[str, torch.Tensor]:
    return {k: v[:, s:e] for k, v in batch.items()}


def iter_windows(T: int, window: int, burnin: int):
    """Yield (start, end, loss_start) window bounds over a length-T sequence.

    Windows overlap by `burnin` steps: window k starts at k*(window-burnin) and the
    first `burnin` steps (k>0) are burn-in only.  Every decision gets exactly one
    loss contribution.
    """
    stride = max(window - burnin, 1)
    s = 0
    while s < T:
        e = min(s + window, T)
        yield s, e, (s if s == 0 else min(s + burnin, e))
        if e >= T:
            break
        s += stride


def train_on_batch(
    model: PEP,
    batch: dict[str, torch.Tensor],
    opt: torch.optim.Optimizer,
    sched,
    *,
    window: int,
    burnin: int,
    clip: float,
    autocast_dtype,
    value_weight: float = 0.5,
) -> list[dict[str, float]]:
    """Run truncated-BPTT updates over one padded batch of battles; returns per-window metrics."""
    B, T = batch["mask"].shape
    dev = batch["mask"].device
    h_carry = model.init_hidden(B, dev)
    out: list[dict[str, float]] = []
    use_amp = autocast_dtype is not None and dev.type == "cuda"
    for s, e, ls in iter_windows(T, window, burnin):
        win = _slice_t(batch, s, e)
        feats = {k: win[k] for k in ("type", "present", "candidate", "cat", "f", "legal")}
        with torch.autocast(device_type=dev.type, dtype=autocast_dtype or torch.bfloat16, enabled=use_amp):
            logits, value, h_seq = model.forward_seq(feats, win["event"], h_carry, win["mask"])
        loss_mask = win["mask"].clone()
        loss_mask[:, : ls - s] = False
        loss, m = policy_value_loss(
            logits, value, win["teacher_probs"], win["action"], win["won"], loss_mask, value_weight
        )
        if m["n"] > 0:
            opt.zero_grad(set_to_none=True)
            loss.backward()
            gn = torch.nn.utils.clip_grad_norm_(model.parameters(), clip)
            opt.step()
            if sched is not None:
                sched.step()
            m["grad_norm"] = float(gn)
            out.append(m)
        # state at the start of the next window = state after step (s + stride - 1)
        stride = max(window - burnin, 1)
        nxt = min(stride, e - s) - 1
        h_carry = h_seq[:, nxt].detach()
    return out


@torch.no_grad()
def evaluate(
    model: PEP, battles: list[Battle], device, *, batch_battles: int = 16, chunk: int = 64, autocast_dtype=None
) -> dict[str, float]:
    """Holdout KL / CE and top-1 agreement over full battle sequences (no burn-in)."""
    model.eval()
    tot = {"policy": 0.0, "kl": 0.0, "value": 0.0, "top1": 0.0, "n": 0}
    n_kl = 0
    use_amp = autocast_dtype is not None and device.type == "cuda"
    for i in range(0, len(battles), batch_battles):
        batch = batch_to_device(collate_battles(battles[i : i + batch_battles]), device)
        B, T = batch["mask"].shape
        h = model.init_hidden(B, device)
        for s in range(0, T, chunk):
            win = _slice_t(batch, s, min(s + chunk, T))
            feats = {k: win[k] for k in ("type", "present", "candidate", "cat", "f", "legal")}
            with torch.autocast(device_type=device.type, dtype=autocast_dtype or torch.bfloat16, enabled=use_amp):
                logits, value, h_seq = model.forward_seq(feats, win["event"], h, win["mask"])
            h = h_seq[:, -1]
            _, m = policy_value_loss(logits, value, win["teacher_probs"], win["action"], win["won"], win["mask"])
            n = m["n"]
            tot["policy"] += m["policy"] * n
            tot["value"] += m["value"] * n
            tot["top1"] += m["top1"] * n
            has_t = torch.isfinite(win["teacher_probs"]).all(-1) & win["mask"]
            k = int(has_t.sum())
            tot["kl"] += m["kl"] * k
            n_kl += k
            tot["n"] += n
    model.train()
    n = max(tot["n"], 1)
    return {
        "policy": tot["policy"] / n,
        "kl": tot["kl"] / max(n_kl, 1),
        "value": tot["value"] / n,
        "top1": tot["top1"] / n,
        "n": tot["n"],
    }


# --------------------------------------------------------------------------- driver


def make_scheduler(opt, total_steps: int, warmup: int, floor: float = 0.05):
    def f(step: int) -> float:
        if step < warmup:
            return (step + 1) / max(warmup, 1)
        p = min(1.0, (step - warmup) / max(1, total_steps - warmup))
        return floor + (1 - floor) * 0.5 * (1 + math.cos(math.pi * p))

    return torch.optim.lr_scheduler.LambdaLR(opt, f)


def train(args: argparse.Namespace) -> dict:
    torch.manual_seed(args.seed)
    rng = np.random.default_rng(args.seed)
    device = torch.device(args.device or ("cuda" if torch.cuda.is_available() else "cpu"))
    autocast_dtype = torch.bfloat16 if device.type == "cuda" and not args.no_amp else None

    ds = FeaturesDataset(args.data, max_battles=args.max_battles)
    if len(ds) == 0:
        raise SystemExit("no battles found")
    train_ds, hold_ds = ds.split(args.holdout_frac, seed=args.seed)
    print(
        f"[data] battles={len(ds)} decisions={ds.num_decisions} train={len(train_ds)} holdout={len(hold_ds)}",
        file=sys.stderr,
    )

    cfg = config_for(args.tier, **_overrides(args))
    model = PEP(cfg).to(device)
    print(
        f"[model] tier={args.tier} cfg={asdict(cfg)} params={model.num_params():,} "
        f"(excl matchup {model.num_params(False):,}) device={device}",
        file=sys.stderr,
    )
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.wd, betas=(0.9, 0.95))
    sched = make_scheduler(opt, args.steps, args.warmup)

    run_dir = os.path.join(args.ckpt_dir, args.run)
    os.makedirs(run_dir, exist_ok=True)
    ckpt_path = os.path.join(run_dir, "model.pt")
    log_path = os.path.join(run_dir, "log.jsonl")
    log = open(log_path, "a")

    history: list[dict[str, float]] = []
    evals: list[dict[str, float]] = []
    step = 0
    t0 = time.time()
    model.train()
    while step < args.steps:
        idx = rng.choice(len(train_ds), size=min(args.batch, len(train_ds)), replace=len(train_ds) < args.batch)
        batch = batch_to_device(collate_battles([train_ds[int(i)] for i in idx]), device)
        for m in train_on_batch(
            model,
            batch,
            opt,
            sched,
            window=args.window,
            burnin=args.burnin,
            clip=args.clip,
            autocast_dtype=autocast_dtype,
            value_weight=args.value_weight,
        ):
            step += 1
            m["step"] = step
            m["lr"] = opt.param_groups[0]["lr"]
            history.append(m)
            log.write(json.dumps(m) + "\n")
            if step % args.log_every == 0:
                recent = history[-args.log_every :]
                avg = {k: float(np.mean([r[k] for r in recent])) for k in ("loss", "policy", "value", "top1")}
                print(
                    f"[train] step={step} loss={avg['loss']:.4f} policy={avg['policy']:.4f} "
                    f"value={avg['value']:.4f} top1={avg['top1']:.3f} lr={m['lr']:.2e} "
                    f"{(time.time() - t0):.0f}s",
                    file=sys.stderr,
                )
            if args.eval_every and step % args.eval_every == 0 and len(hold_ds):
                ev = evaluate(model, hold_ds.battles, device, autocast_dtype=autocast_dtype)
                ev["step"] = step
                evals.append(ev)
                log.write(json.dumps({"eval": ev}) + "\n")
                print(
                    f"[eval] step={step} kl={ev['kl']:.4f} policy={ev['policy']:.4f} "
                    f"value={ev['value']:.4f} top1={ev['top1']:.3f} n={ev['n']}",
                    file=sys.stderr,
                )
            if args.save_every and step % args.save_every == 0:
                save_checkpoint(model, ckpt_path, step)
            if step >= args.steps:
                break

    final_eval = evaluate(model, hold_ds.battles, device, autocast_dtype=autocast_dtype) if len(hold_ds) else {}
    if final_eval:
        final_eval["step"] = step
        evals.append(final_eval)
        log.write(json.dumps({"eval": final_eval}) + "\n")
        print(
            f"[eval] final kl={final_eval['kl']:.4f} policy={final_eval['policy']:.4f} "
            f"top1={final_eval['top1']:.3f} n={final_eval['n']}",
            file=sys.stderr,
        )
    save_checkpoint(model, ckpt_path, step, {"tier": args.tier, "final_eval": final_eval})
    log.close()
    print(f"[ckpt] {ckpt_path} (steps={step})", file=sys.stderr)
    return {"steps": step, "history": history, "evals": evals, "checkpoint": ckpt_path, "model": model}


def _overrides(args: argparse.Namespace) -> dict:
    return {k: getattr(args, k) for k in ("d", "layers", "heads", "ffn", "gru") if getattr(args, k) is not None}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Imitation-train the PEP on-device policy from parquet rows.")
    p.add_argument("--data", required=True, help="parquet file, glob, or directory (searched recursively)")
    p.add_argument("--run", required=True, help="run name -> <ckpt-dir>/<run>/model.pt")
    p.add_argument("--tier", default="stone", choices=("pebble", "stone", "boulder"))
    for k in ("d", "layers", "heads", "ffn", "gru"):
        p.add_argument(f"--{k}", type=int, default=None, help=f"override PEPConfig.{k}")
    p.add_argument("--steps", type=int, default=20000, help="optimizer updates (one per TBPTT window)")
    p.add_argument("--batch", type=int, default=32, help="battles per batch")
    p.add_argument("--window", type=int, default=32)
    p.add_argument("--burnin", type=int, default=8)
    p.add_argument("--lr", type=float, default=3e-4)
    p.add_argument("--wd", type=float, default=0.01)
    p.add_argument("--warmup", type=int, default=200)
    p.add_argument("--clip", type=float, default=1.0)
    p.add_argument("--value-weight", type=float, default=0.5)
    p.add_argument("--holdout-frac", type=float, default=0.05)
    p.add_argument("--max-battles", type=int, default=None)
    p.add_argument("--eval-every", type=int, default=1000)
    p.add_argument("--save-every", type=int, default=1000)
    p.add_argument("--log-every", type=int, default=50)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--device", default=None, help="cuda|cpu (default: auto)")
    p.add_argument("--no-amp", action="store_true", help="disable bf16 autocast on CUDA")
    p.add_argument("--ckpt-dir", default=DEFAULT_CKPT_DIR)
    return p


def main(argv: list[str] | None = None) -> None:
    train(build_parser().parse_args(argv))


if __name__ == "__main__":
    main()
