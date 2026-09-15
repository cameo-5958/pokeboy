"""Full-information search teacher over the trainer seat's 16-way actions.

Same shape as sim.search.SearchTeacher (depth-limited, expectation over
re-rolled RNG seeds, alpha blend of worst-case and predicted player reply),
but it scores TrainerEnv actions — including item classes — by stepping
cloned environments, so item turns follow the same ordering and effect rules
the data will be trained on. Full information is legal here: the teacher only
produces demonstrations; the student consumes public-info features.
"""
from __future__ import annotations

import math
import random

from sim import engine
from sim.engine import RESULT_NONE, RESULT_TIE, RESULT_WIN
from sim.search import _TERMINAL, leaf_value
from sim.trainer_env import TrainerEnv


class TrainerTeacher:
    def __init__(self, depth: int = 1, rolls: int = 2, topk: int = 3, alpha: float = 0.3, seed: int = 0):
        if depth < 1 or rolls < 1 or topk < 1 or not 0.0 <= alpha <= 1.0:
            raise ValueError("bad teacher parameters")
        self.depth, self.rolls, self.topk, self.alpha = depth, rolls, topk, alpha
        self._rng = random.Random(seed)

    def reseed(self, seed: int) -> None:
        self._rng = random.Random(seed)

    # -- public API -----------------------------------------------------------
    def choose(self, env: TrainerEnv) -> int:
        scores = self.action_scores(env)
        return min(scores, key=lambda a: (-scores[a], a))

    def policy(self, env: TrainerEnv, temperature: float = 0.25) -> tuple[list[float], dict[int, float]]:
        """Softmax over action scores (leaf-value units) as a 16-way distribution."""
        scores = self.action_scores(env)
        best = max(scores.values())
        probs = [0.0] * 16
        z = 0.0
        for a, s in scores.items():
            w = math.exp(max(-60.0, (s - best) / temperature))
            probs[a] = w; z += w
        return [p / z for p in probs], scores

    def action_scores(self, env: TrainerEnv, actions: set[int] | None = None) -> dict[int, float]:
        legal = env.legal_actions()
        if actions is not None:
            legal = [a for a in legal if a in actions] or legal
        if len(legal) == 1:
            return {legal[0]: 0.0}
        scores = self._root_scores(env, legal, depth=1)
        if self.depth >= 2:
            top = sorted(scores, key=lambda a: scores[a], reverse=True)[: self.topk]
            scores.update(self._root_scores(env, top, depth=self.depth))
        return scores

    # -- internals --------------------------------------------------------------
    def _advance_to_decision(self, env: TrainerEnv, depth: int) -> None:
        """Play out updates where only the player acts (e.g. their replacement after our KO)."""
        while not env.done() and env.request_kind() is None:
            env.auto_step(self._greedy_player(env))

    def _pair_value(self, env: TrainerEnv, action: int, oc: int, depth: int, rolls: int) -> float:
        total = 0.0
        for _ in range(rolls):
            child = env.clone(self._rng.randbytes(8))
            child.step(action, oc)
            total += self._value(child, depth)
        return total / rolls

    def _greedy_player(self, env: TrainerEnv) -> int:
        """The player's own 1-ply greedy engine choice (their leaf value), probed against our PASS
        when no trainer decision is pending, otherwise against our first legal action."""
        choices = env.player_choices()
        if len(choices) == 1:
            return choices[0]
        pending = env.request_kind() is not None
        probe = env.legal_actions()[0] if pending else None
        best, best_v = choices[0], -float("inf")
        for oc in choices:
            child = env.clone(self._rng.randbytes(8))
            if pending:
                child.step(probe, oc)
            else:
                child.auto_step(oc)
            v = leaf_value(child.buf, env.opp)
            if v > best_v:
                best, best_v = oc, v
        return best

    def _root_scores(self, env: TrainerEnv, actions: list[int], depth: int) -> dict[int, float]:
        predicted = self._greedy_player(env)
        opp_choices = env.player_choices()
        scores: dict[int, float] = {}
        for a in actions:
            expected = self._pair_value(env, a, predicted, depth - 1, self.rolls)
            if self.alpha > 0.0 and len(opp_choices) > 1:
                worst = min(self._pair_value(env, a, oc, depth - 1, self.rolls) for oc in opp_choices)
            else:
                worst = expected
            scores[a] = self.alpha * worst + (1.0 - self.alpha) * expected
        return scores

    def _value(self, env: TrainerEnv, depth: int) -> float:
        rt = env.b.raw.result_type()
        if rt != RESULT_NONE:
            if rt == RESULT_TIE:
                return 0.0
            won = (rt == RESULT_WIN) == (env.me == 0)
            return _TERMINAL if won else -_TERMINAL
        self._advance_to_decision(env, depth)
        if env.done():
            return self._value(env, depth)
        if depth <= 0:
            return leaf_value(env.buf, env.me)
        predicted = self._greedy_player(env)
        return max(self._pair_value(env, a, predicted, depth - 1, rolls=1) for a in env.legal_actions())
