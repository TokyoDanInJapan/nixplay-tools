#!/usr/bin/env python3
"""Delete photos from the Nixplay cloud library that are older than a cutoff.

Age is measured by Nixplay's sortDate (upload date). By default this is a
DRY RUN that only reports what would be deleted; pass --execute to actually
delete. Deletion is permanent on Nixplay's side.

Safety interlock: a photo is only ever deleted if it exists in the local
export (its ID is in a _manifest.json under --export-dir AND the referenced
file is present on disk). Anything not verifiably backed up is skipped and
reported. Every 100th deletion is independently verified against the API,
and the run aborts if a "successful" delete turns out to be a no-op.

Credentials come from .env / environment, as with nixplay-export.

Usage:
    nixplay-prune                  # dry run, 365-day cutoff
    nixplay-prune --execute        # actually delete
    nixplay-prune --older-than-days 730
"""

import argparse
import datetime
import json
import os
import sys

import requests

from nixplay_api import NixplaySession, get_credentials, iter_manifest_dirs, photo_key


def load_backup_index(export_dir):
    """Map photo ID -> local file path for every exported item on disk.

    Accepts either layout: album subdirectories each holding a
    _manifest.json (as nixplay-export writes), or a flat directory with
    _manifest.json directly beside the media files.
    """
    index = {}
    for directory, manifest in iter_manifest_dirs(export_dir):
        for key, filename in manifest.items():
            path = os.path.join(directory, filename)
            if os.path.exists(path):
                index[key] = path
    return index


def collect_candidates(client, cutoff, backup):
    """Split library into (deletable, kept, not_backed_up) by the cutoff."""
    to_delete, not_backed_up = [], []
    kept = 0
    for album in client.list_albums():
        for photo in client.iter_album_photos(album["id"]):
            sort_date = str(photo.get("sortDate", ""))[:8]
            if not sort_date or sort_date >= cutoff:
                kept += 1
                continue
            key = photo_key(photo)
            if key in backup:
                to_delete.append(photo)
            else:
                not_backed_up.append(photo)
    return to_delete, kept, not_backed_up


def run_deletion(client, to_delete, log_path="prune_log.json"):
    """Delete photos with periodic verification; returns True if none failed."""
    deleted, failed = 0, 0
    log = []

    def save_log():
        with open(log_path, "w") as fh:
            json.dump(log, fh, indent=2)

    for photo in to_delete:
        try:
            client.delete_picture(photo["id"])
            deleted += 1
            log.append({"id": photo["id"], "filename": photo.get("filename")})
        except (requests.RequestException, RuntimeError) as exc:
            failed += 1
            print(f"  FAILED to delete {photo.get('filename')!r} (id {photo['id']}): {exc}")
            continue
        if deleted % 100 == 0:
            if not client.verify_deleted(photo["id"]):
                save_log()
                raise SystemExit(
                    f"VERIFICATION FAILED: photo {photo['id']} still exists after a "
                    f"'successful' delete. Stopping - {deleted} logged so far; "
                    "re-run to resume (already-deleted photos are skipped)."
                )
            print(f"  {deleted}/{len(to_delete)} deleted (verified)")
            save_log()

    save_log()
    print(f"\nDone: {deleted} deleted, {failed} failed. Log in {log_path}")
    if deleted:
        ok = client.verify_deleted(log[-1]["id"])
        print(
            "Final spot-check:",
            "deletion verified" if ok else "WARNING - last photo still present!",
        )
    return failed == 0


def resolve_cutoff(older_than_days, cutoff_date_str):
    """The cutoff date: an explicit --cutoff-date wins over --older-than-days."""
    if cutoff_date_str:
        return datetime.date.fromisoformat(cutoff_date_str)
    return datetime.date.today() - datetime.timedelta(days=older_than_days)


def main():
    """CLI entry point."""
    sys.stdout.reconfigure(line_buffering=True)
    parser = argparse.ArgumentParser(
        description="Delete Nixplay photos older than a cutoff (dry run by default)."
    )
    parser.add_argument(
        "--older-than-days",
        type=int,
        default=365,
        help="age threshold in days by upload date (default: 365)",
    )
    parser.add_argument(
        "--export-dir",
        default="nixplay_export",
        help="local export used as backup proof (default: ./nixplay_export)",
    )
    parser.add_argument(
        "--cutoff-date",
        metavar="YYYY-MM-DD",
        default=None,
        help="delete photos uploaded before this date (overrides --older-than-days)",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="actually delete; without this flag nothing is changed",
    )
    args = parser.parse_args()

    cutoff_date = resolve_cutoff(args.older_than_days, args.cutoff_date)
    cutoff = cutoff_date.strftime("%Y%m%d")
    print(
        f"Cutoff: photos uploaded before {cutoff_date.isoformat()} "
        f"({'EXECUTE' if args.execute else 'DRY RUN'})"
    )

    backup = load_backup_index(args.export_dir)
    if not backup:
        raise SystemExit(
            f"No export manifests found under {args.export_dir!r} - "
            "refusing to run without a local backup to check against."
        )
    print(f"Backup index: {len(backup)} exported items found on disk.")

    client = NixplaySession(*get_credentials())
    to_delete, kept, not_backed_up = collect_candidates(client, cutoff, backup)

    print(f"\nOlder than cutoff and safely backed up locally: {len(to_delete)}")
    print(f"Newer than cutoff (kept): {kept}")
    if not_backed_up:
        print(f"SKIPPED - older than cutoff but NOT found in local export: {len(not_backed_up)}")
        for photo in not_backed_up[:10]:
            print(f"  not backed up: {photo.get('filename')!r} (id {photo.get('id')})")

    with open("prune_plan.json", "w") as fh:
        json.dump(
            [
                {"id": p.get("id"), "filename": p.get("filename"), "sortDate": p.get("sortDate")}
                for p in to_delete
            ],
            fh,
            indent=2,
        )
    print("Full list written to prune_plan.json")

    if not args.execute:
        print(
            "\nDry run only - nothing was deleted. "
            "Re-run with --execute to delete the photos listed above."
        )
        return 0

    print(f"\nDeleting {len(to_delete)} photos from Nixplay...")
    return 0 if run_deletion(client, to_delete) else 1


if __name__ == "__main__":
    sys.exit(main())
