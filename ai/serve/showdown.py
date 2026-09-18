"""Showdown transport bridge (the SPECS transport adapter, v1 via poke-env).

Translates a poke-env Battle into a schema_v1 state + an action-int ->
poke-env order mapping, so any of our checkpoint agents can play on the
Showdown protocol - against metamon's pretrained agents, foul-play, or the
human ladder. Translation is duck-typed (attribute access only) and
poke-env itself is imported lazily inside PokeboyPlayer, so this module and
its tests work without the dependency installed.

History tails are rebuilt live by HistoryTracker from battle diffs in the
training format (measured neutral vs TaurosV0, kept on for distribution
fidelity); --no-history plays with empty tails for A/B controls.

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

_HIST_K = 20


def _dmg_bucket(frac: float) -> int:
    # same buckets as sim/battle.py and data/convert_showdown.py (SPECS §4.1)
    if frac <= 0:
        return 0
    return min(7, 1 + int(min(frac, 0.6999) * 10))


class HistoryTracker:
    """Rebuilds the sim's per-turn history tail from poke-env battle diffs.

    Call observe(battle) at every decision point before reading the state,
    and record_decision(battle, target) with the chosen order target after.
    Semantics mirror sim/battle.py's recorder (damage summed per side and
    bucketed, KO forces bucket 8, first action of a turn wins) so live
    tails match the training distribution. The one inference: the
    opponent's action is taken from their active mon's last_move when they
    did not switch, which mislabels the rare fully-blocked turn but keeps
    the common repeated-move case attributed.
    """

    def __init__(self):
        self._snap: dict | None = None
        self._turns: dict[int, dict] = {}

    @staticmethod
    def _snapshot(battle) -> dict:
        def mon_row(mon):
            return (float(mon.current_hp_fraction or 0.0), _status_name(mon),
                    bool(mon.fainted))

        opp_active = battle.opponent_active_pokemon
        my_active = battle.active_pokemon
        return {
            "team": {m.species: mon_row(m) for m in battle.team.values()},
            "opp": {m.species: mon_row(m) for m in battle.opponent_team.values()},
            "my_active": None if my_active is None else my_active.species,
            "opp_active": None if opp_active is None else opp_active.species,
            "opp_last_move": (
                None if opp_active is None
                or getattr(opp_active, "last_move", None) is None
                else opp_active.last_move.id
            ),
        }

    def _bucket(self, battle) -> dict:
        turn = int(battle.turn) - (0 if battle.force_switch else 1)
        return self._turns.setdefault(turn, {
            "my": None, "op": None, "dmg_me": 0.0, "dmg_opp": 0.0,
            "ko_me": False, "ko_opp": False, "ev": set(),
        })

    @staticmethod
    def _side_diff(pre: dict, post: dict, bucket: dict, dmg_key: str,
                   ko_key: str) -> None:
        for species, (hp0, status0, fainted0) in pre.items():
            hp1, status1, fainted1 = post.get(species, (0.0, status0, True))
            bucket[dmg_key] += max(0.0, hp0 - hp1)
            if not fainted0 and (fainted1 or (hp0 > 0 and hp1 <= 0)):
                bucket[ko_key] = True
                bucket["ev"].add("ft")
            if status0 is None and status1 is not None:
                bucket["ev"].add("st")

    def observe(self, battle) -> None:
        from data.convert_metamon import _eff_events

        pre, cur = self._snap, self._snapshot(battle)
        self._snap = cur
        if pre is None:
            return
        bucket = self._bucket(battle)
        self._side_diff(pre["team"], cur["team"], bucket, "dmg_me", "ko_me")
        self._side_diff(pre["opp"], cur["opp"], bucket, "dmg_opp", "ko_opp")
        if bucket["op"] is None and pre["opp_active"] is not None:
            if cur["opp_active"] != pre["opp_active"]:
                bucket["op"] = f"S:{canon_species(cur['opp_active'])}"
            elif cur["opp_last_move"] is not None:
                move = canon_move(cur["opp_last_move"])
                bucket["op"] = f"M:{move}"
                _eff_events(bucket["ev"], move,
                            pre["my_active"] and canon_species(pre["my_active"]))

    def record_decision(self, battle, target) -> None:
        from data.convert_metamon import _eff_events

        bucket = self._turns.setdefault(int(battle.turn), {
            "my": None, "op": None, "dmg_me": 0.0, "dmg_opp": 0.0,
            "ko_me": False, "ko_opp": False, "ev": set(),
        })
        if bucket["my"] is not None:
            return  # first action of the turn wins
        if hasattr(target, "species"):
            bucket["my"] = f"S:{canon_species(target.species)}"
        else:
            move = canon_move(target.id)
            bucket["my"] = f"M:{move}"
            opp = (self._snap or {}).get("opp_active")
            _eff_events(bucket["ev"], move, opp and canon_species(opp))

    def tail(self, battle) -> list[dict[str, Any]]:
        done = sorted((t for t in self._turns if t < int(battle.turn)),
                      reverse=True)
        out = []
        for i, t in enumerate(done[:_HIST_K]):
            b = self._turns[t]
            out.append({
                "o": -(i + 1),
                "my": b["my"],
                "op": b["op"],
                "dm": 8 if b["ko_me"] else _dmg_bucket(b["dmg_me"]),
                "do": 8 if b["ko_opp"] else _dmg_bucket(b["dmg_opp"]),
                "ev": sorted(b["ev"]),
            })
        return out


def state_from_battle(battle, history_tail=()) -> tuple[dict[str, Any], dict[int, Any]]:
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
        "history_tail": list(history_tail),
    }
    return state, orders


def team_to_showdown(specs) -> str:
    """PokemonSpec list -> Showdown paste (gen1: no items/abilities/EVs)."""
    blocks = []
    for spec in specs:
        lines = [spec.species] + [f"- {m}" for m in spec.moves]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def make_team_builder(seed: int = 0, pool: str = "mixed",
                      team_index: int | None = None):
    """Teambuilder sampling a fresh team every battle (lazy import).

    pool: "mixed" for the standard 20/40/40 benchmark distribution, or a
    named TeamSampler pool ("competitive", "variety") to draw from alone -
    e.g. matching an external opponent's curated team set. team_index pins
    one fixed team from that pool (per-team win-rate probes).
    """
    import random as _random

    from poke_env.teambuilder import Teambuilder

    from sim.teamsets import TeamSampler

    class MixedTeamBuilder(Teambuilder):
        def __init__(self):
            self._rng = _random.Random(seed)
            self._sampler = TeamSampler()

        def yield_team(self) -> str:
            if team_index is not None:
                team = self._sampler.pools[pool][team_index]
            elif pool == "mixed":
                team = self._sampler.sample(self._rng)
            else:
                team = self._rng.choice(self._sampler.pools[pool])
            paste = team_to_showdown(team)
            return self.join_team(self.parse_showdown_team(paste))

    return MixedTeamBuilder()


def make_player(ckpt: str, battle_format: str = "gen1ou", team=None,
                temperature: float = 0.25, device: str | None = None,
                seed: int = 0, agent=None, history: bool = True,
                **player_kwargs):
    """Build a poke-env Player wrapping a checkpoint agent (lazy import).

    agent overrides the default ModelAgent - e.g. serve.overdrive's
    OverdriveAgent for search-at-serve play."""
    from poke_env.player import Player

    from models.agent import ModelAgent
    from sim.schema import State

    if agent is None:
        agent = ModelAgent(ckpt, seed=seed, device=device, temperature=temperature)

    class PokeboyPlayer(Player):
        _trackers: dict[str, HistoryTracker] = {}

        def choose_move(self, battle):
            try:
                tag = getattr(battle, "battle_tag", "showdown")
                if len(self._trackers) > 64:  # finished battles never return
                    self._trackers.clear()
                tracker = self._trackers.setdefault(tag, HistoryTracker())
                tracker.observe(battle)
                tail = tracker.tail(battle) if history else []
                state_json, orders = state_from_battle(battle, history_tail=tail)
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
                history_tail=state_json["history_tail"],
            )
            action = agent.choose(state)
            try:
                tracker.record_decision(battle, orders[action])
            except Exception:
                self.logger.exception("history record failed")
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
    p.add_argument("--team-pool", default="mixed",
                   choices=["mixed", "competitive", "variety"])
    p.add_argument("--username", help="account name on the server")
    p.add_argument("--overdrive", action="store_true",
                   help="serve-time search over determinized engine clones")
    p.add_argument("--no-history", action="store_true",
                   help="diagnostic: play with empty history tails")
    p.add_argument("--team-index", type=int, default=None,
                   help="pin one fixed team from --team-pool")
    p.add_argument("--determinizations", type=int, default=4)
    p.add_argument("--search-depth", type=int, default=2)
    p.add_argument("--search-mode", default="teacher",
                   choices=["teacher", "value"])
    args = p.parse_args()

    async def run() -> None:
        # without the timer, an opponent that crashes mid-battle leaves the
        # battle open forever and poke-env queues all further challenges
        # behind it (max 1 concurrent battle)
        kwargs = {"start_timer_on_battle_start": True}
        if args.username:
            from poke_env.ps_client.account_configuration import (
                AccountConfiguration,
            )

            kwargs["account_configuration"] = AccountConfiguration(
                args.username, None)
        agent = None
        if args.overdrive:
            from serve.overdrive import OverdriveAgent

            agent = OverdriveAgent(args.ckpt, temperature=args.temperature,
                                   determinizations=args.determinizations,
                                   depth=args.search_depth,
                                   mode=args.search_mode)
        player = make_player(args.ckpt, battle_format=args.format,
                             team=make_team_builder(args.team_seed,
                                                    args.team_pool,
                                                    args.team_index),
                             temperature=args.temperature, agent=agent,
                             history=not args.no_history, **kwargs)
        if args.mode == "local-smoke":
            from poke_env.player import RandomPlayer

            opp = RandomPlayer(battle_format=args.format,
                               team=make_team_builder(args.team_seed + 1,
                                                      args.team_pool))
            await player.battle_against(opp, n_battles=args.battles)
        elif args.mode == "ladder":
            await player.ladder(args.battles)
        elif args.mode == "challenge":
            await player.send_challenges(args.opponent, n_challenges=args.battles)
        else:
            await player.accept_challenges(None, args.battles)
        print({"wins": player.n_won_battles, "battles": player.n_finished_battles})

    asyncio.run(run())


if __name__ == "__main__":
    main()
