"""ModelAgent: a checkpoint-backed seat for `python -m sim battle`.

Loads a train_imitation checkpoint, masks illegal actions, and *samples*
from the policy (SPECS §7) — sampling happens on CPU with an owned
generator so a seed fully determines the action sequence.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import torch

from models.encoder import FieldValueEncoder
from models.tiers import TIERS
from models.tokenizer import Tokenizer
from sim.schema import State


class ModelAgent:
    def __init__(
        self,
        checkpoint: str | Path,
        seed: int = 0,
        device: str | None = None,
        temperature: float = 1.0,
    ):
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.temperature = temperature
        ckpt = torch.load(checkpoint, map_location=self.device, weights_only=True)
        self.tok = Tokenizer(seq_len=ckpt.get("seq_len"), hist_k=ckpt.get("hist_k", 0))
        self.model = FieldValueEncoder(
            TIERS[ckpt["tier"]], self.tok, value_bins=ckpt.get("value_bins", 0)
        ).to(self.device)
        self.model.load_state_dict(ckpt["model"])
        self.model.eval()
        self._gen = torch.Generator().manual_seed(seed)

    @classmethod
    def from_model(
        cls,
        model,
        tokenizer: Tokenizer,
        seed: int = 0,
        device: str | None = None,
        temperature: float = 1.0,
    ) -> "ModelAgent":
        """Wrap an already-built model in place (no checkpoint round-trip);
        the caller keeps ownership of train/eval mode and device."""
        self = cls.__new__(cls)
        self.device = device or next(model.parameters()).device.type
        self.temperature = temperature
        self.tok = tokenizer
        self.model = model
        self._gen = torch.Generator().manual_seed(seed)
        return self

    def reseed(self, seed: int) -> None:
        """Reset the sampling stream (e.g. per battle when the agent is reused)."""
        self._gen = torch.Generator().manual_seed(seed)

    @torch.no_grad()
    def choose(self, state: State) -> int:
        enc = self.tok.encode(state.to_json())
        batch = dict(
            field_ids=torch.tensor(enc["field_ids"][None], dtype=torch.long, device=self.device),
            value_ids=torch.tensor(enc["value_ids"][None], dtype=torch.long, device=self.device),
            slot_ids=torch.tensor(enc["slot_ids"][None], dtype=torch.long, device=self.device),
            cont=torch.tensor(np.asarray(enc["cont"])[None], dtype=torch.float32, device=self.device),
            lengths=torch.tensor([enc["length"]], dtype=torch.long, device=self.device),
        )
        logits = self.model(**batch)[0].float().cpu()
        mask = torch.full_like(logits, float("-inf"))
        mask[state.legal_actions] = 0.0
        probs = torch.softmax(logits / self.temperature + mask, dim=-1)
        return int(torch.multinomial(probs, 1, generator=self._gen).item())
