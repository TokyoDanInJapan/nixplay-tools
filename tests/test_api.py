"""Tests for nixplay_api using a scripted fake HTTP session (no network)."""

import pytest

from nixplay_api import API, NixplaySession, load_dotenv


class FakeResponse:
    def __init__(self, status_code=200, json_body=None, text="", cookies=None):
        self.status_code = status_code
        self._json = json_body
        self.text = text or (str(json_body) if json_body is not None else "")
        self.cookies = cookies or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


class FakeCookies(dict):
    def get(self, name, default=None):
        return dict.get(self, name, default)


class FakeSession:
    """Plays back scripted responses; records every request it receives."""

    login_ok = FakeResponse(200, {"valid": True, "errors": [], "token": "t"})

    def __init__(self, script):
        self.script = script  # list of (method, url_substring, response)
        self.requests = []
        self.headers = {}
        self.cookies = FakeCookies({"prod.csrftoken": "csrf-token"})

    def _play(self, method, url):
        self.requests.append((method, url))
        for i, (m, frag, resp) in enumerate(self.script):
            if m == method and frag in url:
                self.script.pop(i)
                return resp
        raise AssertionError(f"unexpected {method} {url}")

    def post(self, url, **kwargs):
        if url.endswith("/www-login/"):
            self.requests.append(("POST", url))
            return self.login_ok
        return self._play("POST", url)

    def get(self, url, **kwargs):
        return self._play("GET", url)

    def delete(self, url, **kwargs):
        return self._play("DELETE", url)


def make_client(script, factory_calls=None):
    sessions = []

    def factory():
        s = FakeSession(script)
        sessions.append(s)
        if factory_calls is not None:
            factory_calls.append(s)
        return s

    client = NixplaySession("e@example.com", "pw", session_factory=factory, quiet=True)
    return client, sessions


def test_login_sets_csrf_header():
    client, sessions = make_client([])
    assert sessions[0].headers["X-CSRFToken"] == "csrf-token"


def test_login_failure_raises_systemexit():
    class BadLoginSession(FakeSession):
        login_ok = FakeResponse(
            200, {"valid": False, "errors": {"password": {"messages": [["wrong"]]}}}
        )

    with pytest.raises(SystemExit, match="Login failed"):
        NixplaySession(
            "e@example.com", "pw", session_factory=lambda: BadLoginSession([]), quiet=True
        )


def test_delete_accepts_source_id_body():
    script = [("POST", "/picture/1/delete/", FakeResponse(200, {"source_id": None}))]
    client, _ = make_client(script)
    client.delete_picture(1)  # no exception


def test_delete_treats_404_as_already_gone():
    script = [("POST", "/picture/2/delete/", FakeResponse(404))]
    client, _ = make_client(script)
    client.delete_picture(2)


def test_delete_stale_session_relogs_in_and_retries():
    stale = FakeResponse(200, None, text="<html>login</html>")
    good = FakeResponse(200, {"source_id": 5})
    script = [("POST", "/picture/3/delete/", stale), ("POST", "/picture/3/delete/", good)]
    client, sessions = make_client(script)
    client.delete_picture(3)
    assert len(sessions) == 2  # re-login created a fresh session


def test_delete_persistent_no_op_raises():
    script = [
        ("POST", "/picture/4/delete/", FakeResponse(200, None, text="nope")) for _ in range(10)
    ]
    client, _ = make_client(script)
    with pytest.raises(RuntimeError, match="not accepted"):
        client.delete_picture(4)


def test_proactive_relogin_after_threshold(monkeypatch):
    monkeypatch.setattr(NixplaySession, "RELOGIN_EVERY", 2)
    script = [("POST", "/delete/", FakeResponse(200, {"source_id": 1})) for _ in range(4)]
    client, sessions = make_client(script)
    for pid in range(4):
        client.delete_picture(pid)
    assert len(sessions) > 1


def test_verify_deleted():
    script = [
        ("GET", f"{API}/picture/9/", FakeResponse(404)),
        ("GET", f"{API}/picture/8/", FakeResponse(200, {})),
    ]
    client, _ = make_client(script)
    assert client.verify_deleted(9) is True
    assert client.verify_deleted(8) is False


def test_list_albums_tags_kind():
    script = [
        ("GET", "/v2/albums/web/json/", FakeResponse(200, [{"id": 1, "title": "A"}])),
        ("GET", "/v2/albums/email/json/", FakeResponse(200, [{"id": 2, "title": "B"}])),
    ]
    client, _ = make_client(script)
    albums = client.list_albums()
    assert [(a["id"], a["_kind"]) for a in albums] == [(1, "web"), (2, "email")]


def test_iter_album_photos_pages_until_short_page(monkeypatch):
    import nixplay_api

    monkeypatch.setattr(nixplay_api, "PAGE_SIZE", 2)
    page1 = FakeResponse(200, {"photos": [{"id": 1}, {"id": 2}]})
    page2 = FakeResponse(200, {"photos": [{"id": 3}]})
    script = [("GET", "page=1", page1), ("GET", "page=2", page2)]
    client, _ = make_client(script)
    ids = [p["id"] for p in client.iter_album_photos(7)]
    assert ids == [1, 2, 3]


def test_load_dotenv_parsing_and_precedence(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n"
        "NIXPLAY_EMAIL=file@example.com\n"
        "NIXPLAY_PASSWORD='quoted pass'\n"
        "MALFORMED LINE\n"
    )
    monkeypatch.setenv("NIXPLAY_EMAIL", "env@example.com")
    monkeypatch.delenv("NIXPLAY_PASSWORD", raising=False)
    load_dotenv(str(env_file))
    import os

    assert os.environ["NIXPLAY_EMAIL"] == "env@example.com"  # real env wins
    assert os.environ["NIXPLAY_PASSWORD"] == "quoted pass"


def test_load_dotenv_missing_file_is_noop(tmp_path):
    load_dotenv(str(tmp_path / "absent.env"))  # must not raise


def test_get_json_relogs_in_on_403(monkeypatch):
    import nixplay_api

    monkeypatch.setattr(nixplay_api.time, "sleep", lambda _: None)
    script = [
        ("GET", "/v2/albums/", FakeResponse(403)),
        ("GET", "/v2/albums/", FakeResponse(200, [{"id": 1}])),
    ]
    client, sessions = make_client(script)
    assert client.get_json(f"{API}/v2/albums/web/json/") == [{"id": 1}]
    assert len(sessions) == 2  # stale session was replaced


def test_get_json_relogs_in_on_non_json_body(monkeypatch):
    import nixplay_api

    monkeypatch.setattr(nixplay_api.time, "sleep", lambda _: None)
    script = [
        ("GET", "/v2/albums/", FakeResponse(200, None, text="<html>login</html>")),
        ("GET", "/v2/albums/", FakeResponse(200, [])),
    ]
    client, sessions = make_client(script)
    assert client.get_json(f"{API}/v2/albums/web/json/") == []
    assert len(sessions) == 2


class ItemFakeSession(FakeSession):
    """FakeSession with scripted playlist-item endpoints."""


def test_add_playlist_items_success_and_rejection():
    ok = FakeResponse(200, {})
    bad = FakeResponse(500, None, text="boom")
    script = [("POST", "/v3/playlists/1/items", ok), ("POST", "/v3/playlists/2/items", bad)]
    client, _ = make_client(script)
    client.add_playlist_items(1, [10, 11])  # no exception
    import pytest as _pytest

    with _pytest.raises(RuntimeError, match="rejected"):
        client.add_playlist_items(2, [12])


def test_delete_playlist_and_remove_item_status_handling():
    script = [
        ("DELETE", "/v3/playlists/1", FakeResponse(204)),
        ("DELETE", "/v3/playlists/2", FakeResponse(404)),
        ("DELETE", "/v3/playlists/3", FakeResponse(500, None, text="no")),
        ("DELETE", "/v3/playlists/4/items?id=a", FakeResponse(200)),
        ("DELETE", "/v3/playlists/5/items?id=b", FakeResponse(500, None, text="no")),
    ]
    client, _ = make_client(script)
    client.delete_playlist(1)
    client.delete_playlist(2)
    import pytest as _pytest

    with _pytest.raises(RuntimeError):
        client.delete_playlist(3)
    client.remove_playlist_item(4, "a")
    with _pytest.raises(RuntimeError):
        client.remove_playlist_item(5, "b")


def test_iter_playlist_slides_pages(monkeypatch):
    import nixplay_api

    monkeypatch.setattr(nixplay_api, "PAGE_SIZE", 2)
    page1 = FakeResponse(200, {"slides": [{"dbId": 1}, {"dbId": 2}]})
    page2 = FakeResponse(200, {"slides": [{"dbId": 3}]})
    script = [("GET", "offset=0", page1), ("GET", "offset=2", page2)]
    client, _ = make_client(script)
    assert [s["dbId"] for s in client.iter_playlist_slides(9)] == [1, 2, 3]


def test_photo_key_precedence():
    from nixplay_api import photo_key

    assert photo_key({"id": 1, "dbId": 2, "md5": "m"}) == "1"
    assert photo_key({"dbId": 2, "md5": "m"}) == "2"
    assert photo_key({"md5": "m", "s3filename": "s"}) == "m"
    assert photo_key({"s3filename": "s"}) == "s"


def test_iter_manifest_dirs_both_layouts(tmp_path):
    import json as _json

    from nixplay_api import iter_manifest_dirs

    (tmp_path / "_manifest.json").write_text(_json.dumps({"1": "a.jpg"}))
    sub = tmp_path / "Album"
    sub.mkdir()
    (sub / "_manifest.json").write_text(_json.dumps({"2": "b.jpg"}))
    found = {d: m for d, m in iter_manifest_dirs(str(tmp_path))}
    assert found[str(tmp_path)] == {"1": "a.jpg"}
    assert found[str(sub)] == {"2": "b.jpg"}
    assert list(iter_manifest_dirs(str(tmp_path / "nope"))) == []
