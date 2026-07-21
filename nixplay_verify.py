#!/usr/bin/env python3
"""Verify the integrity of a local Nixplay backup.

Walks every _manifest.json under --export-dir (album subdirectories or a
flat layout) and checks that each entry's file exists and is non-empty,
and that video files contain real video data (a JPEG signature in a .mp4
means the export grabbed a still frame instead of the video). With
--md5, image content is also hashed and compared against the checksums
Nixplay recorded in _metadata.json (videos are skipped: the API serves
transcoded playback files whose hash legitimately differs).

Exits nonzero if any problem is found.

Usage:
    nixplay-verify --export-dir DIR [--md5]
"""

import argparse
import hashlib
import json
import os
import sys

from nixplay_api import iter_manifest_dirs, photo_key
from nixplay_export import VIDEO_EXTENSIONS

JPEG_MAGIC = b"\xff\xd8\xff"


def check_file(path):
    """Return a problem string, or None if the file looks healthy."""
    if not os.path.exists(path):
        return "missing"
    if os.path.getsize(path) == 0:
        return "empty"
    if path.lower().endswith(VIDEO_EXTENSIONS):
        with open(path, "rb") as fh:
            if fh.read(3) == JPEG_MAGIC:
                return "video file contains JPEG data (still frame, not video)"
    return None


def file_md5(path):
    """MD5 hex digest of a file, read in chunks."""
    digest = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_md5s(directory):
    """Map photo key -> recorded md5 from this directory's _metadata.json."""
    metadata_path = os.path.join(directory, "_metadata.json")
    if not os.path.exists(metadata_path):
        return {}
    with open(metadata_path) as fh:
        metadata = json.load(fh)
    return {photo_key(p): p["md5"] for p in metadata if p.get("md5")}


def verify(export_dir, check_md5=False):
    """Check every manifest entry; returns 0 if clean, 1 if problems."""
    total = problems = md5_checked = 0
    found_any = False
    for directory, manifest in iter_manifest_dirs(export_dir):
        found_any = True
        md5s = load_md5s(directory) if check_md5 else {}
        dir_problems = 0
        for key, filename in manifest.items():
            total += 1
            path = os.path.join(directory, filename)
            problem = check_file(path)
            if problem is None and md5s.get(key) and not path.lower().endswith(VIDEO_EXTENSIONS):
                md5_checked += 1
                if file_md5(path) != md5s[key]:
                    problem = "md5 mismatch with Nixplay's recorded checksum"
            if problem:
                problems += 1
                dir_problems += 1
                print(f"  PROBLEM ({problem}): {os.path.join(directory, filename)}")
        label = os.path.relpath(directory, export_dir) if directory != export_dir else "."
        status = "OK" if dir_problems == 0 else f"{dir_problems} problems"
        print(f"{label}: {len(manifest)} entries, {status}")
    if not found_any:
        print(f"No _manifest.json found under {export_dir!r}.")
        return 1
    print(
        f"\nChecked {total} entries"
        + (f" ({md5_checked} md5-verified)" if check_md5 else "")
        + f": {'all OK' if problems == 0 else f'{problems} PROBLEMS'}"
    )
    return 1 if problems else 0


def main():
    """CLI entry point."""
    sys.stdout.reconfigure(line_buffering=True)
    parser = argparse.ArgumentParser(description="Verify a local Nixplay backup.")
    parser.add_argument("--export-dir", required=True)
    parser.add_argument(
        "--md5",
        action="store_true",
        help="also compare image content hashes against Nixplay's records",
    )
    args = parser.parse_args()
    return verify(args.export_dir, check_md5=args.md5)


if __name__ == "__main__":
    sys.exit(main())
