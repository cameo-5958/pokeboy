"""HuggingFace corpora downloaders.

Repo ids and layouts verified live 2026-07-12:
  jakegrigsby/metamon-parsed-replays  gen1{ou,uu,nu,ubers}.tar.gz (first-person trajectories)
  jakegrigsby/metamon-raw-replays     data/*.parquet, all gens (filter at conversion)
  jakegrigsby/metamon-teams           */gen1*.tar.gz team corpora
  jakegrigsby/metamon-usage-stats     usage statistics
  jakegrigsby/metamon-synthetic       synthetic self-play battles
  milkkarten/pokechamp                data/*.parquet, all gens (filter at conversion)
  milkkarten/pokechamp-pretrain-data  pretraining text data
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from huggingface_hub import snapshot_download

from data.downloads import Manifest


@dataclass(frozen=True)
class HFSource:
    repo_id: str
    allow_patterns: list[str] | None  # None = full snapshot


SOURCES: dict[str, list[HFSource]] = {
    "metamon": [
        HFSource("jakegrigsby/metamon-parsed-replays", ["gen1*"]),
        HFSource("jakegrigsby/metamon-teams", ["*gen1*"]),
        HFSource("jakegrigsby/metamon-usage-stats", None),
        HFSource("jakegrigsby/metamon-raw-replays", None),
    ],
    "metamon-synthetic": [
        HFSource("jakegrigsby/metamon-synthetic", ["*gen1*"]),
    ],
    "pokechamp": [
        HFSource("milkkarten/pokechamp", None),
    ],
    # The PokéAgent challenge release is built on the metamon datasets; the
    # curated team corpus lives in metamon-teams (pulled above).
    "pokeagent": [
        HFSource("jakegrigsby/metamon-teams", None),
    ],
}


def pull_hf(source: str, root: Path, dry_run: bool = False, only: str | None = None) -> list[Path]:
    """Download the HF repos for a named source into root/raw/<source>/.

    `only` keeps just the repos whose id contains that substring, so a caller can take
    the team corpora alone (`--source metamon --only teams`) without the replay dumps.
    """
    dest_root = Path(root) / "raw" / source
    manifest = Manifest(dest_root)
    out: list[Path] = []
    for src in SOURCES[source]:
        if only and only not in src.repo_id:
            continue
        key = src.repo_id
        if manifest.has(key):
            continue
        if dry_run:
            print(f"would pull {src.repo_id} patterns={src.allow_patterns}")
            continue
        local = snapshot_download(
            repo_id=src.repo_id,
            repo_type="dataset",
            allow_patterns=src.allow_patterns,
            local_dir=dest_root / src.repo_id.replace("/", "__"),
        )
        manifest.add(key, {"patterns": src.allow_patterns, "path": str(local)})
        out.append(Path(local))
    return out
