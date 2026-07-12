"""Shared download plumbing: atomic manifests, resumable fetch, rate limiting.

Rules (ai spec): a partial download never enters a manifest; manifest writes
are atomic (temp file + rename); scrapers are polite (rate-limited, backoff).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import requests


class Manifest:
    """Per-source manifest.json mapping key -> metadata dict."""

    def __init__(self, source_dir: Path):
        self.path = Path(source_dir) / "manifest.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.entries: dict[str, Any] = {}
        if self.path.exists():
            self.entries = json.loads(self.path.read_text())

    def has(self, key: str) -> bool:
        return key in self.entries

    def add(self, key: str, meta: dict[str, Any]) -> None:
        self.entries[key] = {**meta, "added_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.entries, indent=1, sort_keys=True))
        os.replace(tmp, self.path)


class RateLimiter:
    def __init__(self, min_interval_s: float):
        self.min_interval_s = min_interval_s
        self._last = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        delta = self.min_interval_s - (now - self._last)
        if delta > 0:
            time.sleep(delta)
        self._last = time.monotonic()


def fetch(
    url: str,
    dest: Path,
    session: requests.Session,
    retries: int = 3,
    backoff: float = 2.0,
    timeout: float = 60.0,
) -> Path:
    """Stream url to dest atomically; dest only exists on full success."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            with session.get(url, stream=True, timeout=timeout) as r:
                r.raise_for_status()
                with open(part, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1 << 16):
                        f.write(chunk)
            os.replace(part, dest)
            return dest
        except Exception as exc:  # noqa: BLE001 - re-raised after retries
            last_exc = exc
            part.unlink(missing_ok=True)
            if attempt < retries - 1:
                time.sleep(backoff * (2**attempt))
    assert last_exc is not None
    raise last_exc
