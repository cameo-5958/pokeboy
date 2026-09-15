"""Live Pokémon Showdown replay scraper for gen1 formats.

Pages https://replay.pokemonshowdown.com/search.json?format=<fmt>&page=<n>
(≤51 items per page; empty page ends iteration) and fetches each replay's
full JSON (including the battle log) once, keyed in the manifest by id.
"""

from __future__ import annotations

import json
from pathlib import Path

import requests

from data.downloads import Manifest, RateLimiter

BASE = "https://replay.pokemonshowdown.com"
FORMATS = ("gen1ou", "gen1uu", "gen1ubers", "gen1randombattle")


def scrape_format(
    fmt: str,
    root: Path,
    min_interval_s: float = 1.0,
    max_pages: int | None = None,
    dry_run: bool = False,
) -> int:
    dest = Path(root) / "raw" / "showdown" / fmt
    manifest = Manifest(dest)
    session = requests.Session()
    rl = RateLimiter(min_interval_s)
    fetched = 0
    page = 1
    while max_pages is None or page <= max_pages:
        rl.wait()
        r = session.get(f"{BASE}/search.json", params={"format": fmt, "page": page}, timeout=30)
        r.raise_for_status()
        items = r.json()
        if not items:
            break
        for item in items:
            rid = item["id"]
            if manifest.has(rid):
                continue
            if dry_run:
                print(f"would fetch {BASE}/{rid}.json")
                continue
            rl.wait()
            rr = session.get(f"{BASE}/{rid}.json", timeout=30)
            rr.raise_for_status()
            replay = rr.json()
            path = dest / f"{rid}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.part")
            tmp.write_text(json.dumps(replay))
            tmp.replace(path)
            manifest.add(rid, {"uploadtime": item.get("uploadtime")})
            fetched += 1
        if len(items) < 51:
            break
        page += 1
    return fetched


def scrape_all(root: Path, dry_run: bool = False, min_interval_s: float = 1.0) -> int:
    return sum(
        scrape_format(fmt, root, min_interval_s=min_interval_s, dry_run=dry_run)
        for fmt in FORMATS
    )
