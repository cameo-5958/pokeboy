"""Teacher-labelled row generation round trip (synthetic parties, no trainers.json needed)."""
import random

import numpy as np
import pyarrow.parquet as pq
import pytest

from sim.generate_trainer import greedy_opponent, play_battle, write_rows
from sim.trainer_env import EVENT_DIM
from sim.trainer_search import TrainerTeacher
from tests.test_trainer_env import surge_env

pytest.importorskip("pkai")


def test_rows_round_trip(tmp_path):
    env = surge_env(5)
    teacher = TrainerTeacher(depth=1, rolls=1, seed=0)
    rows = play_battle(env, teacher, greedy_opponent(TrainerTeacher(depth=1, rolls=1, seed=1)), battle_id="b1")
    assert rows and rows[0]["seq"] == 0 and all(r["battle_id"] == "b1" for r in rows)
    assert all(abs(sum(r["teacher_probs"]) - 1.0) < 1e-5 for r in rows)
    assert all(r["legal"] >> r["action"] & 1 for r in rows)
    assert all(len(r["event"]) == EVENT_DIM * 4 for r in rows)
    path = write_rows(rows, tmp_path, 0)
    t = pq.read_table(path)
    assert t.num_rows == len(rows)
    assert t.column("won").to_pylist()[0] in (True, False)
    ev = np.frombuffer(t.column("event").to_pylist()[-1], dtype=np.float32)
    assert ev.shape == (EVENT_DIM,)
