"""Encoder transformer over field-value tokens (ai/SPECS.md §4.2, policy head
only for the imitation shakedown; value/belief/opp-policy heads come with RL).

embedding = E_field + E_value + E_slot + W_cont·cont ; bidirectional encoder
(no positional encoding — structure lives in field/slot embeddings); a learned
[ACT] query token is prepended and its output feeds the policy head.
"""

from __future__ import annotations

import torch
import torch.nn as nn

from models.tiers import TierConfig
from models.tokenizer import Tokenizer

N_ACTIONS = 10


class FieldValueEncoder(nn.Module):
    def __init__(self, cfg: TierConfig, tokenizer: Tokenizer | None = None):
        super().__init__()
        tok = tokenizer or Tokenizer()
        d = cfg.d_model
        self.e_field = nn.Embedding(tok.n_fields, d)
        self.e_value = nn.Embedding(tok.n_values, d)
        self.e_slot = nn.Embedding(tok.n_slots, d)
        self.w_cont = nn.Linear(tok.n_cont, d)
        self.act_query = nn.Parameter(torch.randn(1, 1, d) * 0.02)
        layer = nn.TransformerEncoderLayer(
            d_model=d,
            nhead=cfg.heads,
            dim_feedforward=cfg.ffn,
            activation="gelu",
            batch_first=True,
            norm_first=True,
            dropout=0.0,
        )
        self.encoder = nn.TransformerEncoder(layer, num_layers=cfg.layers)
        self.policy = nn.Sequential(nn.LayerNorm(d), nn.Linear(d, d), nn.GELU(), nn.Linear(d, N_ACTIONS))

    def forward(
        self,
        field_ids: torch.Tensor,  # (B, L) int
        value_ids: torch.Tensor,
        slot_ids: torch.Tensor,
        cont: torch.Tensor,  # (B, L, C) float
        lengths: torch.Tensor,  # (B,) int
    ) -> torch.Tensor:
        x = (
            self.e_field(field_ids)
            + self.e_value(value_ids)
            + self.e_slot(slot_ids)
            + self.w_cont(cont)
        )
        b, seq_len = field_ids.shape
        act = self.act_query.expand(b, 1, -1)
        x = torch.cat([act, x], dim=1)
        # padding mask: True = ignore. [ACT] (pos 0) always attended.
        pos = torch.arange(seq_len, device=field_ids.device).unsqueeze(0)
        pad = pos >= lengths.unsqueeze(1)
        mask = torch.cat([torch.zeros(b, 1, dtype=torch.bool, device=pad.device), pad], dim=1)
        h = self.encoder(x, src_key_padding_mask=mask)
        return self.policy(h[:, 0])

    def num_params(self) -> int:
        return sum(p.numel() for p in self.parameters())
