"""Teacher corpus generator (SPECS §5.1 synthetic teacher, milestone 3).

Plays SearchTeacher games and writes decisions as schema_v1 parquet parts
byte-compatible with the replay converters (same SCHEMA via write_rows), so
the trainer consumes them with just `--sources teacher`.

Seat mix per battle (seeded, deterministic): mostly teacher-vs-teacher (both
sides recorded), a slice vs the scripted bots for opponent diversity (teacher
side recorded only). Battles hitting the turn cap (stall wars, ~5%) are
dropped. The recorded state is the PUBLIC view (b.state(player)) - the
teacher's full-information advantage never leaks into training rows.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

from sim.agents import MaxDamageBot, RandomBot
from sim.battle import run_battle
from sim.search import SearchTeacher
from sim.teams import sample_team

MAX_TURNS = 300
TEACHER_ELO = 2000  # marks demonstrator strength; scripted opponents unrated

_MIX = ("tvt", "tvt", "tvt", "tvt", "tvt", "tvt", "tvt", "tvt", "maxdamage", "random")


def _battle_rows(spec: dict[str, Any]) -> list[dict[str, Any]]:
    from data.trajectory import dumps_state, mechanics_flags_hash

    rng = random.Random(spec["seed"])
    kind = _MIX[rng.randrange(len(_MIX))]
    cfg = dict(depth=spec["depth"], rolls=spec["rolls"], alpha=spec["alpha"])
    p1 = SearchTeacher(seed=rng.randrange(2**31), **cfg)
    if kind == "tvt":
        p2: Any = SearchTeacher(seed=rng.randrange(2**31), **cfg)
    elif kind == "maxdamage":
        p2 = MaxDamageBot()
    else:
        p2 = RandomBot(rng.randrange(2**31))
    if spec.get("teams") == "mixed":
        from sim.teamsets import mixed_sample_team as pick_team
    else:
        pick_team = sample_team
    rec = run_battle(p1, p2, pick_team(rng), pick_team(rng),
                     seed=rng.randrange(2**63), max_turns=MAX_TURNS)
    if rec.winner not in ("p1", "p2"):
        return []  # stall war or tie: no clean outcome label
    flags = mechanics_flags_hash()
    battle_id = f"t_{spec['seed']:016x}"
    players = (1, 2) if kind == "tvt" else (1,)
    rows = []
    for player in players:
        won = rec.winner == f"p{player}"
        for t in rec.turns:
            state = t[f"state_p{player}"]
            rows.append(
                {
                    "battle_id": battle_id,
                    "source": "teacher",
                    "elo": TEACHER_ELO,
                    "mechanics_flags_hash": flags,
                    "turn": state.get("turn", 0),
                    "player": player,
                    "state_json": dumps_state(state),
                    "action": t[f"a{player}"],
                    "action_detail": "",
                    "request_kind": state.get("request_kind", "turn"),
                    "won": won,
                }
            )
    return rows


def generate_corpus(
    out: Path,
    battles: int,
    seed: int = 0,
    workers: int = 1,
    depth: int = 2,
    rolls: int = 2,
    alpha: float = 0.3,
    part_rows: int = 25_000,
    teams: str = "standard",
) -> dict[str, int]:
    from data.trajectory import write_rows

    if teams == "mixed":  # fail fast (in the parent) if the corpus is absent
        from sim.teamsets import TeamSampler

        TeamSampler()
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    specs = [
        {"seed": rng.randrange(2**63), "depth": depth, "rolls": rolls,
         "alpha": alpha, "teams": teams}
        for _ in range(battles)
    ]
    buffer: list[dict[str, Any]] = []
    part = kept = total_rows = 0

    def flush() -> None:
        nonlocal part, buffer, total_rows
        if buffer:
            write_rows(buffer, out / f"part-{part:05d}.parquet")
            total_rows += len(buffer)
            part += 1
            buffer = []

    if workers <= 1:
        results = map(_battle_rows, specs)
    else:
        import multiprocessing

        pool = multiprocessing.Pool(workers)
        results = pool.imap(_battle_rows, specs, chunksize=8)
    for rows in results:
        if rows:
            kept += 1
            buffer.extend(rows)
        if len(buffer) >= part_rows:
            flush()
    flush()
    if workers > 1:
        pool.close()
        pool.join()
    return {"battles": battles, "kept": kept, "rows": total_rows, "parts": part}
