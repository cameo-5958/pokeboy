"""Smoke test for the recurrent PPO fine-tune of PEP: two tiny CPU iterations end to end."""
from __future__ import annotations

import os

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from models.pep import PEP, PEPConfig, load_checkpoint, save_checkpoint  # noqa: E402
from models.train_ppo import build_parser, gae, train  # noqa: E402

torch.set_num_threads(2)


def test_gae_terminal_only_reward():
    v = np.array([0.2, -0.1, 0.4], np.float32)
    adv, ret = gae(v, 1.0, gamma=1.0, lam=0.95)
    d2 = 1.0 - 0.4
    d1 = 0.4 - (-0.1)
    d0 = -0.1 - 0.2
    assert adv[2] == pytest.approx(d2)
    assert adv[1] == pytest.approx(d1 + 0.95 * d2)
    assert adv[0] == pytest.approx(d0 + 0.95 * (d1 + 0.95 * d2))
    assert np.allclose(ret, adv + v)


def test_gae_reward_array_matches_terminal_form():
    v = np.array([0.2, -0.1, 0.4], np.float32)
    a1, r1 = gae(v, 1.0, gamma=1.0, lam=0.95)
    a2, r2 = gae(v, np.array([0.0, 0.0, 1.0], np.float32), gamma=1.0, lam=0.95)
    assert np.allclose(a1, a2) and np.allclose(r1, r2)
    a3, _ = gae(v, np.array([0.5, 0.0, 1.0], np.float32), gamma=1.0, lam=0.95)
    assert a3[0] == pytest.approx(a1[0] + 0.5)


def test_train_ppo_league_tiny(tmp_path):
    cfg = PEPConfig(d=32, layers=1, ffn=64, gru=32)
    init = tmp_path / "init.pt"
    save_checkpoint(PEP(cfg), str(init), steps=0)
    args = build_parser().parse_args(
        [
            "--init-ckpt", str(init), "--run", "ppo-league", "--iters", "2", "--battles-per-iter", "4",
            "--workers", "0", "--device", "cpu", "--threads", "2", "--minibatch", "2", "--epochs", "1",
            "--eval-every", "0", "--eval-battles", "0", "--no-eval-at-start", "--ckpt-dir", str(tmp_path),
            "--opponents", "self:0.5,past:0.5", "--matchups", "mirror:0.5,balanced:0.5", "--shaping", "0.3",
            "--past-every", "1", "--past-init", "--reset-feat-cols", "own:38-41,player:34-37",
        ]
    )
    res = train(args)
    assert res["updates"] > 0
    assert os.path.exists(os.path.join(tmp_path, "ppo-league", "league", "iter-00001.pt"))


def test_train_ppo_tiny(tmp_path):
    cfg = PEPConfig(d=32, layers=1, ffn=64, gru=32)
    init = tmp_path / "init.pt"
    save_checkpoint(PEP(cfg), str(init), steps=0)
    args = build_parser().parse_args(
        [
            "--init-ckpt", str(init), "--run", "ppo-test", "--iters", "2", "--battles-per-iter", "4",
            "--workers", "2", "--device", "cpu", "--threads", "2", "--minibatch", "2", "--epochs", "2",
            "--eval-every", "1", "--eval-battles", "4", "--ckpt-dir", str(tmp_path), "--opponents", "greedy,random",
        ]
    )
    res = train(args)
    assert len(res["history"]) == 2
    assert res["updates"] > 0
    for h in res["history"]:
        assert h["battles"] == 4 and h["decisions"] > 0
        assert np.isfinite(h["loss"]) and np.isfinite(h["approx_kl"]) and h["entropy"] >= 0
    assert res["evals"] and 0.0 <= res["evals"][-1]["win_rate"] <= 1.0
    ckpt = os.path.join(str(tmp_path), "ppo-test", "model.pt")
    assert os.path.exists(ckpt) and os.path.exists(os.path.join(str(tmp_path), "ppo-test", "iter-00002.pt"))
    model, blob = load_checkpoint(ckpt)
    assert model.cfg == cfg
    assert blob["ppo"]["iter"] == 2
    assert os.path.exists(os.path.join(str(tmp_path), "ppo-test", "log.jsonl"))
