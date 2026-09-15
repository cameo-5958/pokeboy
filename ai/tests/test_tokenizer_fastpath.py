"""The tokenizer's encode() output is a training-data contract: the
preallocated fast path must produce byte-identical encodings to the
original list-append implementation (kept here as the reference)."""

import random

import numpy as np

from models.tokenizer import Tokenizer
from sim.agents import MaxDamageBot
from sim.battle import Battle
from sim.teams import sample_team


def _reference_encode(tok: Tokenizer, state: dict) -> dict:
    fields, values, slots = [], [], []
    cont: list[np.ndarray] = []

    def emit(field: str, value: str, slot: str, **channels: float) -> None:
        fields.append(tok._field_ix[field])
        values.append(tok._value_ix[value])
        slots.append(tok._slot_ix[slot])
        c = np.zeros(len(tok._cont_ix), dtype=np.float32)
        for k, v in channels.items():
            c[tok._cont_ix[k]] = v
        cont.append(c)

    from models.tokenizer import NONE

    emit("GLB", NONE, "GLOBAL",
         turn=min(state.get("turn", 0), 200) / 200.0,
         force_switch=1.0 if state.get("request_kind") == "force_switch" else 0.0)
    opp_active = tok._active_mon(state["opp_side"])
    my_active = tok._active_mon(state["my_side"])
    tok._emit_side(emit, state["my_side"], "MY", mine=True, opp_active=opp_active)
    tok._emit_side(emit, state["opp_side"], "OPP", mine=False, opp_active=my_active)
    if tok.hist_k:
        for entry in state.get("history_tail", [])[: tok.hist_k]:
            tok._emit_hist(emit, entry)
    n = len(fields)
    pad = tok.seq_len - n
    return {
        "field_ids": np.asarray(fields + [0] * pad, dtype=np.int16),
        "value_ids": np.asarray(values + [0] * pad, dtype=np.int16),
        "slot_ids": np.asarray(slots + [0] * pad, dtype=np.int16),
        "cont": np.vstack(cont + [np.zeros((pad, len(tok._cont_ix)), dtype=np.float32)]),
        "length": n,
    }


def test_encode_matches_reference_across_battle_states():
    for dmg_feats in (False, True):
        tok = Tokenizer(hist_k=20, dmg_feats=dmg_feats)
        rng = random.Random(17)
        b = Battle(sample_team(rng), sample_team(rng), seed=33)
        bots = (MaxDamageBot(), MaxDamageBot())
        checked = 0
        for _ in range(60):
            if b.winner:
                break
            for player in (1, 2):
                state = b.state(player).to_json()
                got = tok.encode(state)
                want = _reference_encode(tok, state)
                assert got["length"] == want["length"]
                for key in ("field_ids", "value_ids", "slot_ids"):
                    assert np.array_equal(got[key], want[key]), key
                assert np.array_equal(got["cont"], want["cont"])
                checked += 1
            b.step(bots[0].choose(b.state(1)), bots[1].choose(b.state(2)))
        assert checked > 40
