"""Showdown transport bridge (the SPECS transport adapter, v1 via poke-env).

Translates a poke-env Battle into a schema_v1 state + an action-int ->
poke-env order mapping, so any of our checkpoint agents can play on the
Showdown protocol — against metamon's pretrained agents, foul-play, or the
human ladder. Translation is duck-typed (attribute access only) and
poke-env itself is imported lazily inside PokeboyPlayer, so this module and
its tests work without the dependency installed.

History tails are left empty in v1: models are trained with tail
augmentation, so short/absent history is in-distribution (measured cost is
a few points — a live tail from poke-env observations is the known upgrade).

  uv run python -m serve.showdown --ckpt checkpoints/banquet/latest/model.pt \
      --mode challenge --opponent SomeUser         # or --mode ladder
"""

from __future__ import annotations

from typing import Any

from data.normalize import canon_move, canon_species

_TEAM_SIZE = 6


def _status_name(mon) -> str | None:
    status = getattr(mon, "status", None)
    if status is None:
        return None
    name = getattr(status, "name", str(status)).upper()
    return None if name == "FNT" else name


def _my_mon(mon) -> dict[str, Any]:
    moves = list(mon.moves.values())
    return {
        "species": canon_species(mon.base_species or mon.species),
        "hp_fraction": round(float(mon.current_hp_fraction), 4),
        "status": _status_name(mon),
        "fainted": bool(mon.fainted),
        "moves": [canon_move(m.id) for m in moves],
        "pp": [int(m.current_pp) for m in moves],
    }


def _opp_mon(mon) -> dict[str, Any]:
    return {
        "species": canon_species(mon.base_species or mon.species),
        "hp_fraction": round(float(mon.current_hp_fraction), 4),
        "status": _status_name(mon),
        "fainted": bool(mon.fainted),
        "revealed_moves": [canon_move(m.id) for m in mon.moves.values()],
    }


_UNREVEALED = {"species": None, "hp_fraction": None, "status": None,
               "fainted": False, "revealed_moves": []}


def state_from_battle(battle) -> tuple[dict[str, Any], dict[int, Any]]:
    """schema_v1 state dict + {action_int: poke-env order target}."""
    active = battle.active_pokemon
    bench = list(battle.available_switches)
    mine = [_my_mon(active)] + [_my_mon(m) for m in bench]

    opp_active = battle.opponent_active_pokemon
    opp_rest = [m for m in battle.opponent_team.values() if m is not opp_active]
    # opp active can be None (lead not yet revealed / mid-replacement)
    opp = [_opp_mon(opp_active) if opp_active is not None else dict(_UNREVEALED)]
    opp += [_opp_mon(m) for m in opp_rest]
    opp += [dict(_UNREVEALED)] * max(0, _TEAM_SIZE - len(opp))

    orders: dict[int, Any] = {}
    available_ids = {m.id: m for m in battle.available_moves}
    listed_ids = set()
    for k, move in enumerate(active.moves.values()):
        listed_ids.add(move.id)
        if move.id in available_ids:
            orders[k] = available_ids[move.id]
    for j, mon in enumerate(bench):
        orders[4 + j] = mon
    if not any(0 <= a <= 3 for a in orders) and battle.available_moves:
        # Struggle (or another engine-forced move outside the known moveset)
        forced = [m for m in battle.available_moves if m.id not in listed_ids]
        if forced:
            orders[9] = forced[0]

    state = {
        "schema_v": 1,
        "battle_id": getattr(battle, "battle_tag", "showdown"),
        "turn": int(battle.turn),
        "request_kind": "force_switch" if battle.force_switch else "turn",
        "my_side": {"active_ix": 0, "pokemon": mine},
        "opp_side": {"active_ix": 0, "pokemon": opp},
        "legal_actions": sorted(orders),
        "history_tail": [],
    }
    return state, orders


def team_to_showdown(specs) -> str:
    """PokemonSpec list -> Showdown paste (gen1: no items/abilities/EVs)."""
    blocks = []
    for spec in specs:
        lines = [spec.species] + [f"- {m}" for m in spec.moves]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def make_player(ckpt: str, battle_format: str = "gen1ou", team=None,
                temperature: float = 0.25, device: str | None = None,
                seed: int = 0, **player_kwargs):
    """Build a poke-env Player wrapping a checkpoint agent (lazy import)."""
    from poke_env.player import Player

    from models.agent import ModelAgent
    from sim.schema import State

    agent = ModelAgent(ckpt, seed=seed, device=device, temperature=temperature)

    class PokeboyPlayer(Player):
        def choose_move(self, battle):
            try:
                state_json, orders = state_from_battle(battle)
            except Exception:  # a stalled battle is worse than a random move
                self.logger.exception("state translation failed; playing random")
                return self.choose_random_move(battle)
            if not orders:
                return self.choose_random_move(battle)
            state = State(
                battle_id=state_json["battle_id"],
                turn=state_json["turn"],
                request_kind=state_json["request_kind"],
                my_side=state_json["my_side"],
                opp_side=state_json["opp_side"],
                legal_actions=state_json["legal_actions"],
                history_tail=[],
            )
            action = agent.choose(state)
            return self.create_order(orders[action])

    return PokeboyPlayer(battle_format=battle_format, team=team, **player_kwargs)


def main() -> None:
    import argparse
    import asyncio
    import random as _random

    p = argparse.ArgumentParser()
    p.add_argument("--ckpt", required=True)
    p.add_argument("--format", default="gen1ou")
    p.add_argument("--mode", choices=["challenge", "accept", "ladder", "local-smoke"],
                   default="local-smoke")
    p.add_argument("--opponent", help="username to challenge (mode=challenge)")
    p.add_argument("--battles", type=int, default=10)
    p.add_argument("--temperature", type=float, default=0.25)
    p.add_argument("--team-seed", type=int, default=0)
    args = p.parse_args()

    from sim.teamsets import TeamSampler

    team = team_to_showdown(TeamSampler().sample(_random.Random(args.team_seed)))

    async def run() -> None:
        player = make_player(args.ckpt, battle_format=args.format, team=team,
                             temperature=args.temperature)
        if args.mode == "local-smoke":
            from poke_env.player import RandomPlayer

            opp = RandomPlayer(battle_format=args.format, team=team_to_showdown(
                TeamSampler().sample(_random.Random(args.team_seed + 1))))
            await player.battle_against(opp, n_battles=args.battles)
            print({"wins": player.n_won_battles, "battles": player.n_finished_battles})
        elif args.mode == "ladder":
            await player.ladder(args.battles)
            print({"wins": player.n_won_battles, "battles": player.n_finished_battles})
        elif args.mode == "challenge":
            await player.send_challenges(args.opponent, n_challenges=args.battles)
        else:
            await player.accept_challenges(None, args.battles)

    asyncio.run(run())


if __name__ == "__main__":
    main()
