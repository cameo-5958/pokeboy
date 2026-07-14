"""Simulator CLI.

  python -m sim battle --p1 {random,maxdamage,cli,jsonl} --p2 ... --seed N [--battles K]
  python -m sim bench --seconds S

The jsonl agent speaks the seat protocol on stdio: one schema_v1 state JSON
line out, one {"action": int} line back — the contract a future transport
adapter (HTTP/WS/battle-link) will implement.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import random
import sys
import time

from sim.agents import HumanCLI, MaxDamageBot, RandomBot
from sim.battle import run_battle
from sim.engine import RESULT_NONE, RawBattle
from sim.pack import pack_battle
from sim.schema import State
from sim.teams import sample_team


class JsonlSeat:
    """Drives a seat over stdin/stdout JSON lines."""

    def choose(self, state: State) -> int:
        sys.stdout.write(json.dumps(state.to_json()) + "\n")
        sys.stdout.flush()
        return int(json.loads(sys.stdin.readline())["action"])


def make_agent(name: str, seed: int):
    if name == "random":
        return RandomBot(seed)
    if name == "maxdamage":
        return MaxDamageBot()
    if name == "cli":
        return HumanCLI()
    if name == "jsonl":
        return JsonlSeat()
    if name == "search" or name.startswith("search:"):
        from sim.search import SearchTeacher

        depth = int(name.partition(":")[2] or 1)
        return SearchTeacher(depth=depth, seed=seed)
    if name.startswith("model:"):
        from models.agent import ModelAgent  # keep sim torch-free for other seats

        return ModelAgent(name.removeprefix("model:"), seed=seed)
    raise SystemExit(f"unknown agent {name!r}")


def cmd_battle(args) -> None:
    rng = random.Random(args.seed)
    tally = {"p1_wins": 0, "p2_wins": 0, "ties": 0, "unfinished": 0, "turns": 0}
    cache: dict[str, object] = {}

    def get_agent(name: str, seed: int):
        # model seats keep the loaded checkpoint across battles; only the
        # sampling stream is reset per battle
        if not name.startswith("model:"):
            return make_agent(name, seed)
        if name in cache:
            cache[name].reseed(seed)
        else:
            cache[name] = make_agent(name, seed)
        return cache[name]

    for i in range(args.battles):
        a1 = get_agent(args.p1, rng.randrange(2**31))
        a2 = get_agent(args.p2, rng.randrange(2**31))
        t1, t2 = sample_team(rng), sample_team(rng)
        rec = run_battle(a1, a2, t1, t2, seed=rng.randrange(2**63))
        key = {"p1": "p1_wins", "p2": "p2_wins", "tie": "ties"}.get(rec.winner, "unfinished")
        tally[key] += 1
        tally["turns"] += len(rec.turns)
    print(json.dumps({"battles": args.battles, **tally}))


def cmd_generate(args) -> None:
    from pathlib import Path

    from sim.generate import generate_corpus

    stats = generate_corpus(
        Path(args.out),
        battles=args.battles,
        seed=args.seed,
        workers=args.workers or os.cpu_count() or 1,
        depth=args.depth,
        rolls=args.rolls,
        alpha=args.alpha,
    )
    print(json.dumps(stats))


def _bench_worker(worker_args) -> tuple[int, int]:
    seed, seconds = worker_args
    rng = random.Random(seed)
    decisions = battles = 0
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        t1, t2 = sample_team(rng), sample_team(rng)
        b = RawBattle(pack_battle(t1, t2, rng.randrange(2**63)))
        prng = random.Random(rng.randrange(2**31))
        c1 = c2 = 0
        while b.result_type() == RESULT_NONE:
            b.update(c1, c2)
            if b.result_type() != RESULT_NONE:
                break
            r1, r2 = b.requests()
            c1 = prng.choice(b.choices(0, r1))
            c2 = prng.choice(b.choices(1, r2))
            decisions += 2
        battles += 1
    return decisions, battles


def cmd_bench(args) -> None:
    n = args.workers or os.cpu_count() or 1
    t0 = time.monotonic()
    with multiprocessing.Pool(n) as pool:
        results = pool.map(_bench_worker, [(i, args.seconds) for i in range(n)])
    wall = time.monotonic() - t0
    decisions = sum(r[0] for r in results)
    battles = sum(r[1] for r in results)
    print(
        json.dumps(
            {
                "decisions_per_s": decisions / wall,
                "battles_per_s": battles / wall,
                "workers": n,
                "wall_s": round(wall, 2),
            }
        )
    )


def main() -> None:
    p = argparse.ArgumentParser(prog="sim")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("battle", help="run battles between two seat agents")
    b.add_argument("--p1", default="random")
    b.add_argument("--p2", default="random")
    b.add_argument("--seed", type=int, default=0)
    b.add_argument("--battles", type=int, default=1)
    b.set_defaults(fn=cmd_battle)

    e = sub.add_parser("bench", help="raw engine throughput benchmark")
    e.add_argument("--seconds", type=float, default=10.0)
    e.add_argument("--workers", type=int, default=0)
    e.set_defaults(fn=cmd_bench)

    g = sub.add_parser("generate", help="generate a SearchTeacher demonstration corpus")
    g.add_argument("--battles", type=int, required=True)
    g.add_argument("--out", default="datasets/processed/teacher")
    g.add_argument("--seed", type=int, default=0)
    g.add_argument("--workers", type=int, default=0, help="0 = all cores")
    g.add_argument("--depth", type=int, default=2)
    g.add_argument("--rolls", type=int, default=2)
    g.add_argument("--alpha", type=float, default=0.3)
    g.set_defaults(fn=cmd_generate)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
