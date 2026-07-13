import json
from pathlib import Path

import pytest

from data.convert_metamon import convert_trajectory
from data.convert_showdown import RejectedReplay

FIXTURE = Path(__file__).parent / "fixtures" / "metamon_gen1ou_sample.json.lz4"
NAME = "gen1ou/2023/11/smogtours-gen1ou-732991_Unrated_grudge48147_vs_innerfocus97582_11-29-2023_WIN.json.lz4"


def test_convert_yields_rows():
    rows = convert_trajectory(NAME, FIXTURE.read_bytes(), source="metamon")
    assert len(rows) > 20
    for row in rows:
        assert 0 <= row["action"] <= 8
        state = json.loads(row["state_json"])
        assert state["schema_v"] == 1
        assert state["my_side"]["pokemon"][0]["species"]
        assert row["won"] is True  # filename says WIN
        if row["action"] <= 3:
            me = state["my_side"]["pokemon"][0]
            assert row["action_detail"] == me["moves"][row["action"]]


def test_forced_decisions_skipped():
    rows = convert_trajectory(NAME, FIXTURE.read_bytes(), source="metamon")
    # fixture has -1 actions; they must not appear
    turns = [r["turn"] for r in rows]
    assert len(turns) == len(set(turns)) and len(rows) < 51


def test_garbage_rejected():
    with pytest.raises(RejectedReplay):
        convert_trajectory(NAME, b"not lz4", source="metamon")
    with pytest.raises(RejectedReplay):
        convert_trajectory("weird_name.json.lz4", FIXTURE.read_bytes(), source="metamon")
