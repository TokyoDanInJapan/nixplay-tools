#!/usr/bin/env python3
"""Export all photos and videos from a Nixplay account.

Downloads full-resolution originals from every album (web + email albums),
saves the raw per-album metadata JSON, and names each file the same way as
the RWTH Aachen nixplay-export script:

    YYMMDD HHMM Caption -- OriginalName.jpg

The date is the EXIF capture date when the file has one, otherwise Nixplay's
sortDate (upload time). Files with no caption just get date + original name.

Credentials are read from a .env file next to this script (see .env.example),
falling back to the NIXPLAY_EMAIL / NIXPLAY_PASSWORD environment variables,
then to an interactive prompt (password is never echoed or stored).

Usage:
    nixplay-export [--out DIR] [--albums SUBSTRING]

The export is resumable: photos already downloaded (tracked per-album in
_manifest.json) are skipped on re-runs.
"""

import argparse
import datetime
import json
import os
import re
import sys
import time

import requests
from PIL import Image

from nixplay_api import MAX_RETRIES, NixplaySession, get_credentials, photo_key

VIDEO_EXTENSIONS = (".mp4", ".mov", ".m4v", ".avi", ".mpg", ".mpeg", ".3gp")


def sanitize(text):
    """Make text safe for use in a filename."""
    text = text.replace(":", "-").replace("/", "-").replace("\x00", "")
    return re.sub(r"[\r\n\t]+", " ", text).strip()


def is_video(photo):
    """True if this album photo or slide is a video."""
    if photo.get("video"):
        return True
    return (photo.get("filename") or "").lower().endswith(VIDEO_EXTENSIONS)


def pick_url(photo):
    """Best download URL for this item, or None."""
    if is_video(photo):
        video = photo.get("video") or {}
        # The top-level url/originalUrl of a video is only a JPEG still.
        return video.get("url") or None
    return photo.get("originalUrl") or photo.get("url")


def exif_short_date(path):
    """'2016:10:18 17:25:20' EXIF DateTime -> '161018 1725', or None."""
    try:
        with Image.open(path) as img:
            raw = img.getexif().get(306)
        if not raw:
            return None
        d = raw.replace(":", "-").replace("/", "-")
        short = d[2:4] + d[5:7] + d[8:13] + d[14:16]
        return short if len(short) == 11 else None
    except Exception:
        return None


def sortdate_short_date(sort_date):
    """'20210417084351' -> '210417 0843', or None."""
    s = str(sort_date or "")
    if len(s) >= 12 and s.isdigit():
        return s[2:8] + " " + s[8:12]
    return None


def target_name(photo, downloaded_path):
    original = sanitize(photo.get("filename") or "")
    if not original:
        original = os.path.basename((photo.get("s3filename") or "unnamed").split("/")[-1])
    caption = sanitize(photo.get("caption") or "")
    name = f"{caption} -- {original}" if caption else original
    short = None if is_video(photo) else exif_short_date(downloaded_path)
    if short is None:
        short = sortdate_short_date(photo.get("sortDate"))
    return f"{short} {name}" if short else name


UNKNOWN_YEAR = "unknown-year"


def year_from_name(basename):
    """'230404 1520 a.jpg' -> '2023'; zeroed or absent prefix -> None."""
    prefix = basename[:6]
    if prefix.isdigit() and prefix != "000000":
        yy = int(prefix[:2])
        return str(2000 + yy if yy <= 69 else 1900 + yy)
    return None


def year_from_metadata(item):
    """Upload year from an album photo's sortDate or a slide's timestamp."""
    s = str(item.get("sortDate") or "")
    if len(s) >= 4 and s[:4].isdigit() and s[:4] != "0000":
        return s[:4]
    ts = item.get("timestamp")
    if ts:
        if ts > 1e11:  # milliseconds
            ts /= 1000
        return str(datetime.datetime.fromtimestamp(ts).year)
    return None


def dest_rel_name(photo, downloaded_path, by_year=False):
    """Relative path a download should land at, optionally under a year dir."""
    name = target_name(photo, downloaded_path)
    if not by_year:
        return name
    year = year_from_name(name) or year_from_metadata(photo) or UNKNOWN_YEAR
    return os.path.join(year, name)


def unique_path(directory, name):
    """A path under directory for name, adding \" (N)\" on collision."""
    path = os.path.join(directory, name)
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(name)
    n = 2
    while True:
        path = os.path.join(directory, f"{stem} ({n}){ext}")
        if not os.path.exists(path):
            return path
        n += 1


def download(client, url, dest_dir, photo, key, by_year=False):
    """Stream url to dest_dir under its final name; returns the relative path."""
    part_path = os.path.join(dest_dir, f".part-{key}")
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with client.get(url, timeout=300, stream=True) as resp:
                resp.raise_for_status()
                with open(part_path, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=1 << 20):
                        fh.write(chunk)
            rel = dest_rel_name(photo, part_path, by_year)
            subdir = os.path.dirname(rel)
            if subdir:
                os.makedirs(os.path.join(dest_dir, subdir), exist_ok=True)
            final = unique_path(dest_dir, rel)
            os.rename(part_path, final)
            return os.path.relpath(final, dest_dir)
        except requests.RequestException as exc:
            if attempt == MAX_RETRIES:
                if os.path.exists(part_path):
                    os.remove(part_path)
                raise
            wait = 2**attempt
            print(f"  download failed ({exc}); retrying in {wait}s...")
            time.sleep(wait)


def export_album(client, album, out_root, by_year=False, flat=False):
    """Download an album's missing items; returns (done, skipped, failed)."""
    title = sanitize(album.get("title") or f"album-{album['id']}") or f"album-{album['id']}"
    if album.get("_kind") == "email":
        title += " (email album)"

    if not album.get("photo_count"):
        print(f"\nAlbum '{title}' is empty - skipping.")
        return 0, 0, 0

    dest = out_root if flat else os.path.join(out_root, title)
    os.makedirs(dest, exist_ok=True)
    for stale in os.listdir(dest):
        if stale.startswith(".part-"):
            os.remove(os.path.join(dest, stale))

    manifest_path = os.path.join(dest, "_manifest.json")
    manifest = {}
    if os.path.exists(manifest_path):
        with open(manifest_path) as fh:
            manifest = json.load(fh)

    def save_manifest():
        with open(manifest_path, "w") as fh:
            json.dump(manifest, fh, indent=2)

    reported = album.get("photo_count", "?")
    print(f"\nAlbum '{title}': {reported} items reported by Nixplay")

    all_photos = []
    done = skipped = failed = downloaded_this_page = 0
    for photo in client.iter_album_photos(album["id"]):
        all_photos.append(photo)
        key = photo_key(photo)
        existing = manifest.get(key)
        if existing and os.path.exists(os.path.join(dest, existing)):
            skipped += 1
            continue
        url = pick_url(photo)
        if not url:
            print(f"  no usable download URL for {photo.get('filename')!r} (id {key}) - skipping.")
            failed += 1
            continue
        try:
            saved = download(client, url, dest, photo, key, by_year=by_year)
        except requests.RequestException as exc:
            print(f"  FAILED {photo.get('filename')!r} (id {key}): {exc}")
            failed += 1
            continue
        manifest[key] = saved
        done += 1
        downloaded_this_page += 1
        if done % 100 == 0:
            print(
                f"  {done + skipped + failed}/{reported} processed "
                f"({done} new, {skipped} already present, {failed} failed)"
            )
            save_manifest()

    with open(os.path.join(dest, "_metadata.json"), "w") as fh:
        json.dump(all_photos, fh, indent=2)
    save_manifest()
    print(f"  Finished '{title}': {done} downloaded, {skipped} already present, {failed} failed")
    return done, skipped, failed


def main():
    """CLI entry point."""
    sys.stdout.reconfigure(line_buffering=True)
    parser = argparse.ArgumentParser(description="Export all Nixplay albums.")
    parser.add_argument(
        "--out", default="nixplay_export", help="output directory (default: ./nixplay_export)"
    )
    parser.add_argument(
        "--albums",
        metavar="SUBSTRING",
        default=None,
        help="only export albums whose title contains SUBSTRING",
    )
    parser.add_argument(
        "--by-year",
        action="store_true",
        help="place downloads in per-year subfolders (like nixplay-sort)",
    )
    parser.add_argument(
        "--flat",
        action="store_true",
        help="write directly into --out with no album subfolder "
        "(requires a single non-empty album)",
    )
    args = parser.parse_args()

    client = NixplaySession(*get_credentials())

    albums = client.list_albums()
    if args.albums is not None:
        albums = [a for a in albums if args.albums.lower() in (a.get("title") or "").lower()]
        if not albums:
            raise SystemExit(f"No albums match {args.albums!r}.")
    total_reported = sum(a.get("photo_count", 0) for a in albums)
    print(f"Found {len(albums)} albums, ~{total_reported} items reported by Nixplay.")

    if args.flat and sum(1 for a in albums if a.get("photo_count")) > 1:
        raise SystemExit("--flat requires a single non-empty album; use --albums to pick one.")

    totals = [0, 0, 0]
    for album in albums:
        result = export_album(client, album, args.out, by_year=args.by_year, flat=args.flat)
        totals = [t + r for t, r in zip(totals, result)]

    print(
        f"\nAll done: {totals[0]} downloaded, {totals[1]} already present, "
        f"{totals[2]} failed, into '{os.path.abspath(args.out)}'"
    )
    if totals[2]:
        print("Re-run the script to retry failures; existing files are skipped.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
