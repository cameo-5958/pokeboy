"""history_tail (SPECS §4.1 HIST): per-decision last-K-turn event summaries.

Entry format (compact, per completed turn, newest first):
  {"o": -1..-20, "my": "M:<move>"|"S:<species>"|"P"|None, "op": same,
   "dm": 0-8|None, "do": 0-8|None, "ev": [flags]}
dm/do are damage buckets suffered by me / by opponent (8 = KO).
"""

import json
from pathlib import Path

from data.convert_showdown import convert_replay

FIXTURE = Path(__file__).parent / "fixtures" / "replay_gen1ou_sample.json"


def rows():
    return convert_replay(json.loads(FIXTURE.read_text()), source="showdown")


def tail(row):
    return json.loads(row["state_json"])["history_tail"]


def test_history_tail_shape_and_offsets():
    for row in rows():
        t = tail(row)
        assert isinstance(t, list) and len(t) <= 20
        offsets = [e["o"] for e in t]
        assert offsets == sorted(offsets, reverse=True)  # newest (-1) first
        for e in t:
            assert -20 <= e["o"] <= -1
            assert set(e) >= {"o", "my", "op", "dm", "do", "ev"}
            for a in (e["my"], e["op"]):
                assert a is None or a == "P" or a[:2] in ("M:", "S:")
            for d in (e["dm"], e["do"]):
                assert d is None or 0 <= d <= 8
        # a decision on turn N can only see completed turns
        if row["turn"] <= 1:
            assert t == []


def test_history_tail_matches_own_previous_action():
    """If my previous decision was a move on turn N-1, the o=-1 entry must say so."""
    by_player = {1: {}, 2: {}}
    for r in rows():
        by_player[r["player"]].setdefault(r["turn"], r)  # first decision that turn
    checked = 0
    for player, by_turn in by_player.items():
        for turn, r in by_turn.items():
            prev = by_turn.get(turn - 1)
            if not prev or prev["action"] > 3 or prev["request_kind"] != "turn":
                continue
            entries = {e["o"]: e for e in tail(r)}
            if -1 in entries and entries[-1]["my"] is not None:
                assert entries[-1]["my"] == f"M:{prev['action_detail']}", (turn, player)
                checked += 1
    assert checked >= 5, f"only {checked} cross-checks exercised"


def test_history_tail_perspectives_mirror():
    """p1's 'my' action for a turn == p2's 'op' action for the same turn."""
    p1 = {r["turn"]: r for r in rows() if r["player"] == 1}
    p2 = {r["turn"]: r for r in rows() if r["player"] == 2}
    checked = 0
    for turn in set(p1) & set(p2):
        e1 = {e["o"]: e for e in tail(p1[turn])}
        e2 = {e["o"]: e for e in tail(p2[turn])}
        for o in set(e1) & set(e2):
            if e1[o]["my"] is not None or e2[o]["op"] is not None:
                assert e1[o]["my"] == e2[o]["op"], (turn, o)
                assert e1[o]["dm"] == e2[o]["do"], (turn, o)
                checked += 1
    assert checked >= 10


def test_history_tail_records_damage_and_faints():
    dm = [e for r in rows() for e in tail(r) if (e["do"] or 0) > 0]
    assert dm, "no damage ever recorded in history"
    kos = [e for r in rows() for e in tail(r) if e["dm"] == 8 or e["do"] == 8 or "ft" in e["ev"]]
    assert kos, "fixture battle has faints; history recorded none"


def _metamon_rows():
    from data.convert_metamon import convert_trajectory

    fx = Path(__file__).parent / "fixtures" / "metamon_gen1ou_sample.json.lz4"
    name = "smogtours-gen1ou-000001_1500_a_vs_b_2024_WIN.json.lz4"
    return convert_trajectory(name, fx.read_bytes(), source="metamon")


def test_metamon_tails_are_deep_and_schema_shaped():
    """Metamon trajectories are sequential - tails must accumulate up to 20
    turns with the same entry schema the showdown converter emits."""
    mrows = _metamon_rows()
    tails = [json.loads(r["state_json"])["history_tail"] for r in mrows]
    assert max(len(t) for t in tails) > 5, "tails never accumulate"
    for t in tails:
        assert len(t) <= 20
        assert [e["o"] for e in t] == [-(i + 1) for i in range(len(t))]
        for e in t:
            assert set(e) >= {"o", "my", "op", "dm", "do", "ev"}
            for a in (e["my"], e["op"]):
                assert a is None or a == "P" or a[:2] in ("M:", "S:")


def test_metamon_tail_matches_own_previous_action():
    """The o=-1 'my' entry must equal the previous row's ground-truth action."""
    mrows = _metamon_rows()
    checked = 0
    for prev, r in zip(mrows, mrows[1:]):
        if r["turn"] != prev["turn"] + 1:
            continue
        t = json.loads(r["state_json"])["history_tail"]
        if not t:
            continue
        e = t[0]
        expected = ("M:" if prev["action"] <= 3 else "S:") + prev["action_detail"]
        assert e["my"] == expected, (r["turn"], e["my"], expected)
        checked += 1
    assert checked >= 10, f"only {checked} checks"


def test_metamon_tails_carry_damage_and_events():
    mrows = _metamon_rows()
    entries = [e for r in mrows for e in json.loads(r["state_json"])["history_tail"]]
    assert any((e["dm"] or 0) > 0 for e in entries), "no damage-to-me ever recorded"
    assert any((e["do"] or 0) > 0 for e in entries), "no damage-to-opp ever recorded"
    assert any(e["dm"] == 8 or e["do"] == 8 for e in entries), "no KO bucket recorded"
    assert any(e["ev"] for e in entries), "no event flags ever recorded"
    assert any(e["op"] and e["op"].startswith("S:") for e in entries), (
        "opponent switches never recorded"
    )
