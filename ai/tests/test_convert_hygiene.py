from pathlib import Path

from data.__main__ import _clear_parts


def test_clear_parts_removes_stale_parquet(tmp_path):
    d = tmp_path / "processed" / "showdown"
    d.mkdir(parents=True)
    for i in (0, 1, 9999):
        (d / f"part-{i:04d}.parquet").write_bytes(b"x")
    (d / "manifest.json").write_text("{}")
    _clear_parts(d)
    assert not list(d.glob("part-*.parquet"))
    assert (d / "manifest.json").exists()  # non-part files untouched


def test_clear_parts_tolerates_missing_dir(tmp_path):
    _clear_parts(tmp_path / "nope")  # must not raise
