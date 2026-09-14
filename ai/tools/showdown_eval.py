"""Evaluate the PEP checkpoint on a local Pokémon Showdown server against poke-env baselines.

    scripts/setup_external.sh                       # once: Showdown + Metamon under ai/external
    scripts/showdown_server.sh                      # local server on localhost:8000
    uv run python -m tools.showdown_eval --checkpoint checkpoints/pep/current/model-fp32.pt --battles 100

Opponents: random (RandomPlayer), maxbp (MaxBasePowerPlayer), heuristic (SimpleHeuristicsPlayer).
Both sides draw teams from `--team-dir` (default: $METAMON_TEAM_DIR when set, else the built-in
RBY OU standard sets). `--accept USER` instead waits for challenges from an external agent
(e.g. Metamon's evaluator, see scripts/metamon_h2h.sh) under a fixed username.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

from serve.showdown_pep import make_player, make_teambuilder


async def run(args) -> int:
    from poke_env.player import MaxBasePowerPlayer, RandomPlayer, SimpleHeuristicsPlayer
    from poke_env.ps_client.account_configuration import AccountConfiguration
    from poke_env.ps_client.server_configuration import LocalhostServerConfiguration

    stamp = int(time.time()) % 100000
    kw = dict(server_configuration=LocalhostServerConfiguration, start_timer_on_battle_start=True,
              max_concurrent_battles=args.concurrency)
    pep = make_player(args.checkpoint, battle_format=args.format, temperature=args.temperature, seed=args.seed,
                      team_dir=args.team_dir, account_configuration=AccountConfiguration(args.username or f"pep{stamp}", None), **kw)
    if args.accept:
        print(f"[accept] waiting for {args.battles} challenges as {pep.username}", file=sys.stderr, flush=True)
        await pep.accept_challenges(args.accept, args.battles)
        print(f"[result] vs {args.accept}: win {pep.win_rate:.3f} ({pep.n_won_battles}/{pep.n_finished_battles})")
        return 0
    kinds = {"random": RandomPlayer, "maxbp": MaxBasePowerPlayer, "heuristic": SimpleHeuristicsPlayer}
    for name in args.opponents.split(","):
        opp = kinds[name](battle_format=args.format, team=make_teambuilder(args.seed + 100, args.team_dir),
                          account_configuration=AccountConfiguration(f"{name}{stamp}", None), **kw)
        pep.reset_battles()
        t0 = time.time()
        await pep.battle_against(opp, n_battles=args.battles)
        print(f"[result] vs {name:9s} win {pep.win_rate:.3f} ({pep.n_won_battles}/{pep.n_lost_battles}/{pep.n_tied_battles} w/l/t)"
              f" {time.time() - t0:.0f}s", flush=True)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--checkpoint", required=True)
    ap.add_argument("--battles", type=int, default=100)
    ap.add_argument("--opponents", default="random,maxbp,heuristic")
    ap.add_argument("--format", default="gen1ou")
    ap.add_argument("--temperature", type=float, default=0.5)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--username", default=None)
    ap.add_argument("--team-dir", default=os.environ.get("METAMON_TEAM_DIR") or None,
                    help="directory of Showdown team exports to draw from (both sides); default $METAMON_TEAM_DIR")
    ap.add_argument("--accept", default=None, help="accept challenges from this username instead of running baselines")
    args = ap.parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
