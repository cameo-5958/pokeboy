"""The trainer-seat network can hold the player seat: swapped view is consistent and playable."""
import numpy as np
import pytest

torch = pytest.importorskip("torch")

from models.pep import PEP, PEPConfig  # noqa: E402
from sim import trainers  # noqa: E402
from sim.player_seat import NetPlayer, player_view, swap_event  # noqa: E402
from sim.trainer_env import TrainerEnv  # noqa: E402
from tools.eval_trainer import play  # noqa: E402

pytestmark = pytest.mark.skipif(
    not trainers.available(),
    reason="needs datasets/trainers.json: cd ai && "
           "uv run python tools/dump_trainers.py",
)


def test_player_view_swaps_sides():
    parties = trainers.load().parties
    t, o = parties[10], parties[20]
    env = TrainerEnv(t.to_specs(), t.class_id, o.to_specs(), seed=3, rng_seed=1)
    v = player_view(env)
    ov, oe = v.observation(), env.observation()
    assert ov.own_count == len(o.mons) and ov.player_count == len(t.mons)
    assert ov.active.species == oe.player[oe.player_slot].species
    assert ov.player[ov.player_slot].species == oe.active.species
    assert v.count == 0 and all(not (v.features()[1] >> a & 1) for a in range(10, 16))  # no items for the player

def test_swap_event_mirrors_sides():
    ev = np.arange(64, dtype=np.float32)
    out = swap_event(ev, 1)
    assert out[1] == 1.0 and out[0] == 0.0 and list(out[3:9]) == list(ev[9:15]) and list(out[9:15]) == list(ev[3:9])


def test_net_player_completes_battles():
    parties = trainers.load().parties
    model = PEP(PEPConfig(d=32, layers=1, ffn=64, gru=32)).eval()
    player = NetPlayer(model, seed=0, temperature=1.0)
    done = 0
    for i in range(3):
        p = parties[100 + i]
        env = TrainerEnv(p.to_specs(), p.class_id, p.to_specs(), seed=i, rng_seed=i)
        r = play(env, lambda e: e.legal_actions()[0], player.choose, max_decisions=200)
        done += r is not None or env.done()
    assert done == 3
