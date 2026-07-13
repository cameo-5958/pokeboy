import json
from pathlib import Path

import numpy as np

from models.tokenizer import SEQ_LEN, Tokenizer

FIXTURE_STATE = {
    "schema_v": 1,
    "turn": 12,
    "request_kind": "turn",
    "my_side": {
        "active_ix": 0,
        "pokemon": [
            {
                "species": "Snorlax",
                "hp_fraction": 0.62,
                "status": "PAR",
                "moves": ["Body Slam", "Reflect", "Rest", "Ice Beam"],
                "pp": [12, 30, 15, 15],
                "fainted": False,
            },
            {
                "species": "Tauros",
                "hp_fraction": 1.0,
                "status": None,
                "moves": ["Body Slam", "Hyper Beam"],
                "fainted": False,
            },
        ],
    },
    "opp_side": {
        "active_ix": 0,
        "pokemon": [
            {
                "species": "Chansey",
                "hp_fraction": 0.8,
                "status": None,
                "revealed_moves": ["Ice Beam"],
                "fainted": False,
            },
            {"species": None, "hp_fraction": None, "status": None, "revealed_moves": []},
        ],
    },
}


def test_shapes_and_padding():
    tok = Tokenizer()
    enc = tok.encode(FIXTURE_STATE)
    for key in ("field_ids", "value_ids", "slot_ids"):
        assert enc[key].shape == (SEQ_LEN,)
        assert enc[key].dtype == np.int16
    assert enc["cont"].shape == (SEQ_LEN, tok.n_cont)
    # padding tail is PAD (0) everywhere
    n = enc["length"]
    assert n < SEQ_LEN
    assert (enc["value_ids"][n:] == 0).all()


def test_vocab_bounds():
    tok = Tokenizer()
    assert tok.n_values <= 512
    enc = tok.encode(FIXTURE_STATE)
    assert enc["value_ids"].max() < tok.n_values
    assert enc["field_ids"].max() < tok.n_fields
    assert enc["slot_ids"].max() < tok.n_slots


def test_deterministic():
    tok = Tokenizer()
    a, b = tok.encode(FIXTURE_STATE), tok.encode(FIXTURE_STATE)
    assert all(np.array_equal(a[k], b[k]) for k in a)


def test_hidden_info_stays_hidden():
    tok = Tokenizer()
    enc = tok.encode(FIXTURE_STATE)
    # the unrevealed opp bench slot must encode UNREVEALED, never a species
    unrevealed_id = tok.value_id("UNREVEALED")
    slot = tok.slot_id("OPP_BENCH_1")
    mask = enc["slot_ids"] == slot
    species_field = tok.field_id("SPECIES")
    ids = enc["value_ids"][(enc["field_ids"] == species_field) & mask]
    assert list(ids) == [unrevealed_id]


def test_distinct_states_distinct_encodings():
    tok = Tokenizer()
    other = json.loads(json.dumps(FIXTURE_STATE))
    other["my_side"]["pokemon"][0]["hp_fraction"] = 0.1
    a, b = tok.encode(FIXTURE_STATE), tok.encode(other)
    assert not np.array_equal(a["cont"], b["cont"])


def test_type_effectiveness_channel():
    tok = Tokenizer()
    enc = tok.encode(FIXTURE_STATE)
    # Body Slam (normal, STAB for Snorlax) vs Chansey (normal): eff 1.0, stab 1.0
    move_field = tok.field_id("MOVE")
    active = tok.slot_id("MY_ACTIVE")
    rows = np.where((enc["field_ids"] == move_field) & (enc["slot_ids"] == active))[0]
    assert len(rows) == 4
    eff_ch, stab_ch = tok.cont_channel("type_eff"), tok.cont_channel("stab")
    assert enc["cont"][rows[0], eff_ch] == 0.25  # 1.0x scaled by /4
    assert enc["cont"][rows[0], stab_ch] == 1.0


def test_works_on_converted_corpus_rows():
    import pyarrow.parquet as pq

    parts = sorted(
        (Path(__file__).parents[1] / "datasets" / "processed").rglob("part-*.parquet")
    )
    if not parts:
        import pytest

        pytest.skip("no converted corpus present")
    tok = Tokenizer()
    table = pq.read_table(parts[0], columns=["state_json"])
    for s in table["state_json"][:200].to_pylist():
        enc = tok.encode(json.loads(s))
        assert enc["length"] > 0
