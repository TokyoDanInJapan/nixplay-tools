"""Flow tests for export_album, download, and the playlist subcommands."""

import argparse
import json

import pytest

from nixplay_export import download, export_album
from nixplay_playlists import cmd_export, cmd_merge, cmd_unlink


class StreamResponse:
    def __init__(self, data=b"file-bytes", status=200):
        self.data = data
        self.status_code = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)

    def iter_content(self, chunk_size):
        yield self.data


class FakeClient:
    """Offline stand-in for NixplaySession used by the flow tests."""

    def __init__(self, playlists=(), slides=None, album_photos=None):
        self.playlists = list(playlists)
        self.slides = slides or {}
        self.album_photos = album_photos or {}
        self.added = []
        self.removed = []
        self.deleted_playlists = []

    def get(self, url, **kwargs):
        return StreamResponse()

    def list_playlists(self):
        return [p for p in self.playlists if p["id"] not in self.deleted_playlists]

    def iter_playlist_slides(self, playlist_id):
        yield from self.slides.get(playlist_id, [])

    def iter_album_photos(self, album_id):
        yield from self.album_photos.get(album_id, [])

    def add_playlist_items(self, playlist_id, picture_ids):
        self.added.append((playlist_id, list(picture_ids)))
        self.slides.setdefault(playlist_id, []).extend({"dbId": i} for i in picture_ids)

    def remove_playlist_item(self, playlist_id, item_id):
        self.removed.append((playlist_id, item_id))
        self.slides[playlist_id] = [
            s for s in self.slides[playlist_id] if s["playlistItemId"] != item_id
        ]

    def delete_playlist(self, playlist_id):
        self.deleted_playlists.append(playlist_id)


def test_download_names_and_by_year(tmp_path):
    photo = {"filename": "a.jpg", "caption": "", "sortDate": "20230404152000"}
    saved = download(FakeClient(), "http://x", str(tmp_path), photo, "k1")
    assert saved == "230404 1520 a.jpg"
    assert (tmp_path / saved).read_bytes() == b"file-bytes"

    saved = download(FakeClient(), "http://x", str(tmp_path), photo, "k2", by_year=True)
    assert saved == "2023/230404 1520 a.jpg"
    assert (tmp_path / "2023" / "230404 1520 a.jpg").exists()


def test_export_album_downloads_and_skips(tmp_path):
    photos = [
        {
            "id": 1,
            "filename": "new.jpg",
            "caption": "",
            "sortDate": "20240101000000",
            "originalUrl": "http://orig/1",
        },
        {
            "id": 2,
            "filename": "old.jpg",
            "caption": "",
            "sortDate": "20240101000000",
            "originalUrl": "http://orig/2",
        },
    ]
    album = {"id": 7, "title": "Trip", "photo_count": 2, "_kind": "web"}
    dest = tmp_path / "Trip"
    dest.mkdir(parents=True)
    (dest / "already-here.jpg").write_bytes(b"x")
    (dest / "_manifest.json").write_text(json.dumps({"2": "already-here.jpg"}))

    client = FakeClient(album_photos={7: photos})
    done, skipped, failed = export_album(client, album, str(tmp_path))
    assert (done, skipped, failed) == (1, 1, 0)
    manifest = json.loads((dest / "_manifest.json").read_text())
    assert manifest["1"] == "240101 0000 new.jpg"
    assert len(json.loads((dest / "_metadata.json").read_text())) == 2


def test_export_album_skips_empty(tmp_path):
    album = {"id": 8, "title": "Empty", "photo_count": 0}
    assert export_album(FakeClient(), album, str(tmp_path)) == (0, 0, 0)
    assert not (tmp_path / "Empty").exists()


def playlist_args(tmp_path, **kw):
    defaults = {
        "export_dir": str(tmp_path),
        "by_year": False,
        "execute": False,
        "older_than_days": 365,
        "source": None,
        "dest": None,
        "keep_source": False,
    }
    defaults.update(kw)
    return argparse.Namespace(**defaults)


def test_cmd_export_downloads_shared_and_renames_video_stills(tmp_path):
    (tmp_path / "_manifest.json").write_text(json.dumps({"1": "mine.jpg"}))
    (tmp_path / "mine.jpg").write_bytes(b"x")
    slides = [
        {
            "dbId": 1,
            "filename": "mine.jpg",
            "originalUrl": "http://o/1",
            "timestamp": 1650000000,
            "playlistItemId": "i1",
        },
        {
            "dbId": 5,
            "filename": "shared.jpg",
            "originalUrl": "http://o/5",
            "timestamp": 1650000000,
            "playlistItemId": "i5",
        },
        {
            "dbId": 6,
            "filename": "clip.mp4",
            "originalUrl": "http://o/6",
            "timestamp": 1650000000,
            "mediaType": "video",
            "playlistItemId": "i6",
        },
    ]
    client = FakeClient(playlists=[{"id": 3, "name": "P", "picture_count": 3}], slides={3: slides})
    assert cmd_export(client, playlist_args(tmp_path)) == 0
    manifest = json.loads((tmp_path / "Playlist P" / "_manifest.json").read_text())
    assert set(manifest) == {"5", "6"}  # own photo untouched
    assert manifest["6"].endswith("clip (video still).jpg")


def test_cmd_unlink_dry_run_and_execute(tmp_path):
    old_ts, new_ts = 1500000000, 4000000000
    slides = [
        {"dbId": 1, "filename": "old.jpg", "timestamp": old_ts, "playlistItemId": "a"},
        {"dbId": 2, "filename": "new.jpg", "timestamp": new_ts, "playlistItemId": "b"},
        {"dbId": 3, "filename": "old-unbacked.jpg", "timestamp": old_ts, "playlistItemId": "c"},
    ]
    (tmp_path / "_manifest.json").write_text(json.dumps({"1": "old.jpg"}))
    (tmp_path / "old.jpg").write_bytes(b"x")

    client = FakeClient(
        playlists=[{"id": 3, "name": "P", "picture_count": 3}], slides={3: list(slides)}
    )
    assert cmd_unlink(client, playlist_args(tmp_path)) == 0
    assert client.removed == []  # dry run

    # execute: only the old AND backed-up item is unlinked; verification then
    # still sees the old-but-unbacked slide, so exit code is 1
    assert cmd_unlink(client, playlist_args(tmp_path, execute=True)) == 1
    assert client.removed == [(3, "a")]


def test_cmd_merge_verifies_then_deletes_source(tmp_path):
    client = FakeClient(
        playlists=[
            {"id": 1, "name": "A", "picture_count": 2},
            {"id": 2, "name": "B", "picture_count": 1},
        ],
        slides={1: [{"dbId": 10}, {"dbId": 11}], 2: [{"dbId": 11}]},
    )
    args = playlist_args(tmp_path, source="A", dest="B", execute=True)
    assert cmd_merge(client, args) == 0
    assert client.added == [(2, [10])]  # 11 was already in B
    assert client.deleted_playlists == [1]


def test_cmd_merge_dry_run_changes_nothing(tmp_path):
    client = FakeClient(
        playlists=[
            {"id": 1, "name": "A", "picture_count": 1},
            {"id": 2, "name": "B", "picture_count": 0},
        ],
        slides={1: [{"dbId": 10}]},
    )
    assert cmd_merge(client, playlist_args(tmp_path, source="A", dest="B")) == 0
    assert client.added == [] and client.deleted_playlists == []


def test_cmd_merge_unknown_playlist(tmp_path):
    client = FakeClient(playlists=[{"id": 1, "name": "A", "picture_count": 0}])
    with pytest.raises(SystemExit, match="No playlist named"):
        cmd_merge(client, playlist_args(tmp_path, source="A", dest="Nope"))


class FlakyClient(FakeClient):
    """First download attempt fails, second succeeds."""

    def __init__(self):
        super().__init__()
        self.calls = 0

    def get(self, url, **kwargs):
        self.calls += 1
        if self.calls == 1:
            import requests

            raise requests.ConnectionError("first try fails")
        return StreamResponse()


def test_download_retries_after_network_error(tmp_path, monkeypatch):
    import nixplay_export

    monkeypatch.setattr(nixplay_export.time, "sleep", lambda _: None)
    photo = {"filename": "r.jpg", "caption": "", "sortDate": "20230101000000"}
    saved = download(FlakyClient(), "http://x", str(tmp_path), photo, "k")
    assert saved == "230101 0000 r.jpg"


def _patch_cli(monkeypatch, module, client, argv):
    import sys

    monkeypatch.setattr(module, "NixplaySession", lambda *a, **k: client)
    monkeypatch.setattr(module, "get_credentials", lambda: ("e@x.com", "pw"))
    monkeypatch.setattr(sys, "argv", argv)


def test_export_main_flat_guard(monkeypatch, tmp_path):
    import nixplay_export

    client = FakeClient()
    client.list_albums = lambda: [
        {"id": 1, "title": "A", "photo_count": 1, "_kind": "web"},
        {"id": 2, "title": "B", "photo_count": 1, "_kind": "web"},
    ]
    _patch_cli(
        monkeypatch, nixplay_export, client, ["nixplay-export", "--out", str(tmp_path), "--flat"]
    )
    with pytest.raises(SystemExit, match="--flat requires"):
        nixplay_export.main()


def test_export_main_happy_path(monkeypatch, tmp_path):
    import nixplay_export

    client = FakeClient(
        album_photos={
            1: [
                {
                    "id": 9,
                    "filename": "a.jpg",
                    "caption": "",
                    "sortDate": "20240101000000",
                    "originalUrl": "http://o/9",
                }
            ]
        }
    )
    client.list_albums = lambda: [{"id": 1, "title": "A", "photo_count": 1, "_kind": "web"}]
    _patch_cli(monkeypatch, nixplay_export, client, ["nixplay-export", "--out", str(tmp_path)])
    assert nixplay_export.main() == 0
    assert (tmp_path / "A" / "240101 0000 a.jpg").exists()


def test_prune_main_dry_run(monkeypatch, tmp_path):
    import json as _json

    import nixplay_prune

    backup = tmp_path / "backup"
    backup.mkdir()
    (backup / "_manifest.json").write_text(_json.dumps({"1": "a.jpg"}))
    (backup / "a.jpg").write_bytes(b"x")
    client = FakeClient()
    client.list_albums = lambda: [{"id": 1, "title": "A", "photo_count": 1, "_kind": "web"}]
    client.album_photos = {1: [{"id": 1, "filename": "a.jpg", "sortDate": "20200101000000"}]}
    monkeypatch.chdir(tmp_path)
    _patch_cli(monkeypatch, nixplay_prune, client, ["nixplay-prune", "--export-dir", str(backup)])
    assert nixplay_prune.main() == 0
    plan = _json.loads((tmp_path / "prune_plan.json").read_text())
    assert [p["id"] for p in plan] == [1]


def test_prune_main_refuses_without_backup(monkeypatch, tmp_path):
    import nixplay_prune

    _patch_cli(
        monkeypatch,
        nixplay_prune,
        FakeClient(),
        ["nixplay-prune", "--export-dir", str(tmp_path / "nope")],
    )
    with pytest.raises(SystemExit, match="refusing"):
        nixplay_prune.main()


def test_playlists_main_dispatch(monkeypatch, tmp_path):
    import json as _json

    import nixplay_playlists

    (tmp_path / "_manifest.json").write_text(_json.dumps({}))
    client = FakeClient(playlists=[{"id": 1, "name": "P", "picture_count": 0}])
    _patch_cli(
        monkeypatch,
        nixplay_playlists,
        client,
        ["nixplay-playlists", "export", "--export-dir", str(tmp_path)],
    )
    assert nixplay_playlists.main() == 0
