import json
import os
import subprocess
import sys

import pytest

torch = pytest.importorskip("torch")

from models.agent import ModelAgent
from models.encoder import FieldValueEncoder
from models.tiers import TIERS
from sim.schema import State
from tests.test_tokenizer import FIXTURE_STATE


@pytest.fixture(scope="module")
def ckpt(tmp_path_factory):
    torch.manual_seed(0)
    model = FieldValueEncoder(TIERS["snack"])
    path = tmp_path_factory.mktemp("ckpt") / "model.pt"
    torch.save({"model": model.state_dict(), "tier": "snack", "steps": 0}, path)
    return path


def _state(legal_actions):
    d = json.loads(json.dumps(FIXTURE_STATE))
    return State(
        battle_id="test",
        turn=d["turn"],
        request_kind=d["request_kind"],
        my_side=d["my_side"],
        opp_side=d["opp_side"],
        legal_actions=legal_actions,
    )


def test_choose_returns_legal_action(ckpt):
    agent = ModelAgent(ckpt, seed=1, device="cpu")
    legal = [0, 1, 2, 3, 9]
    assert all(agent.choose(_state(legal)) in legal for _ in range(20))


def test_illegal_actions_never_sampled(ckpt):
    agent = ModelAgent(ckpt, seed=2, device="cpu")
    assert [agent.choose(_state([4])) for _ in range(10)] == [4] * 10


def test_samples_rather_than_argmax(ckpt):
    agent = ModelAgent(ckpt, seed=3, device="cpu")
    choices = {agent.choose(_state(list(range(10)))) for _ in range(60)}
    assert len(choices) > 1, "policy must sample, not argmax (SPECS §7)"


def test_deterministic_given_seed(ckpt):
    s = _state([0, 1, 2, 3, 9])
    a = ModelAgent(ckpt, seed=7, device="cpu")
    b = ModelAgent(ckpt, seed=7, device="cpu")
    assert [a.choose(s) for _ in range(10)] == [b.choose(s) for _ in range(10)]


def test_cli_model_seat_smoke(ckpt):
    env = {**os.environ, "CUDA_VISIBLE_DEVICES": ""}
    p = subprocess.run(
        [sys.executable, "-m", "sim", "battle",
         "--p1", f"model:{ckpt}", "--p2", "random", "--seed", "5", "--battles", "2"],
        capture_output=True, text=True, timeout=300, env=env,
    )
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout.splitlines()[-1])
    assert out["battles"] == 2
    assert out["p1_wins"] + out["p2_wins"] + out["ties"] + out["unfinished"] == 2
