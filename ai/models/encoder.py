"""Encoder transformer over field-value tokens (ai/SPECS.md §4.2).

embedding = E_field + E_value + E_slot + W_cont·cont ; bidirectional encoder
(no positional encoding - structure lives in field/slot embeddings); learned
query tokens are prepended: [ACT] feeds the policy head and, when value_bins
> 0, [VAL] feeds a two-hot win-prob classification head (SPECS §4.2 head 2).
Belief/opp-policy heads come with RL.
"""

from __future__ import annotations

import torch
import torch.nn as nn

from models.tiers import TierConfig
from models.tokenizer import Tokenizer

N_ACTIONS = 10


class FieldValueEncoder(nn.Module):
    def __init__(self, cfg: TierConfig, tokenizer: Tokenizer | None = None, value_bins: int = 0):
        super().__init__()
        tok = tokenizer or Tokenizer()
        d = cfg.d_model
        self.value_bins = value_bins
        self.e_field = nn.Embedding(tok.n_fields, d)
        self.e_value = nn.Embedding(tok.n_values, d)
        self.e_slot = nn.Embedding(tok.n_slots, d)
        self.w_cont = nn.Linear(tok.n_cont, d)
        self.act_query = nn.Parameter(torch.randn(1, 1, d) * 0.02)
        if value_bins:
            self.val_query = nn.Parameter(torch.randn(1, 1, d) * 0.02)
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
        if value_bins:
            self.value = nn.Sequential(nn.LayerNorm(d), nn.Linear(d, d), nn.GELU(), nn.Linear(d, value_bins))

    def forward(
        self,
        field_ids: torch.Tensor,  # (B, L) int
        value_ids: torch.Tensor,
        slot_ids: torch.Tensor,
        cont: torch.Tensor,  # (B, L, C) float
        lengths: torch.Tensor,  # (B,) int
        return_value: bool = False,
    ) -> torch.Tensor | tuple[torch.Tensor, torch.Tensor]:
        if return_value and not self.value_bins:
            raise ValueError("model was built without a value head (value_bins=0)")
        x = (
            self.e_field(field_ids)
            + self.e_value(value_ids)
            + self.e_slot(slot_ids)
            + self.w_cont(cont)
        )
        b, seq_len = field_ids.shape
        n_query = 2 if self.value_bins else 1
        queries = [self.act_query.expand(b, 1, -1)]
        if self.value_bins:
            queries.append(self.val_query.expand(b, 1, -1))
        x = torch.cat(queries + [x], dim=1)
        # padding mask: True = ignore. Query tokens always attended.
        pos = torch.arange(seq_len, device=field_ids.device).unsqueeze(0)
        pad = pos >= lengths.unsqueeze(1)
        mask = torch.cat(
            [torch.zeros(b, n_query, dtype=torch.bool, device=pad.device), pad], dim=1
        )
        h = self.encoder(x, src_key_padding_mask=mask)
        pi = self.policy(h[:, 0])
        if return_value:
            return pi, self.value(h[:, 1])
        return pi

    def num_params(self) -> int:
        return sum(p.numel() for p in self.parameters())
