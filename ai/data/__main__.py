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


def cmd_convert(args) -> None:
    import json

    from data.convert_showdown import RejectedReplay, convert_replay
    from data.trajectory import write_rows

    if args.source != "showdown":
        raise SystemExit(
            "only the showdown converter exists yet; other corpora ship "
            "pre-parsed formats handled in a later phase"
        )
    root = Path(args.root)
    raw = root / "raw" / "showdown"
    processed = root / "processed" / "showdown"
    rejected_dir = root / "rejected" / "showdown"
    rejected_dir.mkdir(parents=True, exist_ok=True)
    reject_log = open(rejected_dir / "reason.jsonl", "a")

    rows, part, converted, rejected = [], 0, 0, 0
    for path in sorted(raw.rglob("*.json")):
        if path.name == "manifest.json":
            continue
        try:
            replay = json.loads(path.read_text())
            rows.extend(convert_replay(replay, source="showdown"))
            converted += 1
        except (RejectedReplay, json.JSONDecodeError) as exc:
            rejected += 1
            reject_log.write(json.dumps({"file": str(path), "reason": str(exc)}) + "\n")
            continue
        if len(rows) >= args.rows_per_file:
            write_rows(rows, processed / f"part-{part:04d}.parquet")
            part += 1
            rows = []
    if rows:
        write_rows(rows, processed / f"part-{part:04d}.parquet")
    reject_log.close()
    print(json.dumps({"converted": converted, "rejected": rejected, "parts": part + 1}))


def main() -> None:
    p = argparse.ArgumentParser(prog="data")
    sub = p.add_subparsers(dest="cmd", required=True)

    pull = sub.add_parser("pull", help="download a raw corpus")
    pull.add_argument("--source", required=True)
    pull.add_argument("--root", default=str(DATASETS_ROOT))
    pull.add_argument("--dry-run", action="store_true")
    pull.set_defaults(fn=cmd_pull)

    conv = sub.add_parser("convert", help="convert a raw corpus to schema_v1 parquet")
    conv.add_argument("--source", required=True)
    conv.add_argument("--root", default=str(DATASETS_ROOT))
    conv.add_argument("--rows-per-file", type=int, default=10_000)
    conv.set_defaults(fn=cmd_convert)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
