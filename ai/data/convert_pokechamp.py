"""Convert the PokéChamp dataset's gen1 slice to schema_v1 rows.

PokéChamp (milkkarten/pokechamp, verified 2026-07-12) ships parquet shards
with columns text (raw, lowercased Showdown log), month_year, gamemode, elo
(bucket string like "1000-1199"), battle_id. Gen1 rows reuse the showdown
log parser; player names are parsed from |player| lines.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

from data.convert_showdown import RejectedReplay, convert_replay

GEN1_MODES = ("gen1ou", "gen1uu", "gen1nu", "gen1ubers", "gen1randombattle")

_PLAYER = re.compile(r"^\|player\|(p[12])\|([^|]*)\|", re.MULTILINE)


def _players(log: str) -> list[str]:
    names = {m.group(1): m.group(2) for m in _PLAYER.finditer(log)}
    return [names.get("p1", ""), names.get("p2", "")]


def _elo_floor(bucket: str | None) -> int:
    if not bucket:
        return 0
    m = re.match(r"(\d+)", str(bucket))
    return int(m.group(1)) if m else 0


def convert_row(row: dict, source: str = "pokechamp") -> list[dict]:
    log = row.get("text") or ""
    replay = {
        "id": f"pokechamp-{row.get('gamemode')}-{row.get('battle_id')}",
        "rating": _elo_floor(row.get("elo")),
        "players": _players(log),
        "log": log,
    }
    return convert_replay(replay, source=source)


def iter_gen1_rows(data_dir: Path) -> Iterator[dict]:
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    for path in sorted(Path(data_dir).glob("*.parquet")):
        table = pq.read_table(path)
        mask = pc.is_in(table["gamemode"], value_set=__import__("pyarrow").array(GEN1_MODES))
        for row in table.filter(mask).to_pylist():
            yield row
