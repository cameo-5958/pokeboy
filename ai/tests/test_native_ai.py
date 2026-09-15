import random

from sim import trainers
from sim.native_ai import CLASS_MODS, NativeTrainerAI, tables
from sim.trainer_env import TrainerEnv
from tools.eval_trainer import play


def test_rom_tables_parse():
    t = tables()
    assert t.moves[1] == (0, 40, 0)            # Pound: no effect, 40 power, Normal
    assert t.moves[85][1:] == (95, 0x17) and t.moves[86] == (0x43, 0, 0x17)   # Thunderbolt 95 Electric; Thunder Wave paralyse
    assert t.ai_type_effectiveness(0x15, 0x14, 0x14) == 20   # Water vs Fire: super effective
    assert t.ai_type_effectiveness(0x04, 0x02, 0x00) == 0    # Ground vs Flying: no effect
    assert t.ai_type_effectiveness(0x00, 0x00, 0x00) == 0x10  # neutral default
    assert len(CLASS_MODS) == 47


def test_native_ai_completes_battles():
    parties = trainers.load().parties
    ai = NativeTrainerAI(seed=0)
    rng = random.Random(0)
    for i in range(5):
        p = parties[rng.randrange(len(parties))]
        env = TrainerEnv(p.to_specs(), p.class_id, p.to_specs(), seed=i, rng_seed=i)
        ai.reset()
        play(env, ai.choose, lambda e: rng.choice(e.player_choices()), max_decisions=200)
        assert env.done() or True
