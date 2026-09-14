"""Cross-table of trainer-seat policies x player-seat opponents on fixed matchup sets.

    python -m tools.cross_eval --battles 600 --seed 7 --matchups mirror,balanced --workers 14 --out logs/cross.md

Rows (trainer seat): random, native (the cartridge's own AI), maxdmg, teacher_d1, teacher_d2,
ppo_v1, ppo_v4, ppo_v4_int. Columns (player seat): random, greedy (1-ply engine leaf value),
teacher_d1 (search through the swapped view), ppo_v4 (the network in the player seat).
Every cell plays the same matchup list (same parties, seeds) so numbers are comparable.
"""
from __future__ import annotations

import argparse
import multiprocessing as mp
import random
import sys
import time

ROWS = ("random", "native", "maxdmg", "teacher_d1", "teacher_d2", "ppo_v1", "ppo_v4", "ppo_v4_int", "ppo_v6")
COLS = ("random", "greedy", "teacher_d1", "ppo_v4", "ppo_v6")
CKPT_V1 = "checkpoints/pep/current-ppo-v1/model-fp32.pt"
CKPT_V4 = "checkpoints/pep/current/model-fp32.pt"
WEIGHTS_V4 = "checkpoints/pep/current/pkai.weights"
CKPT_V6 = "checkpoints/pep/ppo-v6/model.pt"

# token indices in pkai::Features: field 0, own 1-6, player 7-12, own moves 13-16
_OWN, _MOVES = 1, 13


def make_row(name: str, seed: int):
    from sim.trainer_search import TrainerTeacher
    if name == "random":
        rng = random.Random(seed)
        return lambda env: rng.choice(env.legal_actions())
    if name == "native":
        from sim.native_ai import NativeTrainerAI
        return NativeTrainerAI(seed=seed)
    if name == "maxdmg":
        from models.pep_data import decode_features

        def maxdmg(env) -> int:
            feats, mask, kind = env.features()
            f = decode_features(bytes(feats))["f"]
            if kind == 1:
                cands = [(f[_OWN + i, 38], -i) for i in range(6) if mask >> (4 + i) & 1]
                return 4 - max(cands)[1] if cands else env.legal_actions()[0]
            cands = [(f[_MOVES + i, 5], -i) for i in range(4) if mask >> i & 1]
            return -max(cands)[1] if cands else env.legal_actions()[0]
        return maxdmg
    if name == "teacher_d1":
        return TrainerTeacher(depth=1, rolls=2, seed=seed)
    if name == "teacher_d2":
        return TrainerTeacher(depth=2, rolls=2, seed=seed)
    if name in ("ppo_v1", "ppo_v4", "ppo_v6"):
        from serve.pep_agent import PEPAgent
        ckpt = {"ppo_v1": CKPT_V1, "ppo_v4": CKPT_V4, "ppo_v6": CKPT_V6}[name]
        return PEPAgent(ckpt, temperature=0.5, seed=seed)
    if name == "ppo_v4_int":
        from serve.int_agent import IntAgent
        return IntAgent(WEIGHTS_V4, seed=seed)
    raise ValueError(name)


class _ViewOpponent:
    """Run a trainer-seat policy in the player seat through sim.player_seat.player_view."""

    def __init__(self, policy):
        self.policy = policy
        self._key = None
        self._view = None

    def __call__(self, env) -> int:
        from sim.player_seat import player_view
        if env.b.battle_id != self._key or self._view is None:
            self._key = env.b.battle_id; self._view = player_view(env)
            if hasattr(self.policy, "reset"):
                self.policy.reset()
        v = self._view
        v.round = env.round; v._observe_player()
        if v.request_kind() is None:
            return v.auto_choice()
        a = self.policy.choose(v)
        c = v._engine_choice(a) if a < 10 else None
        return v.auto_choice() if c is None else c


def make_col(name: str, seed: int):
    from sim.trainer_search import TrainerTeacher
    if name == "random":
        rng = random.Random(seed)
        return lambda env: rng.choice(env.player_choices())
    if name == "greedy":
        t = TrainerTeacher(depth=1, rolls=1, seed=seed)
        return lambda env: t._greedy_player(env)
    if name == "teacher_d1":
        return _ViewOpponent(TrainerTeacher(depth=1, rolls=2, seed=seed))
    if name in ("ppo_v4", "ppo_v6"):
        from models.pep import load_checkpoint
        from sim.player_seat import NetPlayer
        model, _ = load_checkpoint(CKPT_V4 if name == "ppo_v4" else CKPT_V6, map_location="cpu")
        return NetPlayer(model.eval(), seed=seed, temperature=0.5).choose
    raise ValueError(name)


def matchup_list(parties, ou_range, mode: str, n: int, seed: int):
    from sim.matchups import parse_mix, sample_matchup
    rng = random.Random(seed); mix = parse_mix(mode); out = []
    for _ in range(n):
        t, o, _m = sample_matchup(rng, parties, mix, ou_range=ou_range)
        out.append((t, o, rng.getrandbits(62), rng.getrandbits(30)))
    return out


def run_cell(job):
    row, col, mode, n, seed = job
    import torch
    torch.set_num_threads(1)
    from sim import trainers
    from sim.trainer_env import TrainerEnv
    from tools.eval_trainer import play
    from sim.matchups import parse_mix, parties_for
    parties, ou_range = parties_for(parse_mix(mode), trainers.load().parties, seed)
    pol = make_row(row, seed + 11); opp = make_col(col, seed + 23)
    t0 = time.time(); w = l = t = 0
    for ti, oi, bs, rs in matchup_list(parties, ou_range, mode, n, seed):
        tp, op = parties[ti], parties[oi]
        env = TrainerEnv(tp.to_specs(), tp.class_id, op.to_specs(), seed=bs, rng_seed=rs)
        if hasattr(pol, "reset"):
            pol.reset()
        r = play(env, pol.choose if hasattr(pol, "choose") else pol, opp)
        w += r is True; l += r is False; t += r is None
    return row, col, mode, w, l, t, time.time() - t0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--battles", type=int, default=600)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--matchups", default="mirror,balanced")
    ap.add_argument("--rows", default=",".join(ROWS))
    ap.add_argument("--cols", default=",".join(COLS))
    ap.add_argument("--workers", type=int, default=14)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    rows = args.rows.split(","); cols = args.cols.split(","); modes = args.matchups.split(",")
    jobs = [(r, c, m, args.battles, args.seed) for m in modes for r in rows for c in cols]
    # slowest first
    jobs.sort(key=lambda j: (j[0] != "teacher_d2", j[1] != "ppo_v4", j[1] != "teacher_d1"))
    results = {}
    with mp.get_context("spawn").Pool(args.workers) as pool:
        for row, col, mode, w, l, t, secs in pool.imap_unordered(run_cell, jobs):
            results[(mode, row, col)] = (w, l, t)
            print(f"[cell] {mode:9s} {row:11s} vs {col:11s} win {w / args.battles:.3f} ({w}/{l}/{t}) {secs:.0f}s",
                  file=sys.stderr, flush=True)
    lines = []
    for m in modes:
        lines.append(f"\n### {m} matchups, {args.battles} battles (seed {args.seed}), trainer-seat win rate\n")
        lines.append("| trainer seat \\ player seat | " + " | ".join(cols) + " |")
        lines.append("|---|" + "---|" * len(cols))
        for r in rows:
            cells = []
            for c in cols:
                w, l, t = results[(m, r, c)]
                cells.append(f"{100 * w / args.battles:.1f} %")
            lines.append(f"| {r} | " + " | ".join(cells) + " |")
    text = "\n".join(lines) + "\n"
    print(text)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
