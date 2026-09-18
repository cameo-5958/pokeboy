"""Smogon monthly usage-stats puller for gen1 formats.

Crawls https://www.smogon.com/stats/ month index and fetches, per month and
per format: the usage tables (0 and 1500+ cutoffs), moveset text, and chaos
JSON, when present. Old months predate gen1 stats - 404s are skipped quietly.
"""

from __future__ import annotations

import re
from pathlib import Path

import requests

from data.downloads import Manifest, RateLimiter, fetch

BASE = "https://www.smogon.com/stats/"
FORMATS = ("gen1ou", "gen1uu", "gen1ubers")


def list_months(session: requests.Session) -> list[str]:
    r = session.get(BASE, timeout=30)
    r.raise_for_status()
    return sorted(set(re.findall(r'href="(\d{4}-\d{2}(?:-DLC\d)?)/"', r.text)))


def month_files(month: str) -> list[str]:
    files = []
    for fmt in FORMATS:
        for cutoff in ("0", "1500"):
            files.append(f"{month}/{fmt}-{cutoff}.txt")
            files.append(f"{month}/moveset/{fmt}-{cutoff}.txt")
            files.append(f"{month}/chaos/{fmt}-{cutoff}.json")
    return files


def pull_smogon(
    root: Path, dry_run: bool = False, min_interval_s: float = 1.0
) -> int:
    dest_root = Path(root) / "raw" / "smogon"
    manifest = Manifest(dest_root)
    session = requests.Session()
    rl = RateLimiter(min_interval_s)
    fetched = 0
    rl.wait()
    for month in list_months(session):
        for rel in month_files(month):
            if manifest.has(rel):
                continue
            if dry_run:
                print(f"would fetch {BASE}{rel}")
                continue
            rl.wait()
            try:
                fetch(f"{BASE}{rel}", dest_root / rel, session, retries=2)
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 404:
                    manifest.add(rel, {"missing": True})
                    continue
                raise
            manifest.add(rel, {})
            fetched += 1
    return fetched
