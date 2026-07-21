"""Tests for nixplay_sort."""

import json

from nixplay_sort import plan_moves, sort_backup, year_from_metadata, year_from_name
from nixplay_verify import verify


def test_year_from_name():
    assert year_from_name("230404 1520 a.jpg") == "2023"
    assert year_from_name("970101 0000 old.jpg") == "1997"
    assert year_from_name("000000 0000 b.jpg") is None
    assert year_from_name("no-prefix.jpg") is None


def test_year_from_metadata():
    assert year_from_metadata({"sortDate": "20210417084351"}) == "2021"
    assert year_from_metadata({"timestamp": 1650000000}) == "2022"
    assert year_from_metadata({"sortDate": "00000000"}) is None
    assert year_from_metadata({}) is None


def test_plan_moves_prefix_fallback_and_idempotency():
    manifest = {
        "1": "230404 1520 a.jpg",  # prefix year
        "2": "000000 0000 b.jpg",  # metadata fallback
        "3": "2023/230101 0000 c.jpg",  # already sorted
        "4": "000000 0000 d.jpg",  # unknown
    }
    metadata = {"2": {"id": 2, "sortDate": "20091231235959"}}
    moves = {k: new for k, _, new in plan_moves(manifest, metadata)}
    assert moves == {
        "1": "2023/230404 1520 a.jpg",
        "2": "2009/000000 0000 b.jpg",
        "4": "unknown-year/000000 0000 d.jpg",
    }


def make_backup(root, manifest, metadata=None, files=()):
    root.mkdir(parents=True, exist_ok=True)
    (root / "_manifest.json").write_text(json.dumps(manifest))
    if metadata is not None:
        (root / "_metadata.json").write_text(json.dumps(metadata))
    for name in files:
        (root / name).write_bytes(b"data")


def test_sort_backup_moves_and_updates_manifest(tmp_path, capsys):
    album = tmp_path / "Album"
    make_backup(
        album,
        {"1": "230404 1520 a.jpg", "2": "000000 0000 b.jpg"},
        metadata=[{"id": 2, "sortDate": "20091231235959"}],
        files=["230404 1520 a.jpg", "000000 0000 b.jpg"],
    )
    assert sort_backup(str(tmp_path), execute=True) == 0
    manifest = json.loads((album / "_manifest.json").read_text())
    assert manifest == {"1": "2023/230404 1520 a.jpg", "2": "2009/000000 0000 b.jpg"}
    assert (album / "2023" / "230404 1520 a.jpg").exists()
    assert (album / "2009" / "000000 0000 b.jpg").exists()

    # verify still passes on the sorted layout
    capsys.readouterr()
    assert verify(str(tmp_path)) == 0

    # second run is a no-op
    capsys.readouterr()
    assert sort_backup(str(tmp_path), execute=True) == 0
    assert "Nothing to do" in capsys.readouterr().out


def test_sort_backup_dry_run_moves_nothing(tmp_path):
    make_backup(tmp_path, {"1": "230404 1520 a.jpg"}, files=["230404 1520 a.jpg"])
    assert sort_backup(str(tmp_path), execute=False) == 0
    assert (tmp_path / "230404 1520 a.jpg").exists()
    manifest = json.loads((tmp_path / "_manifest.json").read_text())
    assert manifest == {"1": "230404 1520 a.jpg"}
