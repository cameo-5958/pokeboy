"""Regression tests for known runtime bugs in the data pipeline."""

import json
import random
import subprocess
import sys
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")

CORPUS = Path(__file__).parents[1] / "datasets" / "processed"
HAS_CORPUS = CORPUS.exists() and any(CORPUS.rglob("part-*.parquet"))


def run_trainer(tmp_path, *extra):
    return subprocess.run(
        [sys.executable, "-m", "models.train_imitation", "--tier", "snack",
         "--ckpt-root", str(tmp_path), *extra],
        capture_output=True, text=True, timeout=600,
        cwd=Path(__file__).parents[1],
    )


@pytest.mark.skipif(not HAS_CORPUS, reason="no converted corpus present")
def test_latest_always_has_a_checkpoint(tmp_path):
    p = run_trainer(tmp_path, "--limit-rows", "3000", "--steps", "10")
    assert p.returncode == 0, p.stderr[-1500:]
    latest = tmp_path / "snack" / "latest"
    assert latest.is_symlink() and (latest / "model.pt").exists()


def test_invalid_args_fail_fast_without_run_dir(tmp_path):
    for bad in (["--steps", "0"], ["--batch-size", "0"],
                ["--limit-rows", "0"], ["--holdout", "1.5"]):
        p = run_trainer(tmp_path, "--limit-rows", "3000", *bad)
        assert p.returncode != 0, f"{bad} accepted"
        assert not (tmp_path / "snack").exists(), f"{bad} created run dir"


def test_make_batches_rejects_empty_rows():
    from models.tokenizer import Tokenizer
    from models.train_imitation import make_batches

    with pytest.raises(ValueError, match="empty"):
        next(make_batches([], Tokenizer(), 8, "cpu"))


def test_battle_ids_are_wide_and_unique():
    from sim.battle import Battle
    from sim.teams import sample_team

    rng = random.Random(0)
    ids = set()
    for _ in range(300):
        b = Battle(sample_team(rng), sample_team(rng), seed=rng.randrange(2**63))
        assert len(b.battle_id) >= 34  # "b_" + ≥32 hex chars (128 bits)
        ids.add(b.battle_id)
    assert len(ids) == 300


def test_model_agent_reseed_restores_determinism(tmp_path):
    from models.agent import ModelAgent
    from models.encoder import FieldValueEncoder
    from models.tiers import TIERS
    from tests.test_model_agent import _state

    torch.manual_seed(0)
    path = tmp_path / "model.pt"
    torch.save({"model": FieldValueEncoder(TIERS["snack"]).state_dict(),
                "tier": "snack", "steps": 0}, path)
    agent = ModelAgent(path, seed=1, device="cpu")
    s = _state(list(range(10)))
    first = [agent.choose(s) for _ in range(8)]
    agent.reseed(1)
    assert [agent.choose(s) for _ in range(8)] == first
