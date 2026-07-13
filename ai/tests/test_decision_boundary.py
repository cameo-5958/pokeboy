"""Decision-boundary integrity (workspace/AI-DATA.md remediation):

Normal-turn rows must be snapshotted at the turn boundary — neither player's
input may contain same-turn effects or its own label. Forced replacements
legitimately decide mid-turn and keep execution-time state.
"""

import json
from pathlib import Path

from data.convert_showdown import convert_replay

FIXTURE = Path(__file__).parent / "fixtures" / "replay_gen1ou_sample.json"


def rows():
    return convert_replay(json.loads(FIXTURE.read_text()), source="showdown")


def state(r):
    return json.loads(r["state_json"])


def _true_first_use() -> dict[tuple[int, str], int]:
    """Ground truth from the raw log: first turn each side's move appears."""
    from data.convert_showdown import _ident_side
    from data.normalize import canon_move

    log = json.loads(FIXTURE.read_text())["log"]
    first: dict[tuple[int, str], int] = {}
    turn = 0
    for line in log.splitlines():
        parts = line.split("|")
        cmd = parts[1] if len(parts) > 1 else ""
        if cmd == "turn":
            turn = int(parts[2])
        elif cmd == "move":
            mv = canon_move(parts[3].strip())
            if mv:
                first.setdefault((_ident_side(parts[2]) + 1, mv), turn)
    return first


def test_no_row_is_a_same_turn_first_use():
    """First uses are unrecoverable (the label move can't be in a leak-free
    pre-decision state) and must be rejected, not emitted."""
    first = _true_first_use()
    move_rows = [r for r in rows() if r["action"] <= 3]
    assert move_rows, "rework must not drop all move rows"
    for r in move_rows:
        assert first[(r["player"], r["action_detail"])] < r["turn"], (
            f"turn {r['turn']}: first use of {r['action_detail']} was emitted"
        )
        s = state(r)
        me = s["my_side"]["pokemon"][s["my_side"]["active_ix"]]
        assert r["action"] < len(me["moves"])
        assert me["moves"][r["action"]] == r["action_detail"]
        # every listed move must also predate this turn — boundary purity
        for mv in me["moves"]:
            assert first.get((r["player"], mv), 0) < r["turn"]


def test_switch_target_always_listed_at_label_index():
    for r in rows():
        if 4 <= r["action"] <= 8:
            s = state(r)
            mons = s["my_side"]["pokemon"]
            ix = s["my_side"]["active_ix"]
            bench = [m["species"] for i, m in enumerate(mons) if i != ix]
            j = r["action"] - 4
            assert j < len(bench), (r["turn"], r["action_detail"])
            assert bench[j] == r["action_detail"]


def test_same_turn_rows_describe_identical_world():
    """Both players' normal-turn rows for turn N must be perspective flips of
    ONE boundary state: my hp of one == opp hp of the other, when revealed."""
    by_turn: dict[int, dict[int, dict]] = {}
    for r in rows():
        if r["request_kind"] != "turn":
            continue
        by_turn.setdefault(r["turn"], {}).setdefault(r["player"], r)
    checked = 0
    for turn, players in by_turn.items():
        if set(players) != {1, 2}:
            continue
        s1, s2 = state(players[1]), state(players[2])
        own1 = {m["species"]: m["hp_fraction"] for m in s1["my_side"]["pokemon"]}
        seen_by_2 = {
            m["species"]: m["hp_fraction"]
            for m in s2["opp_side"]["pokemon"]
            if m["species"] is not None
        }
        for sp, hp in seen_by_2.items():
            assert sp in own1, f"turn {turn}: p2 sees {sp} that p1 doesn't own yet"
            assert hp == own1[sp], f"turn {turn}: {sp} hp differs across perspectives"
            checked += 1
    assert checked >= 30, f"only {checked} cross-perspective checks"


def test_move_reveal_not_visible_same_turn():
    """A normal-turn row must not contain an opp move first revealed that turn."""
    first = _true_first_use()
    for r in rows():
        if r["request_kind"] != "turn":
            continue
        s = state(r)
        opp_player = 2 if r["player"] == 1 else 1
        for mon in s["opp_side"]["pokemon"]:
            for mv in mon.get("revealed_moves", []):
                assert first.get((opp_player, mv), 0) < r["turn"], (
                    f"turn {r['turn']}: {mv} revealed same turn is visible"
                )


def test_forced_switch_rows_keep_execution_time_state():
    forced = [r for r in rows() if r["request_kind"] == "force_switch"]
    assert forced, "fixture has faints; forced switches must survive the rework"
    for r in forced:
        s = state(r)
        mons = s["my_side"]["pokemon"]
        ix = s["my_side"]["active_ix"]
        bench = [m["species"] for i, m in enumerate(mons) if i != ix]
        assert bench[r["action"] - 4] == r["action_detail"]
