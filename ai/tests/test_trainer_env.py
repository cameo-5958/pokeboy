"""Trainer-seat environment: observation/feature parity plumbing, 16-way actions, items."""
import random

import pytest

from sim.pack import PokemonSpec
from sim.trainer_env import TrainerEnv

pytest.importorskip("pkai")

TRAINER_DVS = (8, 9, 8, 8, 8)
LT_SURGE, BROCK, YOUNGSTER = 36, 34, 1


def first_move(env):
    from sim import engine
    return next(c for c in env.player_choices() if engine.choice_type(c) == engine.MOVE)


def surge_env(seed=1):
    trainer = [PokemonSpec("Voltorb", ["Tackle", "Screech"], level=21, dvs=TRAINER_DVS, statexp=(0,) * 5),
               PokemonSpec("Raichu", ["Thunderbolt", "Thunder Wave", "Quick Attack", "Growl"], level=24, dvs=TRAINER_DVS, statexp=(0,) * 5)]
    player = [PokemonSpec("Sandshrew", ["Scratch", "Sand Attack"], level=22, dvs=(8, 8, 8, 8, 8), statexp=(1000,) * 5),
              PokemonSpec("Pidgeotto", ["Gust", "Quick Attack"], level=20, dvs=(8, 8, 8, 8, 8), statexp=(1000,) * 5)]
    return TrainerEnv(trainer, LT_SURGE, player, seed=seed, rng_seed=seed)


def test_observation_and_mask():
    env = surge_env()
    feats, mask, kind = env.features()
    assert kind == 0 and feats.count == 27
    assert mask & 0b11 == 0b11 and not mask & 0b1100          # Voltorb's two moves
    assert mask >> 5 & 1 and not mask >> 4 & 1                 # switch to Raichu legal, not to itself
    assert mask >> 15 & 1                                      # Lt. Surge offers X Speed with count 1
    obs = env.observation()
    assert obs.trainer_class == LT_SURGE and obs.count == 1 and obs.own_count == 2 and obs.player_count == 2
    assert obs.player[0].known and not obs.player[1].known      # only the player's lead is revealed
    assert obs.player[0].moves[0] == 0                          # no move used yet
    assert obs.active.hp == obs.active.max_hp > 0


def test_x_speed_item_turn():
    env = surge_env()
    spe_before = env.observation().active.stats[2]
    env.step(15, first_move(env))                                # X Speed; player uses a move
    obs = env.observation()
    assert obs.stages[2] == 8 and obs.active.stats[2] > spe_before
    assert obs.count == 0
    _, mask, _ = env.features()
    assert not mask >> 15 & 1                                   # count exhausted until the next send-out
    assert obs.player[0].moves[0] != 0                          # the player's move got revealed


def test_random_battle_to_completion_with_items():
    rng = random.Random(3)
    for seed in range(4):
        env = surge_env(seed)
        steps = 0
        while not env.done() and steps < 300:
            kind = env.request_kind()
            if kind is None:
                env.auto_step(env.player_choices()[0]); continue
            legal = env.legal_actions()
            assert legal, "no legal action"
            env.step(rng.choice(legal), rng.choice(env.player_choices()))
            steps += 1
        assert env.done()
        assert env.winner_is_trainer() in (True, False, None)


def test_clone_is_independent():
    env = surge_env()
    c = env.clone(bytes(range(1, 9)))
    c.step(0, c.player_choices()[0])
    assert env.round == 0 and c.round == 1
    assert env.observation().active.hp == env.observation().active.max_hp
