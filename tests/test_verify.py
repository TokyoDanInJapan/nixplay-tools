"""Tests for nixplay_verify."""

import hashlib
import json

from nixplay_verify import check_file, verify


def make_backup(root, manifest, metadata=None, files=None):
    root.mkdir(parents=True, exist_ok=True)
    (root / "_manifest.json").write_text(json.dumps(manifest))
    if metadata is not None:
        (root / "_metadata.json").write_text(json.dumps(metadata))
    for name, content in (files or {}).items():
        (root / name).write_bytes(content)


def test_check_file_states(tmp_path):
    good = tmp_path / "a.jpg"
    good.write_bytes(b"data")
    assert check_file(str(good)) is None
    assert check_file(str(tmp_path / "absent.jpg")) == "missing"
    empty = tmp_path / "empty.jpg"
    empty.write_bytes(b"")
    assert check_file(str(empty)) == "empty"
    fake_video = tmp_path / "clip.mp4"
    fake_video.write_bytes(b"\xff\xd8\xff rest-of-jpeg")
    assert "JPEG" in check_file(str(fake_video))
    real_video = tmp_path / "real.mp4"
    real_video.write_bytes(b"\x00\x00\x00 ftypisom")
    assert check_file(str(real_video)) is None


def test_verify_clean_backup(tmp_path, capsys):
    make_backup(tmp_path / "Album", {"1": "a.jpg"}, files={"a.jpg": b"data"})
    assert verify(str(tmp_path)) == 0
    assert "all OK" in capsys.readouterr().out


def test_verify_reports_missing_and_fake_video(tmp_path, capsys):
    make_backup(
        tmp_path / "Album",
        {"1": "gone.jpg", "2": "clip.mp4"},
        files={"clip.mp4": b"\xff\xd8\xff..."},
    )
    assert verify(str(tmp_path)) == 1
    out = capsys.readouterr().out
    assert "missing" in out
    assert "JPEG" in out


def test_verify_md5(tmp_path, capsys):
    content = b"image-bytes"
    make_backup(
        tmp_path,
        {"1": "a.jpg", "2": "b.jpg"},
        metadata=[
            {"id": 1, "md5": hashlib.md5(content).hexdigest()},
            {"id": 2, "md5": "0" * 32},
        ],
        files={"a.jpg": content, "b.jpg": b"tampered"},
    )
    assert verify(str(tmp_path), check_md5=True) == 1
    out = capsys.readouterr().out
    assert "md5 mismatch" in out
    assert "b.jpg" in out
    assert "a.jpg" not in out.replace("a.jpg: ", "")  # a.jpg passed


def test_verify_empty_dir(tmp_path):
    assert verify(str(tmp_path)) == 1
