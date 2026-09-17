"""Teacher-label pass for policy distillation.

Runs a teacher checkpoint over existing schema_v1 corpus parts and writes
sidecar parquets under <out>/<src>/<same relative path>: one row per input
row IN THE SAME ORDER - battle_id (echoed for alignment checks), kd_probs
(legality-masked temp-1 teacher policy, 10 float32), kd_v (value-head win
prob, -1.0 when the teacher has no value head). Existing sidecars are
skipped so the pass is resumable; the trainer (`--distill-labels`)
discovers labeled parts from the sidecar tree, so partial labeling narrows
the corpus instead of erroring.

CLI:
  uv run python -m models.distill --ckpt <teacher.pt> --sources teacher2 \
      --out datasets/distill/<teacher-tag> [--batch 512] [--limit-rows N]
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]

N_ACTIONS = 10


@torch.no_grad()
def label_states(model, tok, states: list[dict], device: str, batch_size: int):
    """Label states -> (probs (N,10) float32, v (N,) float32; v=-1 no head)."""
    from models.train_imitation import value_estimate

    has_value = bool(getattr(model, "value_bins", 0))
    probs_out = np.zeros((len(states), N_ACTIONS), dtype=np.float32)
    v_out = np.full(len(states), -1.0, dtype=np.float32)
    for i in range(0, len(states), batch_size):
        chunk = states[i : i + batch_size]
        encs = [tok.encode(s) for s in chunk]
        batch = dict(
            field_ids=torch.tensor(np.stack([e["field_ids"] for e in encs]),
                                   dtype=torch.long, device=device),
            value_ids=torch.tensor(np.stack([e["value_ids"] for e in encs]),
                                   dtype=torch.long, device=device),
            slot_ids=torch.tensor(np.stack([e["slot_ids"] for e in encs]),
                                  dtype=torch.long, device=device),
            cont=torch.tensor(np.stack([e["cont"] for e in encs]),
                              dtype=torch.float32, device=device),
            lengths=torch.tensor([e["length"] for e in encs],
                                 dtype=torch.long, device=device),
        )
        if has_value:
            logits, vlogits = model(**batch, return_value=True)
            v_out[i : i + len(chunk)] = value_estimate(vlogits.float()).cpu().numpy()
        else:
            logits = model(**batch)
        logits = logits.float().cpu()
        mask = torch.full_like(logits, float("-inf"))
        for j, s in enumerate(chunk):
            mask[j, s.get("legal_actions") or list(range(N_ACTIONS))] = 0.0
        probs_out[i : i + len(chunk)] = torch.softmax(logits + mask, dim=-1).numpy()
    return probs_out, v_out


def label_corpus(
    model,
    tok,
    sources: list[str],
    out: Path,
    data_root: Path | None = None,
    batch_size: int = 512,
    device: str = "cpu",
    limit_rows: int | None = None,
) -> dict[str, int]:
    import pyarrow as pa
    import pyarrow.parquet as pq

    data_root = Path(data_root) if data_root else ROOT / "datasets" / "processed"
    out = Path(out)
    model.eval()
    stats = {"parts": 0, "rows": 0, "skipped": 0}
    for src in sources:
        for part in sorted((data_root / src).rglob("part-*.parquet")):
            rel = part.relative_to(data_root / src)
            dest = out / src / rel
            if dest.exists():
                stats["skipped"] += 1
                continue
            if limit_rows is not None and stats["rows"] >= limit_rows:
                return stats
            table = pq.read_table(part, columns=["battle_id", "state_json"])
            ids = table.column("battle_id").to_pylist()
            states = [json.loads(s) for s in table.column("state_json").to_pylist()]
            probs, v = label_states(model, tok, states, device, batch_size)
            side = pa.table(
                {
                    "battle_id": pa.array(ids, pa.string()),
                    "kd_probs": pa.array([row.tolist() for row in probs],
                                         pa.list_(pa.float32(), N_ACTIONS)),
                    "kd_v": pa.array(v.tolist(), pa.float32()),
                }
            )
            dest.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(side, dest)
            stats["parts"] += 1
            stats["rows"] += len(ids)
    return stats


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--ckpt", required=True, help="teacher checkpoint")
    p.add_argument("--sources", required=True,
                   help="comma-separated dirs under datasets/processed")
    p.add_argument("--out", required=True, help="sidecar root (datasets/distill/<tag>)")
    p.add_argument("--batch", type=int, default=512)
    p.add_argument("--limit-rows", type=int, default=None)
    p.add_argument("--device", default=None)
    args = p.parse_args()

    from models.agent import ModelAgent

    agent = ModelAgent(args.ckpt, device=args.device)
    t0 = time.time()
    stats = label_corpus(
        agent.model, agent.tok, args.sources.split(","), Path(args.out),
        batch_size=args.batch, device=agent.device, limit_rows=args.limit_rows,
    )
    stats["s"] = round(time.time() - t0, 1)
    print(json.dumps(stats))


if __name__ == "__main__":
    main()
