"""Play the trainer seat of a TrainerEnv with a PEP checkpoint (fp32 reference policy)."""
from __future__ import annotations

import numpy as np
import torch

from models.pep import PEP, features_to_tensors, load_checkpoint
from models.pep_data import decode_features
from sim.trainer_env import TrainerEnv


class PEPAgent:
    def __init__(self, checkpoint: str | None = None, model: PEP | None = None, temperature: float = 0.5,
                 seed: int = 0, device: str = "cpu"):
        if model is None:
            if checkpoint is None:
                raise ValueError("checkpoint or model required")
            loaded = load_checkpoint(checkpoint, map_location=device)
            model = loaded[0] if isinstance(loaded, tuple) else loaded
        self.model = model.to(device).eval()
        self.temperature = temperature
        self.device = device
        self.gen = torch.Generator(device="cpu").manual_seed(seed)
        self.h: torch.Tensor | None = None
        self._battle_key = None

    def reset(self) -> None:
        self.h = None

    @torch.no_grad()
    def choose(self, env: TrainerEnv) -> int:
        """16-way action for the pending trainer decision; carries the GRU state across the battle."""
        key = env.b.battle_id
        if key != self._battle_key:
            self._battle_key = key; self.h = None
        feats, mask, _kind = env.features()
        arrays = decode_features(bytes(feats))
        batch = {k: np.expand_dims(v, 0) for k, v in arrays.items()}
        tensors = features_to_tensors(batch, device=self.device)
        tensors["legal"] = torch.tensor([mask], dtype=torch.int64, device=self.device)
        ev = torch.tensor(np.asarray(env.last_event, dtype=np.float32)[None], device=self.device)
        logits, _value, self.h = self.model(tensors, ev, self.h)
        logits = logits[0].float()
        legal = [a for a in range(16) if mask >> a & 1]
        if self.temperature <= 0:
            return max(legal, key=lambda a: float(logits[a]))
        probs = torch.softmax(logits / self.temperature, dim=-1)
        probs = torch.nan_to_num(probs, nan=0.0)
        if float(probs.sum()) <= 0:
            return legal[0]
        return int(torch.multinomial(probs.cpu(), 1, generator=self.gen))
