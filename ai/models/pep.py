"""PEP: pointer-style entity policy for on-device Pokémon battling.

fp32 training definition of the on-device policy.  The architecture is kept
int8-export friendly on purpose: no LayerNorm, ReLU FFNs, ReZero residual
scalars, plain bias-free matmuls for attention, and a pointer head that scores
candidate tokens instead of a dense action layer.

Input contract (see pkai/python/pkai.py and models/pep_data.py): 27 tokens in
fixed order (0 field, 1-6 own mon, 7-12 player mon, 13-16 own moves, 17-20
player moves, 21-26 items).  Each token carries `present`, `candidate`
(0 or 1 + action index), four categorical ids `cat[4]` and 48 int8 features.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, replace

import torch
import torch.nn as nn
import torch.nn.functional as F

FEAT = 48
N_TOKENS = 27
N_ACTIONS = 16
EV_DIM = 64
N_TOKEN_TYPES = 6

# Vocabulary sizes (from the C++ feature builder).
N_SPECIES = 191
N_MOVES = 166
N_TYPES = 28  # type id + 1, 0 = none
N_EFFECTS = 128
N_MATCHUP = N_SPECIES * N_SPECIES  # 36481
N_TRAINER_CLASS = 48
N_ITEM_CLASS = 8
N_REQUEST_KIND = 4  # cat1 = request kind + 1 (0 = unknown), so 3 kinds need 4 rows

# Fixed token slices per type (contract order).
SLICE_FIELD = slice(0, 1)
SLICE_OWN_MON = slice(1, 7)
SLICE_PLAYER_MON = slice(7, 13)
SLICE_OWN_MOVE = slice(13, 17)
SLICE_PLAYER_MOVE = slice(17, 21)
SLICE_ITEM = slice(21, 27)

NEG = -1e9  # finite "minus infinity" used inside attention masks


@dataclass
class PEPConfig:
    d: int = 128
    layers: int = 3
    heads: int = 4
    ffn: int = 512
    gru: int = 128
    emb_species: int = 32
    emb_move: int = 32
    emb_matchup: int = 8
    emb_small: int = 8

    def __post_init__(self) -> None:
        if self.d % self.heads:
            raise ValueError(f"d={self.d} must be divisible by heads={self.heads}")


def config_for(base: PEPConfig | dict | None = None, **overrides) -> PEPConfig:
    """The model configuration: `PEPConfig` defaults, optionally seeded from a checkpoint's
    `config` dict, with explicit field overrides applied on top."""
    if base is None:
        cfg = PEPConfig()
    elif isinstance(base, PEPConfig):
        cfg = base
    else:
        cfg = PEPConfig(**base)
    return replace(cfg, **overrides) if overrides else cfg


# --------------------------------------------------------------------------- blocks


class SelfAttention(nn.Module):
    """Multi-head self-attention, bias-free projections, key padding mask."""

    def __init__(self, d: int, heads: int):
        super().__init__()
        self.h = heads
        self.dh = d // heads
        self.q = nn.Linear(d, d, bias=False)
        self.k = nn.Linear(d, d, bias=False)
        self.v = nn.Linear(d, d, bias=False)
        self.o = nn.Linear(d, d, bias=False)

    def forward(self, x: torch.Tensor, key_mask: torch.Tensor) -> torch.Tensor:
        # x: (N, L, d); key_mask: (N, L) bool, True = attend
        N, L, _ = x.shape
        q = self.q(x).view(N, L, self.h, self.dh).transpose(1, 2)
        k = self.k(x).view(N, L, self.h, self.dh).transpose(1, 2)
        v = self.v(x).view(N, L, self.h, self.dh).transpose(1, 2)
        scores = torch.matmul(q, k.transpose(-1, -2)).float() / math.sqrt(self.dh)  # (N,h,L,L)
        scores = scores.masked_fill(~key_mask[:, None, None, :], NEG)
        attn = torch.softmax(scores, dim=-1).to(v.dtype)
        out = torch.matmul(attn, v).transpose(1, 2).reshape(N, L, -1)
        return self.o(out)


class EncoderLayer(nn.Module):
    def __init__(self, d: int, heads: int, ffn: int):
        super().__init__()
        self.attn = SelfAttention(d, heads)
        self.ff1 = nn.Linear(d, ffn)
        self.ff2 = nn.Linear(ffn, d)
        self.alpha_attn = nn.Parameter(torch.zeros(1))  # ReZero
        self.alpha_ffn = nn.Parameter(torch.zeros(1))

    def forward(self, x: torch.Tensor, key_mask: torch.Tensor) -> torch.Tensor:
        x = x + self.alpha_attn * self.attn(x, key_mask)
        x = x + self.alpha_ffn * self.ff2(F.relu(self.ff1(x)))
        return x


class PMA(nn.Module):
    """Pooling by multi-head attention with a single learned seed query."""

    def __init__(self, d: int, heads: int):
        super().__init__()
        self.h = heads
        self.dh = d // heads
        self.seed = nn.Parameter(torch.randn(d) * 0.02)
        self.q = nn.Linear(d, d, bias=False)
        self.k = nn.Linear(d, d, bias=False)
        self.v = nn.Linear(d, d, bias=False)
        self.o = nn.Linear(d, d, bias=False)

    def forward(self, x: torch.Tensor, key_mask: torch.Tensor) -> torch.Tensor:
        N, L, _ = x.shape
        q = self.q(self.seed).view(1, self.h, 1, self.dh).to(x.dtype)
        k = self.k(x).view(N, L, self.h, self.dh).transpose(1, 2)
        v = self.v(x).view(N, L, self.h, self.dh).transpose(1, 2)
        scores = torch.matmul(q, k.transpose(-1, -2)).float() / math.sqrt(self.dh)  # (N,h,1,L)
        scores = scores.masked_fill(~key_mask[:, None, None, :], NEG)
        attn = torch.softmax(scores, dim=-1).to(v.dtype)
        out = torch.matmul(attn, v).reshape(N, -1)
        return self.o(out)


class TokenEmbedding(nn.Module):
    """Per-token-type input projection of [f/127 ⊕ categorical embeddings] plus a type vector."""

    def __init__(self, cfg: PEPConfig):
        super().__init__()
        d, s = cfg.d, cfg.emb_small
        self.species = nn.Embedding(N_SPECIES, cfg.emb_species)
        self.move = nn.Embedding(N_MOVES, cfg.emb_move)
        self.type_ = nn.Embedding(N_TYPES, s)
        self.effect = nn.Embedding(N_EFFECTS, s)
        self.matchup = nn.Embedding(N_MATCHUP, cfg.emb_matchup)
        self.trainer = nn.Embedding(N_TRAINER_CLASS, s)
        self.item = nn.Embedding(N_ITEM_CLASS, s)
        self.request = nn.Embedding(N_REQUEST_KIND, s)
        self.token_type = nn.Embedding(N_TOKEN_TYPES, d)
        for e in (self.species, self.move, self.type_, self.effect, self.matchup, self.trainer, self.item, self.request):
            nn.init.normal_(e.weight, std=0.1)

        mon_in = FEAT + cfg.emb_species + 2 * s + cfg.emb_matchup
        move_in = FEAT + cfg.emb_move + 2 * s
        self.in_field = nn.Linear(FEAT + 2 * s, d)
        self.in_own_mon = nn.Linear(mon_in, d)
        self.in_player_mon = nn.Linear(mon_in, d)
        self.in_own_move = nn.Linear(move_in, d)
        self.in_player_move = nn.Linear(move_in, d)
        self.in_item = nn.Linear(FEAT + s, d)

    @staticmethod
    def _idx(cat: torch.Tensor, j: int, n: int) -> torch.Tensor:
        return cat[..., j].long().clamp(0, n - 1)

    def forward(self, cat: torch.Tensor, f: torch.Tensor, tok_type: torch.Tensor) -> torch.Tensor:
        # cat: (N,27,4) int; f: (N,27,48) int8/float; tok_type: (N,27) int
        x = f.float() / 127.0
        c = cat

        def mon(sl: slice, lin: nn.Linear) -> torch.Tensor:
            cc = c[:, sl]
            e = torch.cat(
                [
                    x[:, sl],
                    self.species(self._idx(cc, 0, N_SPECIES)),
                    self.type_(self._idx(cc, 1, N_TYPES)),
                    self.type_(self._idx(cc, 2, N_TYPES)),
                    self.matchup(self._idx(cc, 3, N_MATCHUP)),
                ],
                dim=-1,
            )
            return lin(e)

        def move(sl: slice, lin: nn.Linear) -> torch.Tensor:
            cc = c[:, sl]
            e = torch.cat(
                [
                    x[:, sl],
                    self.move(self._idx(cc, 0, N_MOVES)),
                    self.effect(self._idx(cc, 1, N_EFFECTS)),
                    self.type_(self._idx(cc, 2, N_TYPES)),
                ],
                dim=-1,
            )
            return lin(e)

        cf = c[:, SLICE_FIELD]
        field = self.in_field(
            torch.cat(
                [
                    x[:, SLICE_FIELD],
                    self.trainer(self._idx(cf, 0, N_TRAINER_CLASS)),
                    self.request(self._idx(cf, 1, N_REQUEST_KIND)),
                ],
                dim=-1,
            )
        )
        ci = c[:, SLICE_ITEM]
        item = self.in_item(torch.cat([x[:, SLICE_ITEM], self.item(self._idx(ci, 0, N_ITEM_CLASS))], dim=-1))
        h = torch.cat(
            [
                field,
                mon(SLICE_OWN_MON, self.in_own_mon),
                mon(SLICE_PLAYER_MON, self.in_player_mon),
                move(SLICE_OWN_MOVE, self.in_own_move),
                move(SLICE_PLAYER_MOVE, self.in_player_move),
                item,
            ],
            dim=1,
        )
        return h + self.token_type(tok_type.long().clamp(0, N_TOKEN_TYPES - 1))


# --------------------------------------------------------------------------- model


class PEP(nn.Module):
    def __init__(self, cfg: PEPConfig | None = None):
        super().__init__()
        self.cfg = cfg = cfg or PEPConfig()
        d = cfg.d
        self.embed = TokenEmbedding(cfg)
        self.layers = nn.ModuleList([EncoderLayer(d, cfg.heads, cfg.ffn) for _ in range(cfg.layers)])
        self.pool = PMA(d, cfg.heads)
        self.gru = nn.GRUCell(EV_DIM + d, cfg.gru)
        self.ctx = nn.Linear(d + cfg.gru, d)
        self.ptr_q = nn.Linear(d, d, bias=False)
        self.ptr_k = nn.Linear(d, d, bias=False)
        self.b_type = nn.Parameter(torch.zeros(N_TOKEN_TYPES))  # per candidate-token-type bias
        self.value = nn.Linear(d, 1)

    # -- helpers ---------------------------------------------------------------

    def init_hidden(self, batch: int, device=None, dtype=torch.float32) -> torch.Tensor:
        return torch.zeros(batch, self.cfg.gru, device=device, dtype=dtype)

    @staticmethod
    def legal_mask(legal: torch.Tensor) -> torch.Tensor:
        """uint16 bitmask (…,) -> bool (…,16)."""
        bits = torch.arange(N_ACTIONS, device=legal.device)
        return ((legal.long()[..., None] >> bits) & 1).bool()

    def num_params(self, include_matchup: bool = True) -> int:
        n = sum(p.numel() for p in self.parameters())
        if not include_matchup:
            n -= self.embed.matchup.weight.numel()
        return n

    # -- pieces ----------------------------------------------------------------

    def encode(self, features: dict[str, torch.Tensor]) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """Tokens -> (token states (N,27,d), pooled c_s (N,d), present mask (N,27))."""
        present = features["present"].bool()
        # Guarantee at least one attendable key per row (padded rows): token 0 (field).
        key_mask = present.clone()
        key_mask[:, 0] |= ~present.any(dim=1)
        x = self.embed(features["cat"], features["f"], features["type"])
        for layer in self.layers:
            x = layer(x, key_mask)
        c_s = self.pool(x, key_mask)
        return x, c_s, present

    def heads(
        self, tokens: torch.Tensor, c: torch.Tensor, features: dict[str, torch.Tensor], present: torch.Tensor
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """Pointer policy logits (N,16) and value logit (N,) from context c (N,d)."""
        N = c.shape[0]
        q = self.ptr_q(c).float()  # (N,d)
        k = self.ptr_k(tokens).float()  # (N,27,d)
        score = torch.einsum("nd,nld->nl", q, k) / math.sqrt(self.cfg.d)
        score = score + self.b_type[features["type"].long().clamp(0, N_TOKEN_TYPES - 1)]
        cand = features["candidate"].long()
        valid = (cand > 0) & present
        target = torch.where(valid, cand - 1, torch.full_like(cand, N_ACTIONS))
        logits = torch.full((N, N_ACTIONS + 1), float("-inf"), device=c.device, dtype=torch.float32)
        logits = logits.scatter(1, target, score)[:, :N_ACTIONS]
        logits = logits.masked_fill(~self.legal_mask(features["legal"]), float("-inf"))
        value = self.value(c).float().squeeze(-1)
        return logits, value

    # -- APIs ------------------------------------------------------------------

    def forward(
        self, features: dict[str, torch.Tensor], ev: torch.Tensor | None = None, h: torch.Tensor | None = None
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """Single decision step.

        features: dict with type/present/candidate (N,27), cat (N,27,4), f (N,27,48), legal (N,)
        ev: (N,64) float event vector (zeros if None); h: (N,gru) GRU state (zeros if None).
        Returns (logits (N,16) with -inf for illegal/non-candidate, value logit (N,), h' (N,gru)).
        """
        N = features["present"].shape[0]
        dev = features["present"].device
        if ev is None:
            ev = torch.zeros(N, EV_DIM, device=dev)
        if h is None:
            h = self.init_hidden(N, dev)
        tokens, c_s, present = self.encode(features)
        h_new = self.gru(torch.cat([ev.float(), c_s.float()], dim=-1), h.float())
        c = self.ctx(torch.cat([c_s, h_new.to(c_s.dtype)], dim=-1))
        logits, value = self.heads(tokens, c, features, present)
        return logits, value, h_new

    def forward_seq(
        self,
        features: dict[str, torch.Tensor],
        ev: torch.Tensor | None = None,
        h0: torch.Tensor | None = None,
        mask: torch.Tensor | None = None,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """Whole-sequence API for training.

        features arrays have shape (B,T,...); ev (B,T,64); h0 (B,gru); mask (B,T) bool (padding).
        The encoder runs on all B*T decisions at once; only the GRU loops over T.
        Returns logits (B,T,16), value (B,T), h_seq (B,T,gru) where h_seq[:,t] is the
        state after decision t (unchanged at masked steps).
        """
        B, T = features["present"].shape[:2]
        dev = features["present"].device
        flat = {k: v.reshape(B * T, *v.shape[2:]) for k, v in features.items() if k in _FEATURE_KEYS}
        tokens, c_s, present = self.encode(flat)
        c_s = c_s.float().view(B, T, -1)
        if ev is None:
            ev = torch.zeros(B, T, EV_DIM, device=dev)
        h = self.init_hidden(B, dev) if h0 is None else h0.float()
        hs = []
        for t in range(T):
            h_new = self.gru(torch.cat([ev[:, t].float(), c_s[:, t]], dim=-1), h)
            h = h_new if mask is None else torch.where(mask[:, t, None], h_new, h)
            hs.append(h)
        h_seq = torch.stack(hs, dim=1)  # (B,T,gru)
        c = self.ctx(torch.cat([c_s, h_seq], dim=-1).to(tokens.dtype)).reshape(B * T, -1)
        logits, value = self.heads(tokens, c, flat, present)
        return logits.view(B, T, N_ACTIONS), value.view(B, T), h_seq


_FEATURE_KEYS = ("type", "present", "candidate", "cat", "f", "legal")


def features_to_tensors(arrays: dict, device=None) -> dict[str, torch.Tensor]:
    """numpy feature dict (from pep_data) -> model-ready tensors (cat/type/candidate/legal as int64)."""
    out = {}
    for k in _FEATURE_KEYS:
        a = arrays[k]
        t = torch.as_tensor(a.astype("int64") if k != "f" else a.astype("int8"), device=device)
        out[k] = t
    return out


# --------------------------------------------------------------------------- checkpoints

FEATURE_SCHEMA = "pkai-features-v1"


def save_checkpoint(model: PEP, path: str, steps: int, extra: dict | None = None) -> None:
    import os

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    blob = {
        "model": {k: v.detach().cpu() for k, v in model.state_dict().items()},
        "config": asdict(model.cfg),
        "steps": int(steps),
        "feature_schema": FEATURE_SCHEMA,
    }
    if extra:
        blob.update(extra)
    torch.save(blob, path)


def load_checkpoint(path: str, map_location="cpu") -> tuple[PEP, dict]:
    blob = torch.load(path, map_location=map_location, weights_only=False)
    if blob.get("feature_schema") != FEATURE_SCHEMA:
        raise ValueError(f"checkpoint feature_schema {blob.get('feature_schema')!r} != {FEATURE_SCHEMA!r}")
    # Older checkpoints carry a "tier" name alongside "config"; the config dict is authoritative.
    model = PEP(PEPConfig(**blob["config"]))
    model.load_state_dict(blob["model"])
    return model, blob


def param_report(cfg: PEPConfig | None = None) -> dict[str, int]:
    m = PEP(cfg or PEPConfig())
    return {"total": m.num_params(True), "excl_matchup": m.num_params(False)}


if __name__ == "__main__":
    counts = param_report()
    print(f"{asdict(PEPConfig())} total={counts['total']:,}  excl_matchup={counts['excl_matchup']:,}")
