"""Evaluate trainer-seat policies in the simulator on ROM trainer matchups.

    python -m tools.eval_trainer --checkpoint checkpoints/pep/run/model.pt --battles 400

Reports win rates for the checkpoint, the search teacher and a uniform-random
trainer seat against the same sequence of matchups and player-seat policies,
so numbers are directly comparable (same seeds, same parties).
"""
from __future__ import annotations

import argparse
import random
import time

from sim import trainers
from sim.matchups import parse_mix, parties_for, sample_matchup
from sim.trainer_env import TrainerEnv
from sim.trainer_search import TrainerTeacher


def play(env: TrainerEnv, trainer_policy, opponent, max_decisions: int = 300) -> bool | None:
    n = 0
    while not env.done() and n < max_decisions:
        if env.request_kind() is None:
            env.auto_step(opponent(env)); continue
        env.step(trainer_policy(env), opponent(env)); n += 1
    return env.winner_is_trainer()


def _make_player_seat(kind: str, opp_teacher, opp_rng, seed: int):
    """Player-seat opponent for evaluation.

    greedy        1-ply engine choice (the historical default; weak)
    random        uniform legal
    search[:d]    the depth-limited search teacher on the player side (default depth 2)
    net:<ckpt>    a PEP checkpoint on the player side -- this is how you run
                  checkpoint-vs-checkpoint head-to-heads
    """
    if kind == "random":
        return lambda env: opp_rng.choice(env.player_choices())
    if kind == "greedy":
        return lambda env: opp_teacher._greedy_player(env)
    if kind == "search" or kind.startswith("search:"):
        from sim.player_seat import SearchPlayer

        return SearchPlayer(depth=int(kind.partition(":")[2] or 2), seed=seed + 13).choose
    if kind.startswith("net:"):
        from models.pep import load_checkpoint
        from sim.player_seat import NetPlayer

        model, _ = load_checkpoint(kind[4:], map_location="cpu")
        return NetPlayer(model.eval(), seed=seed + 13, temperature=0.5).choose
    raise ValueError(f"unknown opponent {kind!r}")


def run(policies: dict, parties, battles: int, seed: int, opponent_kind: str = "greedy",
        matchups_mode: str = "random") -> dict[str, dict]:
    """`matchups_mode`: random (any two parties), balanced (strongest levels within 2), mirror (same party), or a mix."""
    rng = random.Random(seed)
    mix = parse_mix(matchups_mode)
    parties, ou_range = parties_for(mix, parties, seed)
    matchups = []
    for _ in range(battles):
        if matchups_mode == "random":   # legacy sampling order, keeps older numbers reproducible
            t, o = rng.choice(parties), rng.choice(parties)
        else:
            ti, oi, _mode = sample_matchup(rng, parties, mix, ou_range=ou_range)
            t, o = parties[ti], parties[oi]
        matchups.append((t, o, rng.getrandbits(62), rng.getrandbits(30)))
    results = {}
    for name, policy in policies.items():
        wins = losses = ties = 0; t0 = time.time()
        opp_teacher = TrainerTeacher(depth=1, rolls=1, seed=seed + 7)
        opp_rng = random.Random(seed + 11)
        opponent = _make_player_seat(opponent_kind, opp_teacher, opp_rng, seed)
        for t, o, bseed, rseed in matchups:
            env = TrainerEnv(t.to_specs(), t.class_id, o.to_specs(), seed=bseed, rng_seed=rseed)
            if hasattr(policy, "reset"):
                policy.reset()
            w = play(env, policy.choose if hasattr(policy, "choose") else policy, opponent)
            wins += w is True; losses += w is False; ties += w is None
        results[name] = {"wins": wins, "losses": losses, "ties": ties, "win_rate": wins / battles, "seconds": time.time() - t0}
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--checkpoint", default=None)
    ap.add_argument("--battles", type=int, default=200)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--temperature", type=float, default=0.5)
    ap.add_argument("--opponent", default="greedy",
                    help="greedy | random | search[:depth] | net:<ckpt>")
    ap.add_argument("--matchups", default="random", help="random | balanced | mirror | mix such as mirror:0.5,balanced:0.5")
    ap.add_argument("--no-teacher", action="store_true")
    ap.add_argument("--int-weights", default=None, help="pkai.weights: add an `int` policy run through the C++ integer model")
    args = ap.parse_args(argv)
    parties = trainers.load().parties
    rng = random.Random(args.seed + 3)
    policies: dict = {"random": lambda env: rng.choice(env.legal_actions())}
    if not args.no_teacher:
        policies["teacher_d1"] = TrainerTeacher(depth=1, rolls=2, seed=args.seed + 5)
    if args.checkpoint:
        from serve.pep_agent import PEPAgent
        policies["pep"] = PEPAgent(args.checkpoint, temperature=args.temperature, seed=args.seed)
    if args.int_weights:
        from serve.int_agent import IntAgent
        policies["int"] = IntAgent(args.int_weights, seed=args.seed)
    res = run(policies, parties, args.battles, args.seed, args.opponent, args.matchups)
    for name, r in res.items():
        print(f"{name:12s} win {r['win_rate']:.3f}  ({r['wins']}/{r['losses']}/{r['ties']} w/l/t)  {r['seconds']:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
