"""Tokenizer HIST segment (SPECS §4.1): 5 tokens per remembered turn, slot
embedding = turn offset, gated by hist_k so hist_k=0 reproduces the legacy
encoding and old checkpoints keep loading."""

import json

import numpy as np

from models.tokenizer import SEQ_LEN, Tokenizer
from tests.test_tokenizer import FIXTURE_STATE


def state_with_tail():
    s = json.loads(json.dumps(FIXTURE_STATE))
    s["history_tail"] = [
        {"o": -1, "my": "M:Body Slam", "op": "S:Starmie", "dm": 3, "do": 0, "ev": ["crit", "se"]},
        {"o": -2, "my": None, "op": "M:Blizzard", "dm": 8, "do": None, "ev": ["ft"]},
    ]
    return s


def test_hist_k0_is_legacy_encoding():
    legacy, hist0 = Tokenizer(), Tokenizer(hist_k=0)
    assert hist0.n_values == legacy.n_values
    assert hist0.n_fields == legacy.n_fields
    assert hist0.n_slots == legacy.n_slots
    a, b = legacy.encode(state_with_tail()), hist0.encode(state_with_tail())
    assert a["length"] == b["length"]  # tail ignored entirely
    assert all(np.array_equal(a[k], b[k]) for k in a)


def test_hist_tokens_appended():
    tok = Tokenizer(hist_k=20)
    assert tok.seq_len == 256
    base = tok.encode(FIXTURE_STATE)["length"]
    enc = tok.encode(state_with_tail())
    assert enc["length"] == base + 2 * 5
    my_act = tok.field_id("HIST_MY_ACTION")
    slot_m1 = tok.slot_id("HIST_-1")
    rows = np.where((enc["field_ids"] == my_act) & (enc["slot_ids"] == slot_m1))[0]
    assert len(rows) == 1
    assert enc["value_ids"][rows[0]] == tok.value_id("MOVE:Body Slam")


def test_hist_action_value_kinds():
    tok = Tokenizer(hist_k=20)
    enc = tok.encode(state_with_tail())
    opp_act = tok.field_id("HIST_OPP_ACTION")
    s1 = tok.slot_id("HIST_-1")
    s2 = tok.slot_id("HIST_-2")
    v = {(-1): None, (-2): None}
    for o, slot in ((-1, s1), (-2, s2)):
        rows = np.where((enc["field_ids"] == opp_act) & (enc["slot_ids"] == slot))[0]
        v[o] = enc["value_ids"][rows[0]]
    assert v[-1] == tok.value_id("SPECIES:Starmie")  # switch → species value
    assert v[-2] == tok.value_id("MOVE:Blizzard")
    my_act = tok.field_id("HIST_MY_ACTION")
    rows = np.where((enc["field_ids"] == my_act) & (enc["slot_ids"] == s2))[0]
    assert enc["value_ids"][rows[0]] == tok.value_id("UNKNOWN")  # unobserved turn


def test_hist_damage_and_event_encoding():
    tok = Tokenizer(hist_k=20)
    enc = tok.encode(state_with_tail())
    s1, s2 = tok.slot_id("HIST_-1"), tok.slot_id("HIST_-2")

    def val(field, slot):
        rows = np.where((enc["field_ids"] == tok.field_id(field)) & (enc["slot_ids"] == slot))[0]
        return enc["value_ids"][rows[0]]

    assert val("HIST_DMG_ME", s1) == tok.value_id("DMG_B3")
    assert val("HIST_DMG_OPP", s1) == tok.value_id("DMG_B0")
    assert val("HIST_DMG_ME", s2) == tok.value_id("DMG_B8")  # KO bucket
    assert val("HIST_DMG_OPP", s2) == tok.value_id("UNKNOWN")
    assert val("HIST_EVENT", s1) == tok.value_id("EV_crit")  # priority over se
    assert val("HIST_EVENT", s2) == tok.value_id("EV_ft")


def test_hist_k_truncates_and_pads():
    tok = Tokenizer(hist_k=20)
    s = state_with_tail()
    s["history_tail"] = [
        {"o": -(i + 1), "my": "M:Body Slam", "op": None, "dm": 0, "do": 0, "ev": []}
        for i in range(30)
    ]
    base = tok.encode(FIXTURE_STATE)["length"]
    enc = tok.encode(s)
    assert enc["length"] == base + 20 * 5  # capped at hist_k turns
    assert enc["length"] <= tok.seq_len
    assert (enc["value_ids"][enc["length"] :] == 0).all()


def test_no_tail_encodes_like_stateless():
    tok = Tokenizer(hist_k=20)
    s = json.loads(json.dumps(FIXTURE_STATE))  # no history_tail key at all
    enc = tok.encode(s)
    assert enc["length"] == Tokenizer().encode(s)["length"]
