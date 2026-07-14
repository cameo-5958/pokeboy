"""Decision-relevant move features (dmg_feats): expected damage fraction,
guaranteed-kill flag, accuracy, and wasted-move flag on MY_ACTIVE move tokens.

Motivated by the skill probe: the model mastered the one skill the tokenizer
hands it as a feature (type_eff) and failed every skill that requires deriving
damage magnitude or move-failure rules from move identity alone.
"""

import json

import pytest

from models.tokenizer import CONT_CHANNELS, Tokenizer
from tests.test_tokenizer import FIXTURE_STATE


def _state(my_moves, opp_species, opp_hp=1.0, opp_status=None, my_hp=1.0):
    return {
        "schema_v": 1, "turn": 10, "request_kind": "turn",
        "my_side": {"active_ix": 0, "pokemon": [
            {"species": "Starmie", "hp_fraction": my_hp, "status": None,
             "moves": my_moves, "pp": [10] * len(my_moves), "fainted": False},
        ]},
        "opp_side": {"active_ix": 0, "pokemon": [
            {"species": opp_species, "hp_fraction": opp_hp, "status": opp_status,
             "revealed_moves": [], "fainted": False},
        ]},
    }


def _move_channels(tok, state):
    """channel dict per MY_ACTIVE move token, in move order."""
    enc = tok.encode(state)
    move_f = tok.field_id("MOVE")
    my_active = tok.slot_id("MY_ACTIVE")
    out = []
    for i in range(enc["length"]):
        if enc["field_ids"][i] == move_f and enc["slot_ids"][i] == my_active:
            out.append({c: float(enc["cont"][i][tok.cont_channel(c)])
                        for c in ("dmg_frac", "kills", "acc", "wasted")})
    return out


def test_legacy_tokenizer_unchanged():
    tok = Tokenizer()
    assert tok.n_cont == len(CONT_CHANNELS)
    with pytest.raises(KeyError):
        tok.cont_channel("dmg_frac")
    tok.encode(FIXTURE_STATE)  # still encodes


def test_dmg_feats_adds_channels():
    tok = Tokenizer(dmg_feats=True)
    assert tok.n_cont == len(CONT_CHANNELS) + 4
    enc = tok.encode(FIXTURE_STATE)
    assert enc["cont"].shape[1] == tok.n_cont


def test_damage_ordering_blizzard_vs_psychic_on_rhydon():
    tok = Tokenizer(dmg_feats=True)
    ch = _move_channels(tok, _state(["Psychic", "Blizzard", "Thunder Wave"], "Rhydon"))
    psychic, blizzard, twave = ch[0], ch[1], ch[2]
    # 2x 120bp vs neutral STAB 90bp: ~78% vs ~44% of Rhydon's HP
    assert blizzard["dmg_frac"] > 1.5 * psychic["dmg_frac"]
    assert blizzard["kills"] == 0.0  # 2HKO, not OHKO, at full HP
    assert psychic["kills"] == 0.0
    assert twave["dmg_frac"] == 0.0
    assert abs(blizzard["acc"] - 0.9) < 1e-6 and abs(psychic["acc"] - 1.0) < 1e-6
    half = _move_channels(tok, _state(["Blizzard"], "Rhydon", opp_hp=0.5))[0]
    assert half["kills"] == 1.0  # min roll clears 50%


def test_kills_flag_tracks_current_hp():
    tok = Tokenizer(dmg_feats=True)
    full = _move_channels(tok, _state(["Psychic"], "Chansey", opp_hp=1.0))[0]
    dying = _move_channels(tok, _state(["Psychic"], "Chansey", opp_hp=0.03))[0]
    assert full["kills"] == 0.0
    assert dying["kills"] == 1.0


def test_wasted_status_move_vs_statused_target():
    tok = Tokenizer(dmg_feats=True)
    healthy = _move_channels(tok, _state(["Thunder Wave"], "Starmie"))[0]
    par = _move_channels(tok, _state(["Thunder Wave"], "Starmie", opp_status="PAR"))[0]
    slp = _move_channels(tok, _state(["Sleep Powder"], "Chansey", opp_status="SLP"))[0]
    immune = _move_channels(tok, _state(["Thunder Wave"], "Rhydon"))[0]  # Ground immune
    assert healthy["wasted"] == 0.0
    assert par["wasted"] == 1.0
    assert slp["wasted"] == 1.0
    assert immune["wasted"] == 1.0


def test_wasted_heal_at_full_hp():
    tok = Tokenizer(dmg_feats=True)
    full = _move_channels(tok, _state(["Recover"], "Tauros", my_hp=1.0))[0]
    low = _move_channels(tok, _state(["Recover"], "Tauros", my_hp=0.12))[0]
    assert full["wasted"] == 1.0
    assert low["wasted"] == 0.0


def test_fixed_damage_moves():
    tok = Tokenizer(dmg_feats=True)
    ch = _move_channels(tok, _state(["Seismic Toss"], "Chansey"))[0]
    # 100 flat vs ~703 max HP Chansey
    assert 0.10 < ch["dmg_frac"] < 0.20


def test_model_agent_roundtrips_dmg_feats(tmp_path):
    torch = pytest.importorskip("torch")
    from models.agent import ModelAgent
    from models.encoder import FieldValueEncoder
    from models.tiers import TIERS
    from tests.test_model_agent import _state as agent_state

    tok = Tokenizer(dmg_feats=True)
    model = FieldValueEncoder(TIERS["snack"], tok)
    path = tmp_path / "model.pt"
    torch.save(
        {"model": model.state_dict(), "tier": "snack", "steps": 0, "hist_k": 0,
         "seq_len": tok.seq_len, "value_bins": 0, "dmg_feats": True},
        path,
    )
    agent = ModelAgent(path, seed=2, device="cpu")
    assert agent.tok.n_cont == tok.n_cont
    legal = [0, 1, 9]
    assert all(agent.choose(agent_state(legal)) in legal for _ in range(10))
