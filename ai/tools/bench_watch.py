"""Benchmark every step checkpoint a training run writes, as it appears.

    uv run python -m tools.bench_watch checkpoints/pep/stone-v2 [more run dirs] --battles 200

Polls each run directory for `step-*.pt` files, evaluates each new one once with
tools.eval_trainer's harness (simulator win-rate vs the greedy 1-ply player over a
fixed matchup sequence, so steps are comparable), and appends a row to
`<run>/bench.csv`: step, win_rate, wins, losses, ties, seconds. Stops when every
run has a `model.pt` newer than its last step checkpoint and `--once` is set, or
runs forever otherwise.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import time

from sim import trainers
from tools.eval_trainer import run

STEP_RE = re.compile(r"step-(\d+)\.pt$")


def done_steps(csv_path: str) -> set[int]:
    if not os.path.exists(csv_path):
        return set()
    with open(csv_path) as fh:
        return {int(r["step"]) for r in csv.DictReader(fh)}


def bench_one(ckpt: str, parties, battles: int, seed: int, temperature: float) -> dict:
    from serve.pep_agent import PEPAgent
    agent = PEPAgent(ckpt, temperature=temperature, seed=seed)
    return run({"pep": agent}, parties, battles, seed)["pep"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--battles", type=int, default=200)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--temperature", type=float, default=0.5)
    ap.add_argument("--interval", type=float, default=60.0)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args(argv)
    parties = trainers.load().parties
    while True:
        did = False
        for run_dir in args.runs:
            csv_path = os.path.join(run_dir, "bench.csv")
            seen = done_steps(csv_path)
            files = sorted((int(m.group(1)), f) for f in os.listdir(run_dir) if (m := STEP_RE.search(f))) if os.path.isdir(run_dir) else []
            for step, f in files:
                if step in seen:
                    continue
                path = os.path.join(run_dir, f)
                if time.time() - os.path.getmtime(path) < 5:
                    continue  # still being written
                r = bench_one(path, parties, args.battles, args.seed, args.temperature)
                new = not os.path.exists(csv_path)
                with open(csv_path, "a", newline="") as fh:
                    w = csv.writer(fh)
                    if new:
                        w.writerow(["step", "win_rate", "wins", "losses", "ties", "seconds"])
                    w.writerow([step, f"{r['win_rate']:.4f}", r["wins"], r["losses"], r["ties"], f"{r['seconds']:.1f}"])
                print(f"[bench] {run_dir} step={step} win={r['win_rate']:.3f} ({r['wins']}/{r['losses']}/{r['ties']}) {r['seconds']:.0f}s", flush=True)
                did = True
        if args.once and not did:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
