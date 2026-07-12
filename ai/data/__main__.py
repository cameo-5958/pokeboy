"""Data pipeline CLI.

  python -m data pull --source {metamon,metamon-synthetic,pokechamp,pokeagent,smogon,showdown,all} [--dry-run]
"""

from __future__ import annotations

import argparse
from pathlib import Path

DATASETS_ROOT = Path(__file__).resolve().parents[1] / "datasets"

HF_SOURCES = ("metamon", "metamon-synthetic", "pokechamp", "pokeagent")


def cmd_pull(args) -> None:
    root = Path(args.root)
    sources = (
        [*HF_SOURCES, "smogon", "showdown"] if args.source == "all" else [args.source]
    )
    for source in sources:
        print(f"== pulling {source}")
        if source in HF_SOURCES:
            from data.sources.hf_corpora import pull_hf

            pull_hf(source, root, dry_run=args.dry_run)
        elif source == "smogon":
            from data.sources.smogon_stats import pull_smogon

            n = pull_smogon(root, dry_run=args.dry_run)
            print(f"smogon: fetched {n} files")
        elif source == "showdown":
            from data.sources.showdown_replays import scrape_all

            n = scrape_all(root, dry_run=args.dry_run)
            print(f"showdown: fetched {n} replays")
        else:
            raise SystemExit(f"unknown source {source!r}")


def main() -> None:
    p = argparse.ArgumentParser(prog="data")
    sub = p.add_subparsers(dest="cmd", required=True)

    pull = sub.add_parser("pull", help="download a raw corpus")
    pull.add_argument("--source", required=True)
    pull.add_argument("--root", default=str(DATASETS_ROOT))
    pull.add_argument("--dry-run", action="store_true")
    pull.set_defaults(fn=cmd_pull)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
