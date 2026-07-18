"""Search overdrive: model-guided expectation search at serve time.

For each decision the policy shortlists candidate actions, the state is
determinized K times (sample_opponent_team fills hidden opponent info from
the benchmark team pools), a SearchTeacher scores the shortlist on each
reconstructed engine battle, and the action with the best average score
wins — policy probability breaks ties. This is the foul-play recipe with a
much faster engine and a learned prior: search quality where the policy is
unsure, policy quality where search is blind (volatiles, long-term plans).

Any failure — reconstruction, search, odd species — falls back to the raw
policy, so overdrive is never worse than the model it wraps at stability.
"""

from __future__ import annotations

import random

from sim.reconstruct import battle_from_state, sample_opponent_team
from sim.schema import State
from sim.search import SearchTeacher


class OverdriveAgent:
    def __init__(self, ckpt: str, determinizations: int = 4, depth: int = 2,
                 rolls: int = 2, topk: int = 4, shortlist: int = 4,
                 seed: int = 0, device: str | None = None,
                 temperature: float = 0.25):
        from models.agent import ModelAgent

        self.policy = ModelAgent(ckpt, seed=seed, device=device,
                                 temperature=temperature)
        self.teacher = SearchTeacher(depth=depth, rolls=rolls, topk=topk,
                                     seed=seed)
        self.k = determinizations
        self.shortlist = shortlist
        self._rng = random.Random(seed)

    def reseed(self, seed: int) -> None:
        self.policy.reseed(seed)
        self._rng = random.Random(seed)

    def choose(self, state: State) -> int:
        legal = list(state.legal_actions)
        if len(legal) <= 1:
            return legal[0] if legal else 9
        try:
            return self._search_choose(state)
        except Exception:
            return self.policy.choose(state)

    def _search_choose(self, state: State) -> int:
        probs = self.policy.action_probs(state)
        ranked = sorted(state.legal_actions, key=lambda a: -float(probs[a]))
        cand = set(ranked[: max(2, self.shortlist)])

        st = state.to_json() if hasattr(state, "to_json") else dict(state)
        totals: dict[int, float] = {a: 0.0 for a in cand}
        counted = 0
        for _ in range(self.k):
            team = sample_opponent_team(st, self._rng)
            b, amap = battle_from_state(st, team, seed=self._rng.getrandbits(31))
            recon_cand = {rec for rec, orig in amap.items() if orig in cand}
            scores = self.teacher.action_scores(b, 1, actions=recon_cand)
            for rec_a, s in scores.items():
                orig = amap.get(rec_a)
                if orig in totals:
                    totals[orig] += s
            counted += 1
        if not counted:
            return self.policy.choose(state)
        # best mean search value; policy probability breaks ties
        return max(totals, key=lambda a: (totals[a], float(probs[a])))
