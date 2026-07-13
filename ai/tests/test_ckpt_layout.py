import json
import subprocess
import sys
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")

CORPUS = Path(__file__).parents[1] / "datasets" / "processed"


@pytest.mark.skipif(not any(CORPUS.rglob("part-*.parquet")) if CORPUS.exists() else True,
                    reason="no converted corpus present")
def test_checkpoints_are_per_run_and_never_clobber(tmp_path):
    def run(steps):
        p = subprocess.run(
            [sys.executable, "-m", "models.train_imitation", "--tier", "snack",
             "--limit-rows", "3000", "--steps", str(steps),
             "--ckpt-root", str(tmp_path), "--eval-every", "0"],
            capture_output=True, text=True, timeout=600,
            cwd=Path(__file__).parents[1],
        )
        assert p.returncode == 0, p.stderr[-2000:]

    run(10)
    run(20)  # different run id → must not overwrite the first
    runs = [d for d in (tmp_path / "snack").iterdir() if d.is_dir() and d.name != "latest"]
    assert len(runs) == 2, [d.name for d in runs]
    for d in runs:
        assert (d / "model.pt").exists()
        assert (d / "metrics.jsonl").exists()
    latest = tmp_path / "snack" / "latest"
    assert latest.is_symlink()
    ckpt = torch.load(latest / "model.pt", map_location="cpu", weights_only=True)
    assert ckpt["steps"] == 20  # points at the most recent run
