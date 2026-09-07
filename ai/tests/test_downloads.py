import json
import time

import pytest
import requests

from data.downloads import Manifest, RateLimiter, fetch


def test_manifest_roundtrip(tmp_path):
    m = Manifest(tmp_path)
    assert not m.has("k1")
    m.add("k1", {"n": 1})
    assert Manifest(tmp_path).has("k1")  # persisted
    assert Manifest(tmp_path).entries["k1"]["n"] == 1


def test_manifest_write_is_atomic(tmp_path):
    m = Manifest(tmp_path)
    m.add("k", {})
    assert not list(tmp_path.glob("*.tmp*"))
    json.loads((tmp_path / "manifest.json").read_text())  # valid json


def test_fetch_partial_never_lands(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("mid-stream")

    monkeypatch.setattr("requests.Session.get", boom)
    with pytest.raises(requests.ConnectionError):
        fetch("http://x.invalid/f", tmp_path / "f.bin", requests.Session(), retries=1)
    assert not (tmp_path / "f.bin").exists()
    assert not list(tmp_path.glob("*.part"))


def test_rate_limiter_spaces_calls():
    rl = RateLimiter(0.1)
    t0 = time.monotonic()
    rl.wait()
    rl.wait()
    assert time.monotonic() - t0 >= 0.1
