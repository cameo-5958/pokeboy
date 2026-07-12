import json
from pathlib import Path

import pytest

from data.convert_showdown import RejectedReplay, convert_replay
from data.trajectory import SCHEMA, write_rows

FIXTURE = Path(__file__).parent / "fixtures" / "replay_gen1ou_sample.json"


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text())


def test_convert_yields_decision_rows():
    rows = convert_replay(load_fixture(), source="showdown")
    assert len(rows) > 20
    for row in rows:
        assert 0 <= row["action"] <= 9
        state = json.loads(row["state_json"])
        assert state["schema_v"] == 1
        assert row["player"] in (1, 2)
    assert {r["player"] for r in rows} == {1, 2}


def test_winner_marked():
    replay = load_fixture()
    rows = convert_replay(replay, source="showdown")
    winners = {r["player"] for r in rows if r["won"]}
    losers = {r["player"] for r in rows if not r["won"]}
    assert len(winners) == 1 and len(losers) >= 1
    # fixture: TR0LLSTER (p1) won
    assert winners == {1}


def test_move_actions_match_reveal_order():
    rows = convert_replay(load_fixture(), source="showdown")
    for row in rows:
        if row["request_kind"] == "turn" and row["action"] <= 3:
            state = json.loads(row["state_json"])
            me = state["my_side"]["pokemon"][state["my_side"]["active_ix"]]
            moves = me["moves"]
            if row["action"] < len(moves):
                assert moves[row["action"]] == row["action_detail"]


def test_corrupt_log_rejected():
    replay = load_fixture()
    replay["log"] = replay["log"][:100]  # truncate mid-preamble
    with pytest.raises(RejectedReplay):
        convert_replay(replay, source="showdown")


def test_rows_write_as_parquet(tmp_path):
    rows = convert_replay(load_fixture(), source="showdown")
    out = write_rows(rows, tmp_path / "part-0000.parquet")
    import pyarrow.parquet as pq

    table = pq.read_table(out)
    assert table.schema.equals(SCHEMA)
    assert table.num_rows == len(rows)
