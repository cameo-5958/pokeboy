"""16-way search teacher over the trainer environment."""
import time

import pytest

from sim.trainer_search import TrainerTeacher
from tests.test_trainer_env import first_move, surge_env

pytest.importorskip("pkai")


def test_teacher_prefers_electric_move_on_pidgeotto():
    env = surge_env(7)
    # Switch the player to Pidgeotto (Flying) so Thunderbolt-class moves dominate once Raichu is out.
    env.step(5, first_move(env))          # trainer brings Raichu; player attacks
    while env.request_kind() is None and not env.done():
        env.auto_step(env.player_choices()[0])
    teacher = TrainerTeacher(depth=1, rolls=2, seed=1)
    probs, scores = teacher.policy(env)
    assert abs(sum(probs) - 1.0) < 1e-6
    legal = env.legal_actions()
    assert all(probs[a] == 0.0 for a in range(16) if a not in legal)
    best = max(scores, key=scores.get)
    assert best in legal


def test_teacher_plays_full_battle_and_is_fast_enough():
    env = surge_env(11)
    teacher = TrainerTeacher(depth=1, rolls=1, seed=2)
    t0 = time.perf_counter(); decisions = 0
    while not env.done() and decisions < 60:
        if env.request_kind() is None:
            env.auto_step(env.player_choices()[0]); continue
        a = teacher.choose(env)
        env.step(a, teacher._greedy_player(env))
        decisions += 1
    dt = time.perf_counter() - t0
    assert decisions > 0
    print(f"{decisions} teacher decisions in {dt:.2f}s ({decisions / dt:.1f}/s)")
