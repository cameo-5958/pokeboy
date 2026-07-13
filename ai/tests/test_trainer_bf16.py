import json
import subprocess
import sys
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")

CORPUS = Path(__file__).parents[1] / "datasets" / "processed"


@pytest.mark.skipif(not any(CORPUS.rglob("part-*.parquet")) if CORPUS.exists() else True,
                    reason="no converted corpus present")
def test_bf16_flag_trains_and_loss_decreases(tmp_path):
    p = subprocess.run(
        [sys.executable, "-m", "models.train_imitation", "--tier", "snack",
         "--limit-rows", "6000", "--steps", "30", "--bf16", "--eval-battles", "0"],
        capture_output=True, text=True, timeout=600,
        cwd=Path(__file__).parents[1],
    )
    assert p.returncode == 0, p.stderr[-2000:]
    losses = [json.loads(line)["loss"] for line in p.stdout.splitlines()
              if '"loss"' in line]
    assert losses, p.stdout[-2000:]
    assert losses[-1] < losses[0], f"loss did not decrease: {losses}"
    assert all(l == l and l < 100 for l in losses)  # finite, sane
