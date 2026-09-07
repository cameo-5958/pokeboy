"""Model tier configs (sized for RTX 3080 10GB — see foundation design doc)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TierConfig:
    name: str
    layers: int
    d_model: int
    heads: int
    ffn: int


TIERS = {
    # micro tiers: distillation-ladder students (on-device targets)
    "morsel": TierConfig("morsel", layers=1, d_model=64, heads=2, ffn=256),
    "crumb": TierConfig("crumb", layers=2, d_model=96, heads=4, ffn=384),
    "bite": TierConfig("bite", layers=3, d_model=128, heads=4, ffn=512),
    "snack": TierConfig("snack", layers=4, d_model=256, heads=4, ffn=1024),
    "entree": TierConfig("entree", layers=8, d_model=512, heads=8, ffn=2048),
    "banquet": TierConfig("banquet", layers=10, d_model=640, heads=10, ffn=2560),
}
