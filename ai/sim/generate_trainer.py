"""Generate teacher-labelled trainer-seat decisions as parquet.

Each row is one decision of the enemy trainer seat: the device feature block
(raw pkai::Features bytes, built by the same C++ as the handheld), the legal
mask, the event vector feeding the recurrent memory, the search teacher's
16-way policy and the battle outcome. Matchups come from the ROM's own
trainer parties (sim.trainers) or from explicit specs.

    python -m sim.generate_trainer --battles 200 --out datasets/trainer/v1 --depth 1
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import pathlib
import random
import time

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from sim.pack import PokemonSpec
from sim.trainer_env import EVENT_DIM, TrainerEnv
from sim.trainer_search import TrainerTeacher

SCHEMA = pa.schema([
    ("battle_id", pa.string()),
    ("trainer_class", pa.int16()),
    ("party_index", pa.int16()),
    ("round", pa.int32()),
    ("seq", pa.int32()),
    ("request_kind", pa.int8()),
    ("legal", pa.uint16()),
    ("features", pa.binary()),
    ("event", pa.binary()),
    ("teacher_probs", pa.list_(pa.float32(), 16)),
    ("teacher_value", pa.float32()),
    ("action", pa.int8()),
    ("won", pa.bool_()),
])


def play_battle(env: TrainerEnv, teacher: TrainerTeacher, opponent, max_decisions: int = 300,
                temperature: float = 0.25, battle_id: str | None = None, party_index: int = 0) -> list[dict]:
    """Play one battle; `opponent(env) -> engine choice` decides for the player seat."""
    rows: list[dict] = []
    bid = battle_id or "t_" + hashlib.sha1(env.buf).hexdigest()[:24]
    seq = 0
    while not env.done() and seq < max_decisions:
        if env.request_kind() is None:
            oc = opponent(env)
            env.b.raw.update(*((0, oc) if env.me == 0 else (oc, 0)))
            env.b._track_reveals(); env._refill_pp(); env._observe_player()
            continue
        feats, mask, kind = env.features()
        probs, scores = teacher.policy(env, temperature)
        action = max(scores, key=lambda a: (scores[a], -a))
        rows.append({
            "battle_id": bid, "trainer_class": env.trainer_class, "party_index": party_index,
            "round": env.round, "seq": seq, "request_kind": kind, "legal": mask,
            "features": bytes(feats), "event": np.asarray(env.last_event, dtype=np.float32).tobytes(),
            "teacher_probs": probs, "teacher_value": -1.0, "action": action, "won": False,
        })
        env.step(action, opponent(env))
        seq += 1
    won = env.winner_is_trainer()
    for r in rows:
        r["won"] = bool(won)
        r["teacher_value"] = 1.0 if won else 0.0 if won is False else -1.0
    return rows


def greedy_opponent(teacher: TrainerTeacher):
    return lambda env: teacher._greedy_player(env)


def random_opponent(rng: random.Random):
    return lambda env: rng.choice(env.player_choices())


def write_rows(rows: list[dict], out_dir: pathlib.Path, part: int) -> pathlib.Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows, schema=SCHEMA)
    path = out_dir / f"part-{part:05d}.parquet"
    pq.write_table(table, path, compression="zstd")
    return path


def sample_matchup(rng: random.Random, parties, mode: str):
    """(trainer_party, opponent_specs, opponent_label)."""
    t = rng.choice(parties)
    if mode == "trainer-vs-trainer":
        o = rng.choice(parties)
        return t, o.to_specs(), f"c{o.class_id}p{o.index}"
    raise ValueError(mode)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--battles", type=int, default=100)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--depth", type=int, default=1)
    ap.add_argument("--rolls", type=int, default=2)
    ap.add_argument("--alpha", type=float, default=0.3)
    ap.add_argument("--temperature", type=float, default=0.25)
    ap.add_argument("--mode", default="trainer-vs-trainer", choices=["trainer-vs-trainer"])
    ap.add_argument("--opponent", default="greedy", choices=["greedy", "random"])
    ap.add_argument("--rows-per-part", type=int, default=20000)
    ap.add_argument("--trainers", type=pathlib.Path, default=None, help="trainers.json (default: datasets/trainers.json)")
    args = ap.parse_args(argv)

    from sim.trainers import load_parties
    parties = load_parties(args.trainers)
    rng = random.Random(args.seed)
    teacher = TrainerTeacher(depth=args.depth, rolls=args.rolls, alpha=args.alpha, seed=args.seed)
    opponent = greedy_opponent(TrainerTeacher(depth=1, rolls=1, seed=args.seed + 1)) if args.opponent == "greedy" else random_opponent(rng)
    rows: list[dict] = []
    part = 0; total = 0; wins = 0; t0 = time.time()
    for i in range(args.battles):
        t, opp_specs, label = sample_matchup(rng, parties, args.mode)
        env = TrainerEnv(t.to_specs(), t.class_id, opp_specs, seed=rng.getrandbits(62), rng_seed=rng.getrandbits(30))
        teacher.reseed(rng.getrandbits(30))
        br = play_battle(env, teacher, opponent, temperature=args.temperature,
                         battle_id=f"t_{args.seed}_{i}_c{t.class_id}p{t.index}_{label}", party_index=t.index)
        rows += br; total += len(br); wins += bool(br and br[0]["won"])
        if len(rows) >= args.rows_per_part:
            write_rows(rows, args.out, part); part += 1; rows = []
        if (i + 1) % 50 == 0:
            print(f"{i + 1}/{args.battles} battles, {total} rows, trainer win-rate {wins / (i + 1):.2f}, {time.time() - t0:.0f}s", flush=True)
    if rows:
        write_rows(rows, args.out, part)
    print(f"done: {args.battles} battles, {total} rows, trainer win-rate {wins / max(1, args.battles):.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
