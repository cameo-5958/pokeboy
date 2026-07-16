"""Imitation-learning trainer (Phase 1 bootstrap + Phase 2 AWR-filtered BC).

  uv run python -m models.train_imitation --tier snack --steps 300 --limit-rows 50000

Streams schema_v1 parquet from datasets/processed/**, tokenizes, CE on
actions; with --value-bins a two-hot value head trains on battle outcomes
(SPECS §4.2) and --awr weights the action CE by exp(A/beta) with
A = 2*(won - V(s)) on the +1/-1 return scale (SPECS §5.2). Saves
checkpoints + metrics to checkpoints/<tier>/ (gitignored).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

from models.encoder import FieldValueEncoder
from models.tiers import TIERS
from models.tokenizer import Tokenizer

ROOT = Path(__file__).resolve().parents[1]

AWR_WEIGHT_CAP = 20.0


def two_hot(p: torch.Tensor, n_bins: int) -> torch.Tensor:
    """(B,) win probs in [0,1] -> (B, n_bins) two-hot targets over evenly
    spaced bin centers; mass splits between the two nearest centers so the
    distribution's expectation equals p exactly."""
    scaled = p.clamp(0.0, 1.0) * (n_bins - 1)
    lo = scaled.floor().long().clamp(max=n_bins - 2)
    frac = (scaled - lo.float()).unsqueeze(-1)
    t = torch.zeros(p.shape[0], n_bins, device=p.device)
    t.scatter_(1, lo.unsqueeze(-1), 1.0 - frac)
    t.scatter_add_(1, (lo + 1).unsqueeze(-1), frac)
    return t


def value_estimate(value_logits: torch.Tensor) -> torch.Tensor:
    """(B, n_bins) logits -> (B,) expected win prob."""
    centers = torch.linspace(0.0, 1.0, value_logits.shape[-1], device=value_logits.device)
    return value_logits.softmax(-1) @ centers


def awr_weights(won: torch.Tensor, v: torch.Tensor, beta: float) -> torch.Tensor:
    """exp(A/beta) on the +1/-1 return scale: A = 2*(won - V(s)) in [-2, 2]."""
    return torch.exp(2.0 * (won - v) / beta).clamp(max=AWR_WEIGHT_CAP)


def load_rows(limit: int, sources: list[str], seed: int = 0) -> list[dict]:
    import pyarrow.parquet as pq

    parts = []
    for src in sources:
        parts += sorted((ROOT / "datasets" / "processed" / src).rglob("part-*.parquet"))
    rng = random.Random(seed)
    rng.shuffle(parts)
    rows: list[dict] = []
    for path in parts:
        table = pq.read_table(path, columns=["battle_id", "state_json", "action", "elo", "won"])
        rows.extend(table.to_pylist())
        if len(rows) >= limit:
            break
    rng.shuffle(rows)
    return rows[:limit]


def make_batches(
    rows: list[dict],
    tok: Tokenizer,
    batch_size: int,
    device: str,
    weights: list[float] | None = None,
    augment_rng: random.Random | None = None,
):
    if not rows:
        raise ValueError("empty rows for make_batches")
    batch_size = min(batch_size, len(rows))
    for i in range(0, len(rows) - batch_size + 1, batch_size):
        chunk = rows[i : i + batch_size]
        encs, labels, wons = [], [], []
        for r in chunk:
            state = json.loads(r["state_json"])
            tail = state.get("history_tail")
            if augment_rng is not None and tail and augment_rng.random() < 0.5:
                # truncate to a random depth so the policy stays calibrated
                # across tail densities (train/live mismatch, AI ablation)
                state["history_tail"] = tail[: augment_rng.randrange(len(tail) + 1)]
            encs.append(tok.encode(state))
            labels.append(min(r["action"], 9))
            wons.append(1.0 if r.get("won") else 0.0)
        yield (
            dict(
                field_ids=torch.tensor(
                    np.stack([e["field_ids"] for e in encs]), dtype=torch.long, device=device
                ),
                value_ids=torch.tensor(
                    np.stack([e["value_ids"] for e in encs]), dtype=torch.long, device=device
                ),
                slot_ids=torch.tensor(
                    np.stack([e["slot_ids"] for e in encs]), dtype=torch.long, device=device
                ),
                cont=torch.tensor(
                    np.stack([e["cont"] for e in encs]), dtype=torch.float32, device=device
                ),
                lengths=torch.tensor(
                    [e["length"] for e in encs], dtype=torch.long, device=device
                ),
            ),
            torch.tensor(labels, dtype=torch.long, device=device),
            None
            if weights is None
            else torch.tensor(weights[i : i + batch_size], dtype=torch.float32, device=device),
            torch.tensor(wons, dtype=torch.float32, device=device),
        )


def row_weights(rows: list[dict], mode: str) -> list[float]:
    """CE weight per row. 'elo': unrated floor 0.25, linear 1100→1600 up to 1.0;
    'winners': keep only won battles; 'elo+winners': both."""
    if mode == "none":
        return [1.0] * len(rows)
    ws = []
    for r in rows:
        w = 1.0
        if mode in ("elo", "elo+winners"):
            elo = r.get("elo") or 0
            w = 0.25 + 0.75 * min(max((elo - 1100) / 500.0, 0.0), 1.0) if elo else 0.25
        if mode in ("winners", "elo+winners"):
            w *= 1.0 if r.get("won") else 0.0
        ws.append(w)
    return ws


def periodic_eval(
    model,
    tok: Tokenizer,
    hold_rows: list[dict],
    device: str,
    battles: int = 50,
    seed: int = 0,
    max_top1_rows: int = 25_000,
) -> dict:
    """Holdout top-1 + quick win rates vs the scripted bots; restores train mode."""
    from models.agent import ModelAgent
    from sim.agents import MaxDamageBot, RandomBot
    from sim.battle import run_battle
    from sim.teams import sample_team

    was_training = model.training
    model.eval()
    correct = total = 0
    with torch.no_grad():
        # batch 128: banquet at train-batch 128 peaks 8.5/10 GB — a 256 eval
        # batch on top of resident optimizer state would risk OOM mid-run
        for batch, labels, _, _ in make_batches(hold_rows[:max_top1_rows], tok, 128, device) \
                if hold_rows else ():
            correct += (model(**batch).argmax(-1) == labels).sum().item()
            total += len(labels)
    metrics = {"holdout_top1": round(correct / max(1, total), 4)} if hold_rows else {}
    # mixed teams are THE benchmark distribution (2026-07-16 decision);
    # standard sets are only a fallback when the teams corpus is absent
    try:
        from sim.teamsets import mixed_sample_team as pick_team

        pick_team(random.Random(0))  # force pool load; raises if absent
    except (FileNotFoundError, ValueError):
        pick_team = sample_team
    from sim.agents import LessEffectiveMaxDamageBot, UniversalMaxDamageBot

    opponents = {
        "random": lambda r: RandomBot(r),
        "maxdamage": lambda r: MaxDamageBot(),
        "umaxdamage": lambda r: UniversalMaxDamageBot(),
        "lemaxdamage": lambda r: LessEffectiveMaxDamageBot(),
    }
    for name, factory in opponents.items():
        rng = random.Random(seed)
        agent = ModelAgent.from_model(model, tok, seed=seed)
        wins = 0
        for _ in range(battles):
            rec = run_battle(
                agent,
                factory(rng.randrange(2**31)),
                pick_team(rng),
                pick_team(rng),
                seed=rng.randrange(2**63),
            )
            wins += rec.winner == "p1"
        metrics[f"wr_{name}"] = round(wins / max(1, battles), 4)
    if was_training:
        model.train()
    return metrics


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--tier", default="snack", choices=list(TIERS))
    p.add_argument("--steps", type=int, default=300)
    p.add_argument("--batch-size", type=int, default=128)
    p.add_argument("--lr", type=float, default=3e-4)
    p.add_argument("--limit-rows", type=int, default=50_000)
    p.add_argument("--sources", default="metamon,pokechamp,showdown")
    p.add_argument("--holdout", type=float, default=0.05)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--weighting", default="none", choices=["none", "elo", "winners", "elo+winners"])
    p.add_argument("--hist-k", type=int, default=0, help="history turns to encode (0=stateless)")
    p.add_argument("--tail-augment", action="store_true",
                   help="randomly truncate history tails during training")
    p.add_argument("--eval-every", type=int, default=0, help="steps between periodic evals (0=off)")
    p.add_argument("--eval-battles", type=int, default=50)
    p.add_argument("--dmg-feats", action="store_true",
                   help="decision-relevant move features (dmg_frac/kills/acc/wasted)")
    p.add_argument("--value-bins", type=int, default=0,
                   help="two-hot value head bins (0=no value head; SPECS says 32)")
    p.add_argument("--value-loss-weight", type=float, default=0.5)
    p.add_argument("--awr", action="store_true",
                   help="weight action CE by exp(2*(won - V)/beta) from the detached value head")
    p.add_argument("--awr-beta", type=float, default=3.0)
    p.add_argument("--awr-warmup", type=int, default=1000,
                   help="steps of plain CE before AWR weights kick in (value head needs to settle)")
    p.add_argument("--bf16", action="store_true", help="autocast forward/backward to bfloat16")
    p.add_argument("--ckpt-root", default=str(ROOT / "checkpoints"))
    args = p.parse_args()
    if args.steps <= 0 or args.batch_size <= 0 or args.limit_rows <= 0:
        p.error("--steps, --batch-size and --limit-rows must be positive")
    if not 0 < args.holdout < 1:
        p.error("--holdout must be in (0, 1)")
    if args.eval_every < 0 or args.eval_battles < 0 or args.hist_k < 0:
        p.error("--eval-every, --eval-battles and --hist-k must be >= 0")
    if args.awr and not args.value_bins:
        p.error("--awr needs a value head; pass --value-bins 32")
    if args.value_bins < 0 or args.value_bins == 1:
        p.error("--value-bins must be 0 or >= 2")
    if args.awr_beta <= 0 or args.awr_warmup < 0:
        p.error("--awr-beta must be > 0 and --awr-warmup >= 0")

    torch.manual_seed(args.seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    tok = Tokenizer(hist_k=args.hist_k, dmg_feats=args.dmg_feats)
    model = FieldValueEncoder(TIERS[args.tier], tok, value_bins=args.value_bins).to(device)
    print(json.dumps({"tier": args.tier, "params": model.num_params(), "device": device}))

    rows = load_rows(args.limit_rows, args.sources.split(","), seed=args.seed)
    # battle-level split: a battle's rows never straddle train/holdout
    import hashlib

    def is_holdout(r: dict) -> bool:
        h = hashlib.sha1(r["battle_id"].encode()).digest()[0] / 255.0
        return h < args.holdout

    hold = [r for r in rows if is_holdout(r)]
    train = [r for r in rows if not is_holdout(r)]
    weights = row_weights(train, args.weighting)
    if args.weighting in ("winners", "elo+winners"):  # drop dead rows up front
        train = [r for r, w in zip(train, weights) if w > 0]
        weights = [w for w in weights if w > 0]
    if not train or not hold:
        raise SystemExit(
            f"degenerate split: train={len(train)} holdout={len(hold)} "
            f"(rows={len(rows)}, weighting={args.weighting})"
        )
    print(
        json.dumps(
            {"rows": len(rows), "train": len(train), "holdout": len(hold), "weighting": args.weighting}
        )
    )

    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, betas=(0.9, 0.95), weight_decay=0.01)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=args.steps, eta_min=args.lr / 10)

    phase2 = (
        ("-dmg" if args.dmg_feats else "")
        + (f"-v{args.value_bins}" if args.value_bins else "")
        + (f"-awr{args.awr_beta:g}" if args.awr else "")
    )
    run_id = (
        f"{args.tier}-{args.weighting}-h{args.hist_k}{phase2}"
        f"-r{args.limit_rows}-s{args.steps}-seed{args.seed}"
    )
    tier_dir = Path(args.ckpt_root) / args.tier
    run_dir = tier_dir / f"{run_id}-{time.strftime('%m%d-%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    metrics = open(run_dir / "metrics.jsonl", "a")

    published = False

    def save_ckpt(step: int) -> None:
        """Atomic checkpoint write; "latest" is published only once a real
        checkpoint exists, so it never points at an empty/partial run."""
        nonlocal published
        tmp = run_dir / ".model.pt.tmp"
        torch.save(
            {"model": model.state_dict(), "tier": args.tier, "steps": step,
             "hist_k": args.hist_k, "seq_len": tok.seq_len, "value_bins": args.value_bins,
             "dmg_feats": args.dmg_feats},
            tmp,
        )
        os.replace(tmp, run_dir / "model.pt")
        if not published:
            tmp_link = tier_dir / f".latest-{os.getpid()}"
            os.symlink(run_dir.name, tmp_link)
            os.replace(tmp_link, tier_dir / "latest")
            published = True

    def log_eval(step: int) -> None:
        m = periodic_eval(model, tok, hold, device, battles=args.eval_battles, seed=args.seed)
        line = {"run": run_id, "step": step, **m, "s": round(time.monotonic() - t0, 1)}
        print(json.dumps(line), flush=True)
        with open(tier_dir / "evals.jsonl", "a") as f:
            f.write(json.dumps(line) + "\n")
        save_ckpt(step)

    autocast = torch.autocast(
        device_type="cuda", dtype=torch.bfloat16, enabled=args.bf16 and device == "cuda"
    )
    step, t0 = 0, time.monotonic()
    model.train()
    while step < args.steps:
        for batch, labels, w, wons in make_batches(
            train, tok, args.batch_size, device, weights,
            augment_rng=random.Random(args.seed) if args.tail_augment else None,
        ):
            opt.zero_grad()
            with autocast:
                if args.value_bins:
                    logits, vlogits = model(**batch, return_value=True)
                    vloss = F.cross_entropy(vlogits.float(), two_hot(wons, args.value_bins))
                else:
                    logits, vloss = model(**batch), None
                per_row = F.cross_entropy(logits, labels, reduction="none")
                if args.awr and step >= args.awr_warmup:
                    v = value_estimate(vlogits.detach().float())
                    per_row = per_row * awr_weights(wons, v, args.awr_beta)
                if w is not None:
                    per_row = per_row * w
                loss = per_row.sum() / (w.sum().clamp_min(1e-8) if w is not None else len(labels))
                if vloss is not None:
                    loss = loss + args.value_loss_weight * vloss
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            sched.step()
            step += 1
            if step % 25 == 0 or step == 1:
                line = {"step": step, "loss": round(loss.item(), 4),
                        "lr": sched.get_last_lr()[0], "s": round(time.monotonic() - t0, 1)}
                if vloss is not None:
                    line["vloss"] = round(vloss.item(), 4)
                print(json.dumps(line), flush=True)
                metrics.write(json.dumps(line) + "\n")
            if args.eval_every and step % args.eval_every == 0 and step < args.steps:
                log_eval(step)
            if step >= args.steps:
                break

    model.eval()
    correct = total = 0
    with torch.no_grad():
        for batch, labels, _, _ in make_batches(hold, tok, args.batch_size, device):
            correct += (model(**batch).argmax(-1) == labels).sum().item()
            total += len(labels)
    acc = correct / max(1, total)
    majority = max(np.bincount([min(r["action"], 9) for r in hold])) / max(1, len(hold))
    result = {"holdout_top1": round(acc, 4), "majority_baseline": round(float(majority), 4)}
    print(json.dumps(result), flush=True)
    metrics.write(json.dumps(result) + "\n")
    metrics.close()
    if args.eval_every:
        log_eval(args.steps)
    save_ckpt(args.steps)
    print(f"saved {run_dir / 'model.pt'}")


if __name__ == "__main__":
    main()
