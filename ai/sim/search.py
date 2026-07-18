"""SearchTeacher: depth-limited expectation-aware maximin search over the
engine (SPECS §5.1 synthetic teacher).

Full information by design: the teacher exists to generate demonstrations,
where seeing both sides is legal (SPECS §4.3 asymmetric-information trick) —
the distilled student only ever consumes public tokens. Every candidate
action pair is stepped on cloned battle buffers; the in-buffer RNG seed
(bytes 376-384) is re-rolled per sample so pair values are expectations over
damage rolls/crits/procs, not single trajectories. Opponent reply is scored
maximin (worst case for us).
"""

from __future__ import annotations

import random

from sim.battle import _POKE, _SIDE, Battle
from sim.engine import RESULT_NONE, RESULT_TIE, RESULT_WIN, RawBattle

_SEED_OFF = 376
_TERMINAL = 1000.0


def clone_raw(raw: RawBattle, seed_bytes: bytes) -> RawBattle:
    """Independent copy of a battle with its internal RNG re-seeded."""
    buf = raw.bytes
    clone = RawBattle(buf[:_SEED_OFF] + seed_bytes)
    clone.last_result = raw.last_result
    return clone


# fraction of a mon's aliveness value lost to each major status
_STATUS_DISCOUNT = {0x20: 0.5, 0x40: 0.15, 0x10: 0.12, 0x08: 0.08}  # FRZ, PAR, BRN, PSN


def _side_score(buf: bytes, side: int) -> float:
    hp_sum, alive = 0.0, 0.0
    for ix in range(6):
        o = _SIDE[side] + ix * _POKE
        hp_max = int.from_bytes(buf[o : o + 2], "little")
        if not hp_max:
            continue  # empty party slot
        hp = int.from_bytes(buf[o + 18 : o + 20], "little")
        hp_sum += hp / hp_max
        if hp:
            status = buf[o + 20]
            worth = 1.0
            if status & 0x07:  # sleep counter bits
                worth -= 0.5
            else:
                for bit, cost in _STATUS_DISCOUNT.items():
                    if status & bit:
                        worth -= cost
                        break
            alive += worth
    return hp_sum + 2.0 * alive


def leaf_value(buf: bytes, me: int) -> float:
    return _side_score(buf, me) - _side_score(buf, 1 - me)


class SearchTeacher:
    """Seat with full-info `choose_full(battle, player)`.

    Simultaneous-move handling: pure maximin assumes the opponent counters
    my exact move — over-pessimistic (measured: depth-2 maximin only ties
    MaxDamage). Instead the opponent's reply is *predicted* with their own
    1-ply greedy calculation, and each root action is scored as a blend of
    worst-case and predicted-case: alpha*min_reply + (1-alpha)*predicted.
    Depth >= 2 re-expands the top-k root actions another turn using the
    predicted opponent line."""

    def __init__(
        self,
        depth: int = 1,
        rolls: int = 2,
        topk: int = 3,
        alpha: float = 0.3,
        seed: int = 0,
    ):
        if depth < 1 or rolls < 1 or topk < 1:
            raise ValueError("depth, rolls and topk must be >= 1")
        if not 0.0 <= alpha <= 1.0:
            raise ValueError("alpha must be in [0, 1]")
        self.depth = depth
        self.rolls = rolls
        self.topk = topk
        self.alpha = alpha
        self._rng = random.Random(seed)

    def reseed(self, seed: int) -> None:
        self._rng = random.Random(seed)

    def choose_full(self, b: Battle, player: int) -> int:
        scores = self.action_scores(b, player)
        # deterministic tie-break: best score, then lowest action id
        return min(scores, key=lambda a: (-scores[a], a))

    def action_scores(self, b: Battle, player: int,
                      actions: set[int] | None = None) -> dict[int, float]:
        """Root action values; optionally restricted to a candidate subset
        (an empty intersection falls back to all legal actions)."""
        me = player - 1
        cmap = b._choice_map(me)
        if actions is not None:
            cmap = {a: c for a, c in cmap.items() if a in actions} or cmap
        if len(cmap) == 1:
            return {next(iter(cmap)): 0.0}
        scores = self._root_scores(b.raw, me, cmap, depth=1)
        if self.depth >= 2:
            top = sorted(scores, key=lambda a: scores[a], reverse=True)[: self.topk]
            deep = self._root_scores(b.raw, me, {a: cmap[a] for a in top}, depth=self.depth)
            scores.update(deep)
        return scores

    # -- internals --

    def _pair_value(self, raw: RawBattle, me: int, mc: int, oc: int, depth: int, rolls: int) -> float:
        total = 0.0
        for _ in range(rolls):
            child = clone_raw(raw, self._rng.randbytes(8))
            c1, c2 = (mc, oc) if me == 0 else (oc, mc)
            child.update(c1, c2)
            total += self._value(child, me, depth)
        return total / rolls

    def _predict_reply(self, raw: RawBattle, me: int, probe: int) -> int:
        """The opponent's own 1-ply greedy choice (their damage calc), probed
        against an arbitrary fixed action of mine — a simultaneous-move reply
        depends only weakly on my concurrent choice."""
        opp = 1 - me
        opp_choices = raw.choices(opp, raw.requests()[opp]) or [0]
        if len(opp_choices) == 1:
            return opp_choices[0]
        best, best_v = opp_choices[0], -float("inf")
        for oc in opp_choices:
            v = self._pair_value(raw, opp, oc, probe, depth=0, rolls=1)
            if v > best_v:
                best, best_v = oc, v
        return best

    def _root_scores(self, raw: RawBattle, me: int, cmap: dict[int, int], depth: int) -> dict[int, float]:
        opp = 1 - me
        opp_choices = raw.choices(opp, raw.requests()[opp]) or [0]
        predicted = self._predict_reply(raw, me, probe=next(iter(cmap.values())))
        scores: dict[int, float] = {}
        for action, choice in cmap.items():
            expected = self._pair_value(raw, me, choice, predicted, depth - 1, self.rolls)
            if self.alpha > 0.0:
                worst = min(
                    self._pair_value(raw, me, choice, oc, depth - 1, self.rolls)
                    for oc in opp_choices
                )
            else:
                worst = expected
            scores[action] = self.alpha * worst + (1.0 - self.alpha) * expected
        return scores

    def _value(self, raw: RawBattle, me: int, depth: int) -> float:
        rt = raw.result_type()
        if rt != RESULT_NONE:
            if rt == RESULT_TIE:
                return 0.0
            won = (rt == RESULT_WIN) == (me == 0)
            return _TERMINAL if won else -_TERMINAL
        if depth <= 0:
            return leaf_value(raw.bytes, me)
        # follow the predicted opponent line; single roll per node keeps the
        # tree tractable — root-level rolls average over the stochasticity
        my_choices = raw.choices(me, raw.requests()[me]) or [0]
        predicted = self._predict_reply(raw, me, probe=my_choices[0])
        return max(
            self._pair_value(raw, me, mc, predicted, depth - 1, rolls=1) for mc in my_choices
        )
