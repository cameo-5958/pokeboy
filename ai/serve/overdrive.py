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

from sim.battle import Battle, _parse_pokemon
from sim.reconstruct import battle_from_state, sample_opponent_team
from sim.schema import State
from sim.search import SearchTeacher, clone_raw


def full_info_state(b: Battle, player: int) -> State:
    """Player's state with the opponent fully revealed — used to value-score
    determinized children, where 'hidden' info was sampled by us anyway.
    Fully-revealed opponents also occur naturally late-game, so these states
    are in-distribution for the model."""
    st = b.state(player)
    opp = 2 - player  # side index of the other player
    mons = []
    for i in range(len(b.teams[opp])):
        p = _parse_pokemon(b.raw.bytes, opp, i)
        mons.append({
            "species": p["species"],
            "hp_fraction": round(p["hp"] / p["max_hp"], 4) if p["max_hp"] else 0.0,
            "status": p["status"],
            "revealed_moves": [m["id"] for m in p["moves"]],
            "fainted": p["fainted"],
        })
    st.opp_side = {"active_ix": st.opp_side["active_ix"], "pokemon": mons}
    return st


class OverdriveAgent:
    def __init__(self, ckpt: str, determinizations: int = 4, depth: int = 2,
                 rolls: int = 2, topk: int = 4, shortlist: int = 4,
                 seed: int = 0, device: str | None = None,
                 temperature: float = 0.25, mode: str = "teacher"):
        from models.agent import ModelAgent

        self.policy = ModelAgent(ckpt, seed=seed, device=device,
                                 temperature=temperature)
        self.teacher = SearchTeacher(depth=depth, rolls=rolls, topk=topk,
                                     seed=seed)
        self.k = determinizations
        self.rolls = rolls
        self.shortlist = shortlist
        self.mode = mode  # "teacher" (heuristic-leaf deep search) | "value"
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
        if self.mode == "value":
            return self._value_choose(st, cand, probs)
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

    def _value_choose(self, st: dict, cand: set[int], probs) -> int:
        """Depth-1 search with batched value-head leaves: expand each
        candidate x roll child (opponent plays the teacher's predicted
        reply) across all determinizations, score every child state with
        the model's value head in one batch, pick the best mean."""
        import torch

        from models.train_imitation import value_estimate

        from sim.engine import RESULT_NONE, RESULT_TIE, RESULT_WIN

        children: list[tuple[int, State]] = []  # (original action, child state)
        settled: dict[int, list[float]] = {}    # terminal children, hard values
        for _ in range(self.k):
            team = sample_opponent_team(st, self._rng)
            b, amap = battle_from_state(st, team, seed=self._rng.getrandbits(31))
            cmap = b._choice_map(0)
            reply = self.teacher._predict_reply(
                b.raw, 0, probe=next(iter(cmap.values())))
            for rec_a, choice in cmap.items():
                orig = amap.get(rec_a)
                if orig not in cand:
                    continue
                for _r in range(self.rolls):
                    child = clone_raw(b.raw, self._rng.randbytes(8))
                    child.update(choice, reply)
                    rt = child.result_type()
                    if rt != RESULT_NONE:
                        won = rt == RESULT_WIN  # player 1 == side 0 wins
                        v = 0.5 if rt == RESULT_TIE else (1.0 if won else 0.0)
                        settled.setdefault(orig, []).append(v)
                        continue
                    cb = object.__new__(Battle)
                    cb.__dict__.update(b.__dict__)
                    cb.raw = child
                    children.append((orig, full_info_state(cb, 1)))
        if not children and not settled:
            return max(cand, key=lambda a: float(probs[a]))

        totals = {a: list(v) for a, v in settled.items()}
        if children:
            import numpy as np

            tok, model = self.policy.tok, self.policy.model
            device = self.policy.device
            encs = [tok.encode(s.to_json()) for _, s in children]
            batch = dict(
                field_ids=torch.tensor(np.stack([e["field_ids"] for e in encs]),
                                       dtype=torch.long, device=device),
                value_ids=torch.tensor(np.stack([e["value_ids"] for e in encs]),
                                       dtype=torch.long, device=device),
                slot_ids=torch.tensor(np.stack([e["slot_ids"] for e in encs]),
                                      dtype=torch.long, device=device),
                cont=torch.tensor(np.stack([e["cont"] for e in encs]),
                                  dtype=torch.float32, device=device),
                lengths=torch.tensor([e["length"] for e in encs],
                                     dtype=torch.long, device=device),
            )
            with torch.inference_mode():
                _, vlogits = model(**batch, return_value=True)
            vals = value_estimate(vlogits.float().cpu())
            for (a, _), v in zip(children, vals.tolist()):
                totals.setdefault(a, []).append(v)
        return max(totals, key=lambda a: (sum(totals[a]) / len(totals[a]),
                                          float(probs[a])))
