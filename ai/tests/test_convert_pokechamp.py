import json
from pathlib import Path

import pytest

from data.convert_pokechamp import _elo_floor, _players, convert_row

DATA_DIR = Path(__file__).parents[1] / "datasets" / "raw" / "pokechamp" / "milkkarten__pokechamp" / "data"


def test_elo_floor():
    assert _elo_floor("1000-1199") == 1000
    assert _elo_floor(None) == 0
    assert _elo_floor("1800+") == 1800


def test_players_parsed_from_log():
    log = "|player|p1|alice|av|\n|player|p2|bob|av|\n"
    assert _players(log) == ["alice", "bob"]


@pytest.mark.skipif(not DATA_DIR.exists(), reason="pokechamp corpus not downloaded")
def test_convert_real_gen1_row():
    from data.convert_pokechamp import iter_gen1_rows

    row = next(iter_gen1_rows(DATA_DIR))
    rows = convert_row(row)
    assert rows
    state = json.loads(rows[0]["state_json"])
    assert state["schema_v"] == 1
    assert all(0 <= r["action"] <= 9 for r in rows)
