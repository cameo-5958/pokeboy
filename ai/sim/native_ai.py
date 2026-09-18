"""The cartridge's own trainer AI (pokered engine/battle/trainer_ai.asm) as a TrainerEnv policy.

Reimplemented from the disassembly, table data read from the loaded ROM (Moves, TypeEffects)
and the two per-class tables transcribed from data/trainers/move_choices.asm and
data/trainers/ai_pointers.asm + the class routines in trainer_ai.asm.

Move choice (AIEnemyTrainerChooseMoves): every move starts at score 10 (the disabled slot at
0x50); the class's modification list adjusts scores; the move is drawn uniformly among the
minimum-score slots.
  1. player's active has a status -> +5 to power-0 moves with a sleep/poison/paralyse effect;
  2. exactly one move executed since send-out -> -1 to stat-up-ish effects
     (ATTACK_UP1..BIDE-1, ATTACK_UP2..POISON-1);
  3. AIGetTypeEffectiveness (first matching TypeEffects row only, neutral default 0x10, the
     known bug): >0x10 -> -1; <0x10 -> +1 if a "better" move exists (Super Fang / special
     damage / Fly, or a damaging move of another type).
Items and switches (TrainerAI): consulted once per turn while wAICount != 0, gated by a
random byte and the class routine; switch target is the lowest-index unfainted party slot
other than the active one (EnemySendOut order).

Known divergences (recorded for the paper): the ROM evaluates TrainerAI when the enemy's
turn executes, i.e. after the player's move when the player is faster, so its HP checks see
this turn's damage; here the check runs on pre-turn HP. The layer-2 counter counts this
policy's own move selections (forced moves the ROM executes without asking are not counted).
"""
from __future__ import annotations

import random

from sim.pkai_bridge import (FULL_HEAL, FULL_RESTORE, GUARD_SPEC, HYPER_POTION, ITEM_SLOT, POTION, SUPER_POTION,
                             X_ATTACK, X_DEFEND, X_SPEED, bridge)

# data/trainers/move_choices.asm, class id -> modification list (1-based class ids as in the ROM).
CLASS_MODS = {
    1: (), 2: (1,), 3: (1,), 4: (1, 3), 5: (1,), 6: (1,), 7: (1, 2, 3), 8: (1, 2), 9: (1,), 10: (1,),
    11: (1, 3), 12: (1,), 13: (1, 2), 14: (1, 3), 15: (1, 3), 16: (), 17: (1,), 18: (1, 3), 19: (1, 2),
    20: (1, 3), 21: (1,), 22: (1,), 23: (1,), 24: (1,), 25: (1,), 26: (1, 3), 27: (1, 2), 28: (1, 2),
    29: (1, 3), 30: (1,), 31: (1, 3), 32: (1, 3), 33: (1,), 34: (1,), 35: (1, 3), 36: (1, 3), 37: (1, 3),
    38: (1, 3), 39: (1, 3), 40: (1, 3), 41: (1, 2), 42: (1, 3), 43: (1, 3), 44: (1, 2, 3), 45: (1,),
    46: (1,), 47: (1, 3),
}
# constants/move_effect_constants.asm
EFFECT_01, ATTACK_UP1, BIDE, SLEEP, SUPER_FANG, SPECIAL_DAMAGE, FLY, ATTACK_UP2, POISON, PARALYZE = (
    0x01, 0x0A, 0x1A, 0x20, 0x28, 0x29, 0x2B, 0x32, 0x42, 0x43)
STATUS_AILMENT_EFFECTS = {EFFECT_01, SLEEP, POISON, PARALYZE}
# ROM addresses (pkai/generated/rom_tables.h): bank, address.
_MOVES = (14, 0x4000)
_TYPE_EFFECTS = (15, 0x64AE)


def _file_offset(bank: int, addr: int) -> int:
    return bank * 0x4000 + (addr - 0x4000)


class RomTables:
    def __init__(self, rom: bytes):
        o = _file_offset(*_MOVES)
        # move id -> (effect, power, type)
        self.moves = {i: (rom[o + 6 * (i - 1) + 1], rom[o + 6 * (i - 1) + 2], rom[o + 6 * (i - 1) + 3])
                      for i in range(1, 166)}
        self.type_effects: list[tuple[int, int, int]] = []
        o = _file_offset(*_TYPE_EFFECTS)
        while rom[o] != 0xFF:
            self.type_effects.append((rom[o], rom[o + 1], rom[o + 2])); o += 3

    def ai_type_effectiveness(self, move_type: int, t1: int, t2: int) -> int:
        """AIGetTypeEffectiveness: first matching row wins, default 0x10 (the ROM's bug)."""
        for atk, dfn, mult in self.type_effects:
            if atk == move_type and (dfn == t1 or dfn == t2):
                return mult
        return 0x10


_tables: RomTables | None = None


def tables() -> RomTables:
    global _tables
    if _tables is None:
        _tables = RomTables(bridge().rom)
    return _tables


class NativeTrainerAI:
    """Trainer-seat policy `choose(env) -> 16-way action` with the cartridge's decision rules."""

    def __init__(self, seed: int = 0):
        self.rng = random.Random(seed)
        self._battle_key = None
        self._active = -1
        self._moves_executed = 0   # wAILayer2Encouragement

    def reset(self) -> None:
        self._battle_key = None

    def move_scores(self, obs, t: RomTables) -> list[int]:
        scores = [10] * 4
        if obs.disabled:
            scores[obs.disabled - 1] = 0x50
        moves = [obs.active.moves[i] for i in range(4)]
        pa = obs.player[obs.player_slot]
        for mod in CLASS_MODS.get(obs.trainer_class, ()):
            if mod == 1 and pa.status != 0:
                for i, m in enumerate(moves):
                    if not m:
                        break
                    effect, power, _ = t.moves[m]
                    if power == 0 and effect in STATUS_AILMENT_EFFECTS:
                        scores[i] += 5
            elif mod == 2 and self._moves_executed == 1:
                for i, m in enumerate(moves):
                    if not m:
                        break
                    effect = t.moves[m][0]
                    if ATTACK_UP1 <= effect < BIDE or ATTACK_UP2 <= effect < POISON:
                        scores[i] -= 1
            elif mod == 3:
                for i, m in enumerate(moves):
                    if not m:
                        break
                    _, _, mtype = t.moves[m]
                    eff = t.ai_type_effectiveness(mtype, pa.types[0], pa.types[1])
                    if eff == 0x10:
                        continue
                    if eff > 0x10:
                        scores[i] -= 1
                        continue
                    better = False
                    for m2 in moves:
                        if not m2:
                            break
                        e2, p2, t2 = t.moves[m2]
                        if e2 in (SUPER_FANG, SPECIAL_DAMAGE, FLY) or (t2 != mtype and p2 != 0):
                            better = True
                            break
                    if better:
                        scores[i] += 1
        return scores

    def choose_move(self, obs, legal: int, t: RomTables) -> int:
        moves = [obs.active.moves[i] for i in range(4)]
        if not CLASS_MODS.get(obs.trainer_class, ()):
            cands = [i for i in range(4) if moves[i] and legal >> i & 1]
        else:
            scores = self.move_scores(obs, t)
            present = [i for i in range(4) if moves[i]]
            lo = min(scores[i] for i in present) if present else 0
            cands = [i for i in present if scores[i] == lo and legal >> i & 1]
        if not cands:
            cands = [i for i in range(4) if legal >> i & 1] or [0]
        return self.rng.choice(cands)

    def trainer_ai(self, obs, legal: int) -> int | None:
        """The class routine: an item action (10..15), a switch action (4..9) or None."""
        if obs.count == 0:
            return None
        r = self.rng.randrange(256)
        hp, mx, st = obs.active.hp, obs.active.max_hp, obs.active.status
        below = lambda a: hp < mx // a
        c = obs.trainer_class
        item = None; switch = False
        if c in (13, 21) and r < 65: switch = True
        elif c == 24 and r < 32: item = X_ATTACK
        elif c == 29 and r < 65: item = GUARD_SPEC
        elif c == 31 and r < 65: item = X_ATTACK
        elif c == 32:
            if below(10): item = HYPER_POTION
            elif below(5): switch = True
        elif c == 33 and r < 65: item = X_DEFEND
        elif c == 34 and st: item = FULL_HEAL
        elif c == 35 and r < 65: item = X_DEFEND
        elif c == 36 and r < 65: item = X_SPEED
        elif c == 37 and r < 129 and below(10): item = SUPER_POTION
        elif c == 38 and r < 65: item = X_ATTACK
        elif c == 39 and r < 65: item = SUPER_POTION
        elif c == 40 and r < 65 and below(10): item = HYPER_POTION
        elif c == 42 and r < 32 and below(5): item = POTION
        elif c == 43 and r < 32 and below(5): item = FULL_RESTORE
        elif c == 44 and r < 129 and below(5): item = SUPER_POTION
        elif c == 46:
            if r < 20: switch = True
            elif r < 129 and below(4): item = SUPER_POTION
        elif c == 47 and r < 129 and below(5): item = HYPER_POTION
        if item is not None:
            slot = ITEM_SLOT[item]
            return slot if legal >> slot & 1 else None
        if switch:
            return self.switch_target(obs, legal)
        return None

    @staticmethod
    def switch_target(obs, legal: int) -> int | None:
        for i in range(obs.own_count):
            if i != obs.own_slot and obs.own[i].hp and legal >> (4 + i) & 1:
                return 4 + i
        return None

    def choose(self, env) -> int:
        key = env.b.battle_id
        if key != self._battle_key:
            self._battle_key = key; self._active = -1; self._moves_executed = 0
        obs = env.observation()
        _feats, legal, kind = env.features()
        if obs.own_slot != self._active:
            self._active = obs.own_slot; self._moves_executed = 0
        if kind == 1:
            a = self.switch_target(obs, legal)
            return a if a is not None else [i for i in range(16) if legal >> i & 1][0]
        a = self.trainer_ai(obs, legal)
        if a is not None:
            return a
        self._moves_executed += 1
        return self.choose_move(obs, legal, tables())
