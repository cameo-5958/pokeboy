"""serve.int_agent: the C++ integer model through libpkai_c, bit-exact against models.pep_int vectors."""
from __future__ import annotations

import os

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN = os.path.join(ROOT, "checkpoints", "pep", "v2-qat")
WEIGHTS = os.path.join(RUN, "pkai.weights")
VECTORS = os.path.join(RUN, "vectors")


@pytest.fixture(scope="module")
def model():
    if not os.path.exists(WEIGHTS):
        pytest.skip(f"{WEIGHTS} missing")
    try:
        from serve.int_agent import IntModel
        return IntModel(WEIGHTS)
    except (FileNotFoundError, OSError) as e:  # library not built
        pytest.skip(f"libpkai_c unavailable: {e}")


def _encode_rows(inp) -> list[bytes]:
    from models.pep_data import encode_features
    return [
        encode_features(present=inp["present"][i], candidate=inp["candidate"][i], cat=inp["cat"][i], f=inp["f"][i],
                        legal=int(inp["legal"][i]), request_kind=int(inp["request_kind"][i]), type_=inp["type"][i])
        for i in range(inp["f"].shape[0])
    ]


def test_matches_reference_vectors_bit_exact(model):
    if not os.path.exists(os.path.join(VECTORS, "inputs.npz")):
        pytest.skip("reference vectors missing")
    inp = np.load(os.path.join(VECTORS, "inputs.npz"))
    outs = np.load(os.path.join(VECTORS, "outputs.npz"))
    assert inp["h0"].shape[1] == model.gru
    for i, blob in enumerate(_encode_rows(inp)):
        h = np.ascontiguousarray(inp["h0"][i].astype(np.int16))
        logits, probs, vacc = model.run(blob, inp["ev8"][i], h)
        assert np.array_equal(logits, outs["logits_q8"][i]), i
        assert np.array_equal(probs, outs["probs"][i]), i
        assert np.array_equal(h, outs["h"][i]), i
        assert vacc == int(outs["value_acc"][i]), i


def test_sequence_state_carries_bit_exact(model):
    from models.pep_int import IntPEP
    path = os.path.join(VECTORS, "sequence.npz")
    if not os.path.exists(path):
        pytest.skip("sequence vectors missing")
    seq = np.load(path)
    from models.pep_data import encode_features
    for b in range(seq["steps"].shape[0]):
        h = np.zeros(model.gru, np.int16)
        for t in range(int(seq["steps"][b])):
            blob = encode_features(present=seq["present"][b, t], candidate=seq["candidate"][b, t], cat=seq["cat"][b, t],
                                   f=seq["f"][b, t], legal=int(seq["legal"][b, t]), type_=seq["type"][b, t])
            ev8 = IntPEP.quantize_event(seq["event"][b, t][None])[0]
            logits, probs, _ = model.run(blob, ev8, h)
            assert np.array_equal(logits, seq["logits_q8"][b, t]), (b, t)
            assert np.array_equal(probs, seq["probs"][b, t]), (b, t)
            assert np.array_equal(h, seq["h"][b, t]), (b, t)


def test_one_battle_actions_legal(model):
    pytest.importorskip("pyarrow")
    from serve.int_agent import IntAgent, sample_u8
    from sim import trainers
    from sim.trainer_env import TrainerEnv
    from sim.trainer_search import TrainerTeacher
    from tools.eval_trainer import play

    parties = trainers.load().parties
    agent = IntAgent(WEIGHTS, seed=1)
    t, o = parties[0], parties[1]
    env = TrainerEnv(t.to_specs(), t.class_id, o.to_specs(), seed=7, rng_seed=3)
    teacher = TrainerTeacher(depth=1, rolls=1, seed=5)
    n = 0

    def policy(env):
        nonlocal n
        legal = env.legal_actions()
        a = agent.choose(env)
        assert a in legal
        n += 1
        return a

    agent.reset()
    play(env, policy, lambda e: teacher._greedy_player(e))
    assert n > 0 and env.done()
    # inverse-CDF sampling: deterministic under a seed, zero-probability actions never drawn
    p = np.zeros(16, np.uint8); p[3] = 200; p[9] = 56
    rng = np.random.default_rng(0)
    draws = {sample_u8(p, rng, 0xFFFF) for _ in range(200)}
    assert draws <= {3, 9} and 3 in draws
    assert sample_u8(np.zeros(16, np.uint8), rng, 0b1010) == 1
