"""Imitation-learning trainer (Phase 1 bootstrap; shakedown-capable).

  uv run python -m models.train_imitation --tier snack --steps 300 --limit-rows 50000

Streams schema_v1 parquet from datasets/processed/**, tokenizes, CE on
actions. Saves checkpoints + metrics to checkpoints/<tier>/ (gitignored).
"""

from __future__ import annotations

import argparse
import json
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
):
    batch_size = min(batch_size, len(rows))
    for i in range(0, len(rows) - batch_size + 1, batch_size):
        chunk = rows[i : i + batch_size]
        encs, labels = [], []
        for r in chunk:
            state = json.loads(r["state_json"])
            encs.append(tok.encode(state))
            labels.append(min(r["action"], 9))
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
        for batch, labels, _ in make_batches(hold_rows[:max_top1_rows], tok, 256, device):
            correct += (model(**batch).argmax(-1) == labels).sum().item()
            total += len(labels)
    metrics = {"holdout_top1": round(correct / max(1, total), 4)}
    opponents = {"random": lambda r: RandomBot(r), "maxdamage": lambda r: MaxDamageBot()}
    for name, factory in opponents.items():
        rng = random.Random(seed)
        agent = ModelAgent.from_model(model, tok, seed=seed)
        wins = 0
        for _ in range(battles):
            rec = run_battle(
                agent,
                factory(rng.randrange(2**31)),
                sample_team(rng),
                sample_team(rng),
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
    p.add_argument("--eval-every", type=int, default=0, help="steps between periodic evals (0=off)")
    p.add_argument("--eval-battles", type=int, default=50)
    p.add_argument("--bf16", action="store_true", help="autocast forward/backward to bfloat16")
    args = p.parse_args()

    torch.manual_seed(args.seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    tok = Tokenizer(hist_k=args.hist_k)
    model = FieldValueEncoder(TIERS[args.tier], tok).to(device)
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
    print(
        json.dumps(
            {"rows": len(rows), "train": len(train), "holdout": len(hold), "weighting": args.weighting}
        )
    )

    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, betas=(0.9, 0.95), weight_decay=0.01)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=args.steps, eta_min=args.lr / 10)

    ckpt_dir = ROOT / "checkpoints" / args.tier
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    metrics = open(ckpt_dir / "metrics.jsonl", "a")
    run_id = f"{args.tier}-{args.weighting}-h{args.hist_k}-r{args.limit_rows}-s{args.steps}-seed{args.seed}"

    def log_eval(step: int) -> None:
        m = periodic_eval(model, tok, hold, device, battles=args.eval_battles, seed=args.seed)
        line = {"run": run_id, "step": step, **m, "s": round(time.monotonic() - t0, 1)}
        print(json.dumps(line), flush=True)
        with open(ckpt_dir / "evals.jsonl", "a") as f:
            f.write(json.dumps(line) + "\n")
        torch.save(
            {"model": model.state_dict(), "tier": args.tier, "steps": step,
             "hist_k": args.hist_k, "seq_len": tok.seq_len},
            ckpt_dir / "model.pt",
        )

    autocast = torch.autocast(
        device_type="cuda", dtype=torch.bfloat16, enabled=args.bf16 and device == "cuda"
    )
    step, t0 = 0, time.monotonic()
    model.train()
    while step < args.steps:
        for batch, labels, w in make_batches(train, tok, args.batch_size, device, weights):
            opt.zero_grad()
            with autocast:
                logits = model(**batch)
                if w is None:
                    loss = F.cross_entropy(logits, labels)
                else:
                    per_row = F.cross_entropy(logits, labels, reduction="none")
                    loss = (per_row * w).sum() / w.sum().clamp_min(1e-8)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            sched.step()
            step += 1
            if step % 25 == 0 or step == 1:
                line = {"step": step, "loss": round(loss.item(), 4),
                        "lr": sched.get_last_lr()[0], "s": round(time.monotonic() - t0, 1)}
                print(json.dumps(line), flush=True)
                metrics.write(json.dumps(line) + "\n")
            if args.eval_every and step % args.eval_every == 0 and step < args.steps:
                log_eval(step)
            if step >= args.steps:
                break

    model.eval()
    correct = total = 0
    with torch.no_grad():
        for batch, labels, _ in make_batches(hold, tok, args.batch_size, device):
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
    torch.save(
        {"model": model.state_dict(), "tier": args.tier, "steps": args.steps,
         "hist_k": args.hist_k, "seq_len": tok.seq_len},
        ckpt_dir / "model.pt",
    )
    print(f"saved {ckpt_dir / 'model.pt'}")


if __name__ == "__main__":
    main()
