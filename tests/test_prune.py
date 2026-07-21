"""Tests for nixplay_prune's backup interlock and candidate selection."""

import json

from nixplay_prune import collect_candidates, load_backup_index


def write_manifest(directory, manifest, files=()):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "_manifest.json").write_text(json.dumps(manifest))
    for name in files:
        (directory / name).write_bytes(b"data")


def test_backup_index_album_subdirectory_layout(tmp_path):
    write_manifest(tmp_path / "Album One", {"1": "a.jpg", "2": "gone.jpg"}, ["a.jpg"])
    index = load_backup_index(str(tmp_path))
    assert set(index) == {"1"}  # entry without a file on disk is excluded


def test_backup_index_flat_layout(tmp_path):
    write_manifest(tmp_path, {"5": "b.jpg"}, ["b.jpg"])
    index = load_backup_index(str(tmp_path))
    assert set(index) == {"5"}


def test_backup_index_missing_dir_is_empty(tmp_path):
    assert load_backup_index(str(tmp_path / "nope")) == {}


class FakeClient:
    def __init__(self, albums, photos_by_album):
        self._albums = albums
        self._photos = photos_by_album

    def list_albums(self):
        return self._albums

    def iter_album_photos(self, album_id):
        yield from self._photos[album_id]


def test_collect_candidates_splits_by_cutoff_and_backup():
    photos = [
        {"id": 1, "sortDate": "20200101000000"},  # old, backed up -> delete
        {"id": 2, "sortDate": "20200101000000"},  # old, NOT backed up -> skip
        {"id": 3, "sortDate": "20990101000000"},  # new -> kept
        {"id": 4, "sortDate": ""},  # no date -> kept (safe side)
    ]
    client = FakeClient([{"id": 77}], {77: photos})
    backup = {"1": "/somewhere/a.jpg"}
    to_delete, kept, not_backed_up = collect_candidates(client, "20250721", backup)
    assert [p["id"] for p in to_delete] == [1]
    assert [p["id"] for p in not_backed_up] == [2]
    assert kept == 2


class FakeDeleteClient:
    """Scripted client for run_deletion: records deletes, controls verification."""

    def __init__(self, fail_ids=(), verify_ok=True):
        self.deleted = []
        self.fail_ids = set(fail_ids)
        self.verify_ok = verify_ok

    def delete_picture(self, photo_id):
        if photo_id in self.fail_ids:
            raise RuntimeError("boom")
        self.deleted.append(photo_id)

    def verify_deleted(self, photo_id):
        return self.verify_ok


def test_run_deletion_happy_path(tmp_path):
    from nixplay_prune import run_deletion

    photos = [{"id": i, "filename": f"{i}.jpg"} for i in range(3)]
    client = FakeDeleteClient()
    log_path = str(tmp_path / "log.json")
    assert run_deletion(client, photos, log_path=log_path) is True
    assert client.deleted == [0, 1, 2]
    with open(log_path) as fh:
        assert len(json.load(fh)) == 3


def test_run_deletion_counts_failures(tmp_path):
    from nixplay_prune import run_deletion

    photos = [{"id": i, "filename": f"{i}.jpg"} for i in range(3)]
    client = FakeDeleteClient(fail_ids={1})
    assert run_deletion(client, photos, log_path=str(tmp_path / "log.json")) is False
    assert client.deleted == [0, 2]


def test_run_deletion_aborts_when_verification_fails(tmp_path):
    import pytest

    from nixplay_prune import run_deletion

    photos = [{"id": i, "filename": f"{i}.jpg"} for i in range(150)]
    client = FakeDeleteClient(verify_ok=False)
    with pytest.raises(SystemExit, match="VERIFICATION FAILED"):
        run_deletion(client, photos, log_path=str(tmp_path / "log.json"))
    assert len(client.deleted) == 100  # stopped at the first checkpoint


def test_resolve_cutoff():
    import datetime

    from nixplay_prune import resolve_cutoff

    assert resolve_cutoff(365, "2025-01-31") == datetime.date(2025, 1, 31)
    expected = datetime.date.today() - datetime.timedelta(days=30)
    assert resolve_cutoff(30, None) == expected
