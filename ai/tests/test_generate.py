"""Teacher corpus generator: schema_v1 parquet rows from SearchTeacher games."""

import json

import pytest

pq = pytest.importorskip("pyarrow.parquet")

from sim.generate import generate_corpus


def _rows(tmp_path, battles=3, seed=5):
    out = tmp_path / "teacher"
    stats = generate_corpus(out, battles=battles, seed=seed, workers=1,
                            depth=1, rolls=1, part_rows=200)
    parts = sorted(out.glob("part-*.parquet"))
    assert parts, "no parquet parts written"
    rows = [r for p in parts for r in pq.read_table(p).to_pylist()]
    return rows, stats


def test_generate_writes_schema_rows(tmp_path):
    rows, stats = _rows(tmp_path)
    assert stats["battles"] == 3 and stats["rows"] == len(rows) > 0
    required = {"battle_id", "source", "elo", "turn", "player", "state_json",
                "action", "request_kind", "won", "mechanics_flags_hash"}
    assert required <= set(rows[0])
    for r in rows[:50]:
        assert r["source"] == "teacher"
        assert 0 <= r["action"] <= 9
        state = json.loads(r["state_json"])
        assert state["schema_v"] == 1
        assert "history_tail" in state
        assert state["request_kind"] == r["request_kind"]


def test_generate_labels_both_sides_consistently(tmp_path):
    rows, _ = _rows(tmp_path)
    by_battle = {}
    for r in rows:
        by_battle.setdefault(r["battle_id"], set()).add((r["player"], r["won"]))
    for battle_id, sides in by_battle.items():
        players = {p for p, _ in sides}
        assert players <= {1, 2}
        if len(players) == 2:  # teacher-vs-teacher: exactly one side won
            wins = {w for _, w in sides}
            assert wins == {True, False}, battle_id


def test_generate_is_deterministic_per_seed(tmp_path):
    r1, _ = _rows(tmp_path / "a", battles=2, seed=9)
    r2, _ = _rows(tmp_path / "b", battles=2, seed=9)
    assert [(x["battle_id"], x["action"]) for x in r1] == [
        (x["battle_id"], x["action"]) for x in r2
    ]
