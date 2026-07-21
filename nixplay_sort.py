#!/usr/bin/env python3
"""Sort a local Nixplay backup into per-year folders.

Moves each file into a <year>/ subfolder of its manifest directory,
using the capture year encoded in the exported filename prefix
("YYMMDD HHMM ..." - EXIF capture date when available, upload date
otherwise). Files with a zeroed prefix (no usable EXIF date) fall back
to the upload date recorded in _metadata.json; anything still undatable
goes into "unknown-year/". Manifests are rewritten in step, so
nixplay-prune and nixplay-verify keep working on the sorted layout.

Dry run by default; idempotent (already-sorted files are left alone).

Usage:
    nixplay-sort --export-dir DIR [--execute]
"""

import argparse
import collections
import json
import os
import sys

from nixplay_api import iter_manifest_dirs, photo_key
from nixplay_export import UNKNOWN_YEAR as UNKNOWN
from nixplay_export import unique_path, year_from_metadata, year_from_name


def plan_moves(manifest, metadata_by_key):
    """Yield (key, old_rel, new_rel) for every file not yet in its year folder."""
    for key, rel in manifest.items():
        base = os.path.basename(rel)
        year = year_from_name(base) or year_from_metadata(metadata_by_key.get(key, {})) or UNKNOWN
        if os.path.dirname(rel) == year:
            continue
        yield key, rel, os.path.join(year, base)


def load_metadata_by_key(directory):
    """Map photo key -> metadata dict from this directory's _metadata.json."""
    path = os.path.join(directory, "_metadata.json")
    if not os.path.exists(path):
        return {}
    with open(path) as fh:
        return {photo_key(item): item for item in json.load(fh)}


def sort_backup(export_dir, execute=False):
    """Plan (and with execute=True, apply) year-folder moves for a backup."""
    total_moves = 0
    for directory, manifest in iter_manifest_dirs(export_dir):
        metadata_by_key = load_metadata_by_key(directory)
        moves = list(plan_moves(manifest, metadata_by_key))
        if not moves:
            continue
        label = os.path.relpath(directory, export_dir) if directory != export_dir else "."
        per_year = collections.Counter(os.path.dirname(new) for _, _, new in moves)
        years = ", ".join(f"{y}: {n}" for y, n in sorted(per_year.items()))
        print(f"{label}: {len(moves)} files to sort ({years})")
        total_moves += len(moves)
        if not execute:
            continue

        for key, old_rel, new_rel in moves:
            old_path = os.path.join(directory, old_rel)
            if not os.path.exists(old_path):
                print(f"  WARNING: {old_rel} missing on disk; manifest left unchanged")
                continue
            os.makedirs(os.path.join(directory, os.path.dirname(new_rel)), exist_ok=True)
            final = unique_path(directory, new_rel)
            os.rename(old_path, final)
            manifest[key] = os.path.relpath(final, directory)
        with open(os.path.join(directory, "_manifest.json"), "w") as fh:
            json.dump(manifest, fh, indent=2)

    if total_moves == 0:
        print("Nothing to do - backup is already sorted by year.")
    elif not execute:
        print(f"\nDry run only - {total_moves} files would move. Re-run with --execute.")
    else:
        print(f"\nSorted {total_moves} files into year folders.")
    return 0


def main():
    """CLI entry point."""
    sys.stdout.reconfigure(line_buffering=True)
    parser = argparse.ArgumentParser(description="Sort a Nixplay backup into year folders.")
    parser.add_argument("--export-dir", required=True)
    parser.add_argument(
        "--execute", action="store_true", help="move files; without this flag nothing is changed"
    )
    args = parser.parse_args()
    return sort_backup(args.export_dir, execute=args.execute)


if __name__ == "__main__":
    sys.exit(main())
