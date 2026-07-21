"""Shared client for Nixplay's unofficial web API.

Encapsulates login, CSRF handling, and the quirks discovered while
building these tools:

- Sessions expire after roughly 10-15 minutes. Most endpoints then fail
  visibly, but the picture-delete endpoint keeps returning HTTP 200
  while silently doing nothing. A genuine delete always carries a
  "source_id" key in its JSON body, so any other response triggers a
  re-login and retry, and long delete runs refresh the session
  proactively.
- Photo originals live at the camelCase ``originalUrl`` field; for
  videos that field is only a JPEG still frame and the real MP4 is the
  signed playback URL inside the ``video`` object.
"""

import getpass
import json
import os
import time

import requests

API = "https://api.nixplay.com"
HEADERS = {
    "Origin": "https://app.nixplay.com",
    "Referer": "https://app.nixplay.com/",
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) nixplay-tools/0.1",
}
MAX_RETRIES = 4
PAGE_SIZE = 100


def load_dotenv(path):
    """Minimal .env parser: KEY=VALUE lines, # comments, optional quotes.

    Real environment variables take precedence over the file.
    """
    if not os.path.exists(path):
        return
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = value


def photo_key(item):
    """Stable string key for an album photo or playlist slide dict."""
    return str(item.get("id") or item.get("dbId") or item.get("md5") or item.get("s3filename"))


def iter_manifest_dirs(export_dir):
    """Yield (directory, manifest dict) for every _manifest.json in a backup.

    Accepts either layout: album subdirectories each holding a
    _manifest.json, or a flat directory with _manifest.json directly
    beside the media files.
    """
    if not os.path.isdir(export_dir):
        return
    candidates = [export_dir] + [os.path.join(export_dir, d) for d in os.listdir(export_dir)]
    for directory in candidates:
        manifest_path = os.path.join(directory, "_manifest.json")
        if not os.path.exists(manifest_path):
            continue
        with open(manifest_path) as fh:
            yield directory, json.load(fh)


def get_credentials():
    """Credentials from the environment, a .env beside this file, or a prompt."""
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    email = os.environ.get("NIXPLAY_EMAIL") or input("Nixplay email: ")
    password = os.environ.get("NIXPLAY_PASSWORD") or getpass.getpass("Nixplay password: ")
    return email, password


class NixplaySession:
    """Authenticated Nixplay API session with automatic re-login."""

    RELOGIN_EVERY = 500  # proactive refresh during long delete runs

    def __init__(self, email, password, session_factory=requests.Session, quiet=False):
        self.email = email
        self.password = password
        self._session_factory = session_factory
        self._quiet = quiet
        self._logins = 0
        self.relogin()

    def relogin(self):
        session = self._session_factory()
        resp = session.post(
            f"{API}/www-login/",
            data={"email": self.email, "password": self.password},
            headers=HEADERS,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("errors") and data["errors"] != []:
            raise SystemExit(f"Login failed: {json.dumps(data['errors'], indent=2)}")
        csrf = session.cookies.get("prod.csrftoken")
        if not csrf:
            raise SystemExit("Login succeeded but no CSRF token cookie was returned.")
        session.headers.update(HEADERS)
        session.headers["X-CSRFToken"] = csrf
        self.session = session
        self._calls_since_login = 0
        self._logins += 1
        if not self._quiet:
            if self._logins == 1:
                print(f"Logged in as {self.email}")
            else:
                print("  session refreshed")

    def get(self, url, **kwargs):
        kwargs.setdefault("timeout", 60)
        return self.session.get(url, **kwargs)

    def get_json(self, url):
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self.get(url)
                resp.raise_for_status()
                return resp.json()
            except (requests.RequestException, ValueError) as exc:
                if attempt == MAX_RETRIES:
                    raise
                # A 401/403, or a 200 whose body isn't JSON (login page),
                # means the session went stale - refresh it before retrying.
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if isinstance(exc, ValueError) or status in (401, 403):
                    print("  session may be stale; re-logging in...")
                    self.relogin()
                wait = 2**attempt
                print(f"  request failed ({exc}); retrying in {wait}s...")
                time.sleep(wait)

    def list_albums(self):
        """All albums (web + email uploads), tagged with their kind."""
        albums = []
        for kind in ("web", "email"):
            data = self.get_json(f"{API}/v2/albums/{kind}/json/") or []
            for album in data:
                album["_kind"] = kind
            albums.extend(data)
        return albums

    def iter_album_photos(self, album_id):
        """Yield every photo dict in the album, paging through the API."""
        page = 1
        while True:
            data = self.get_json(
                f"{API}/album/{album_id}/pictures/json/?page={page}&limit={PAGE_SIZE}"
            )
            photos = data.get("photos", [])
            if not photos:
                return
            yield from photos
            if len(photos) < PAGE_SIZE:
                return
            page += 1

    def list_playlists(self):
        return self.get_json(f"{API}/v3/playlists")

    def iter_playlist_slides(self, playlist_id):
        """Yield every slide dict in the playlist, paging through the API."""
        offset = 0
        while True:
            data = self.get_json(
                f"{API}/v3/playlists/{playlist_id}/slides?size={PAGE_SIZE}&offset={offset}"
            )
            slides = data.get("slides", data) if isinstance(data, dict) else data
            if not slides:
                return
            yield from slides
            if len(slides) < PAGE_SIZE:
                return
            offset += len(slides)

    def add_playlist_items(self, playlist_id, picture_ids):
        """Append existing pictures to a playlist (no dedup server-side)."""
        body = json.dumps({"items": [{"pictureId": i} for i in picture_ids]})
        resp = self.session.post(
            f"{API}/v3/playlists/{playlist_id}/items",
            data=body,
            headers={"content-type": "application/json", "accept": "application/json"},
            timeout=60,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"adding {len(picture_ids)} items to playlist {playlist_id} rejected "
                f"(HTTP {resp.status_code} {resp.text[:200]!r})"
            )

    def delete_playlist(self, playlist_id):
        """Delete a playlist container (never touches the photos in it)."""
        resp = self.session.delete(f"{API}/v3/playlists/{playlist_id}", timeout=30)
        if resp.status_code not in (200, 204, 404):
            raise RuntimeError(
                f"deleting playlist {playlist_id} rejected "
                f"(HTTP {resp.status_code} {resp.text[:200]!r})"
            )

    def remove_playlist_item(self, playlist_id, playlist_item_id):
        """Unlink one slide from a playlist (does not delete the photo)."""
        url = f"{API}/v3/playlists/{playlist_id}/items?id={playlist_item_id}"
        if self._calls_since_login >= self.RELOGIN_EVERY:
            self.relogin()
        resp = self.session.delete(url, timeout=30)
        self._calls_since_login += 1
        if resp.status_code == 404:
            return  # already gone
        if resp.status_code not in (200, 204):
            raise RuntimeError(
                f"unlink of {playlist_item_id} rejected "
                f"(HTTP {resp.status_code} {resp.text[:200]!r})"
            )

    def delete_picture(self, photo_id):
        """Delete a photo, detecting the silent-no-op stale-session mode."""
        url = f"{API}/picture/{photo_id}/delete/json/"
        for attempt in range(1, MAX_RETRIES + 1):
            if self._calls_since_login >= self.RELOGIN_EVERY:
                self.relogin()
            try:
                resp = self.session.post(url, timeout=30)
            except requests.RequestException as exc:
                if attempt == MAX_RETRIES:
                    raise
                wait = 2**attempt
                print(f"  delete failed ({exc}); retrying in {wait}s...")
                time.sleep(wait)
                continue
            self._calls_since_login += 1
            if resp.status_code == 404:
                return  # already gone
            if resp.status_code == 200:
                try:
                    body = resp.json()
                except ValueError:
                    body = None
                if isinstance(body, dict) and "source_id" in body:
                    return
            if attempt == MAX_RETRIES:
                raise RuntimeError(
                    f"delete of {photo_id} not accepted after {MAX_RETRIES} attempts "
                    f"(last: HTTP {resp.status_code} {resp.text[:200]!r})"
                )
            print(f"  suspect delete for {photo_id} (HTTP {resp.status_code}); re-logging in...")
            self.relogin()
            time.sleep(2**attempt)

    def verify_deleted(self, photo_id):
        resp = self.get(f"{API}/picture/{photo_id}/", timeout=30)
        return resp.status_code == 404
