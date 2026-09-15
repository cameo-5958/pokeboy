"""Play the *player* seat of a TrainerEnv with the trainer-seat network (league self-play).

The network only ever sees a trainer-seat observation: own party in full, the other side
as public information. `player_view` builds exactly that observation from the player's
side of the same libpkmn battle (shared buffer, swapped sides, no item class), so the same
weights can hold either seat. The player has no items: trainer class 0 masks the item
candidates and carries the "no class" embedding. Move / switch actions map back to engine
choices through the env's own choice lookup.
"""
from __future__ import annotations

import copy

import numpy as np
import torch

from models.pep import N_ACTIONS, PEP, features_to_tensors
from models.pep_data import decode_features
from sim.trainer_env import EVENT_DIM, TrainerEnv


def player_view(env: TrainerEnv) -> TrainerEnv:
    """A TrainerEnv whose "trainer" is the player side of `env` (shares the battle object)."""
    v = copy.copy(env)
    v.me, v.opp = env.opp, env.me
    v.trainer_specs, v.player_specs = env.player_specs, env.trainer_specs
    v.trainer_class = 0
    v.item, v.item_divisor, v.item_status = None, 0, 0
    v.count_max = v.count = 0
    v.snapshots = {}
    v.last_active = v._active_ix(v.me)
    v.last_event = [0.0] * EVENT_DIM
    return v


def swap_event(ev, action_class: int | None) -> np.ndarray:
    """The trainer-seat event vector seen from the other side: sides swapped, own action class replaced."""
    out = np.zeros(EVENT_DIM, np.float32)
    if action_class is not None:
        out[action_class] = 1.0
    out[3:9] = ev[9:15]
    out[9:15] = ev[3:9]
    out[15] = ev[15]
    return out


class NetPlayer:
    """Player-seat policy `choose(env) -> engine choice` backed by a PEP model (GRU state per battle)."""

    def __init__(self, model: PEP, seed: int = 0, temperature: float = 1.0):
        self.model = model
        self.temperature = temperature
        self.gen = torch.Generator(device="cpu").manual_seed(seed)
        self.h: torch.Tensor | None = None
        self._battle_key = None
        self._view: TrainerEnv | None = None
        self._last_class: int | None = None

    def _sync(self, env: TrainerEnv) -> TrainerEnv:
        key = env.b.battle_id
        if key != self._battle_key or self._view is None:
            self._battle_key = key; self.h = None; self._last_class = None
            self._view = player_view(env)
        v = self._view
        v.round = env.round
        v._observe_player()
        return v

    @torch.no_grad()
    def choose(self, env: TrainerEnv) -> int:
        v = self._sync(env)
        kind = v.request_kind()
        if kind is None:
            self._last_class = None
            return v.auto_choice()
        feats, mask, _ = v.features()
        arrays = decode_features(bytes(feats))
        tensors = features_to_tensors({k: np.expand_dims(a, 0) for k, a in arrays.items()})
        tensors["legal"] = torch.tensor([mask], dtype=torch.int64)
        ev = swap_event(env.last_event, self._last_class)
        logits, _z, self.h = self.model(tensors, torch.from_numpy(ev)[None], self.h)
        logits = logits[0].float()
        legal = [a for a in range(10) if mask >> a & 1]
        if not legal:
            return v.auto_choice()
        if self.temperature <= 0:
            a = max(legal, key=lambda i: float(logits[i]))
        else:
            probs = torch.softmax(logits / self.temperature, dim=-1)
            probs = torch.nan_to_num(probs, nan=0.0)
            probs[[i for i in range(N_ACTIONS) if i not in legal]] = 0.0
            a = legal[0] if float(probs.sum()) <= 0 else int(torch.multinomial(probs, 1, generator=self.gen))
        self._last_class = 0 if a < 4 else 1
        c = v._engine_choice(a)
        return v.auto_choice() if c is None else c


class SearchPlayer:
    """Player-seat policy `choose(env) -> engine choice` backed by the depth-limited search
    teacher, played on the player's side via `player_view`.

    The `greedy` opponent is a 1-ply engine choice; this is the same search that produced the
    imitation corpus, so a league trained against it faces a materially stronger seat. Cost
    scales with depth*rolls: depth 2 is roughly 5x a greedy rollout."""

    def __init__(self, depth: int = 2, rolls: int = 2, topk: int = 3, alpha: float = 0.3,
                 seed: int = 0):
        from sim.trainer_search import TrainerTeacher

        self.teacher = TrainerTeacher(depth=depth, rolls=rolls, topk=topk, alpha=alpha, seed=seed)
        self._battle_key = None
        self._view: TrainerEnv | None = None

    def _sync(self, env: TrainerEnv) -> TrainerEnv:
        key = env.b.battle_id
        if key != self._battle_key or self._view is None:
            self._battle_key = key
            self._view = player_view(env)
        v = self._view
        v.round = env.round
        v._observe_player()
        return v

    def choose(self, env: TrainerEnv) -> int:
        v = self._sync(env)
        if v.request_kind() is None:
            return v.auto_choice()
        c = v._engine_choice(self.teacher.choose(v))
        return v.auto_choice() if c is None else c
