"""schema_v1 trajectory storage: one row per decision per player.

Actions in converted replay data use reveal-order semantics (documented in
convert_showdown.py) because true move-slot order is unobservable in replays.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pyarrow as pa

SCHEMA = pa.schema(
    [
        ("battle_id", pa.string()),
        ("source", pa.string()),
        ("elo", pa.int32()),
        ("mechanics_flags_hash", pa.string()),
        ("turn", pa.int32()),
        ("player", pa.int8()),
        ("state_json", pa.string()),
        ("action", pa.int8()),
        ("action_detail", pa.string()),  # move name or switch target species
        ("request_kind", pa.string()),
        ("won", pa.bool_()),
    ]
)


def mechanics_flags_hash() -> str:
    flags = Path(__file__).resolve().parents[1] / "sim" / "mechanics_flags.json"
    return hashlib.sha1(flags.read_bytes()).hexdigest()[:12]


def write_rows(rows: list[dict], dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    import pyarrow.parquet as pq

    table = pa.Table.from_pylist(rows, schema=SCHEMA)
    pq.write_table(table, dest)
    return dest


def dumps_state(state: dict) -> str:
    return json.dumps(state, separators=(",", ":"), sort_keys=True)
