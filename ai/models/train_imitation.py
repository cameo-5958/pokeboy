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
        table = pq.read_table(path, columns=["state_json", "action", "elo", "won"])
        rows.extend(table.to_pylist())
        if len(rows) >= limit:
            break
    rng.shuffle(rows)
    return rows[:limit]


def make_batches(rows: list[dict], tok: Tokenizer, batch_size: int, device: str):
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
        )


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
    args = p.parse_args()

    torch.manual_seed(args.seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    tok = Tokenizer()
    model = FieldValueEncoder(TIERS[args.tier], tok).to(device)
    print(json.dumps({"tier": args.tier, "params": model.num_params(), "device": device}))

    rows = load_rows(args.limit_rows, args.sources.split(","), seed=args.seed)
    n_hold = max(args.batch_size, int(len(rows) * args.holdout))
    hold, train = rows[:n_hold], rows[n_hold:]
    print(json.dumps({"rows": len(rows), "train": len(train), "holdout": len(hold)}))

    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, betas=(0.9, 0.95), weight_decay=0.01)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=args.steps, eta_min=args.lr / 10)

    ckpt_dir = ROOT / "checkpoints" / args.tier
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    metrics = open(ckpt_dir / "metrics.jsonl", "a")

    step, t0 = 0, time.monotonic()
    model.train()
    while step < args.steps:
        for batch, labels in make_batches(train, tok, args.batch_size, device):
            opt.zero_grad()
            loss = F.cross_entropy(model(**batch), labels)
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
            if step >= args.steps:
                break

    model.eval()
    correct = total = 0
    with torch.no_grad():
        for batch, labels in make_batches(hold, tok, args.batch_size, device):
            correct += (model(**batch).argmax(-1) == labels).sum().item()
            total += len(labels)
    acc = correct / max(1, total)
    majority = max(np.bincount([min(r["action"], 9) for r in hold])) / max(1, len(hold))
    result = {"holdout_top1": round(acc, 4), "majority_baseline": round(float(majority), 4)}
    print(json.dumps(result), flush=True)
    metrics.write(json.dumps(result) + "\n")
    metrics.close()
    torch.save(
        {"model": model.state_dict(), "tier": args.tier, "steps": args.steps},
        ckpt_dir / "model.pt",
    )
    print(f"saved {ckpt_dir / 'model.pt'}")


if __name__ == "__main__":
    main()
