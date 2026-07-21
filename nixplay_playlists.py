#!/usr/bin/env python3
"""Export shared playlist photos and unlink old playlist items.

Playlists can contain photos shared from OTHER Nixplay accounts. Those
never appear in your own albums, so nixplay-export misses them and
nixplay-prune cannot (and should not) delete them. This tool:

  export  - download every playlist photo that is not already in the
            local backup (i.e. shared photos), into one folder per
            playlist under --export-dir, with the usual _manifest.json /
            _metadata.json so the backup index picks them up.
  unlink  - remove playlist items older than the cutoff from playlists.
            This only unlinks them from the frame's rotation; the photos
            remain in their owners' Nixplay accounts. Dry run unless
            --execute is given, and an item is only unlinked if its
            photo is verifiably present in the local backup.

Usage:
    nixplay-playlists export --export-dir DIR
    nixplay-playlists unlink --export-dir DIR [--older-than-days N] [--execute]
"""

import argparse
import datetime
import json
import os
import sys

import requests

from nixplay_api import NixplaySession, get_credentials, photo_key
from nixplay_export import download, sanitize
from nixplay_prune import load_backup_index


def slide_timestamp(slide):
    """When the slide was added, as a datetime (None if unrecorded)."""
    ts = slide.get("timestamp") or 0
    if ts > 1e11:  # milliseconds
        ts /= 1000
    return datetime.datetime.fromtimestamp(ts) if ts else None


def video_still_name(filename):
    """Honest name for a shared video's still frame (the video itself is
    only downloadable by the account that owns it)."""
    stem, _ = os.path.splitext(filename)
    return f"{stem} (video still).jpg"


def slide_to_photo(slide):
    """Adapt a playlist slide dict to the shape target_name expects."""
    dt = slide_timestamp(slide)
    return {
        "id": slide.get("dbId"),
        "filename": slide.get("filename") or slide.get("mediaItemId") or "unnamed",
        "caption": slide.get("caption") or "",
        "sortDate": dt.strftime("%Y%m%d%H%M%S") if dt else None,
    }


def old_slides(slides, cutoff):
    """Slides added before the cutoff datetime."""
    return [s for s in slides if (d := slide_timestamp(s)) and d < cutoff]


def cmd_export(client, args):
    """Download every playlist photo missing from the local backup."""
    backup = load_backup_index(args.export_dir)
    print(f"Backup index: {len(backup)} items already exported.")
    seen = set(backup)
    done = failed = videos = 0

    for playlist in client.list_playlists():
        if not playlist.get("picture_count"):
            continue
        slides = list(client.iter_playlist_slides(playlist["id"]))
        fresh = [s for s in slides if photo_key(s) not in seen]
        if not fresh:
            print(f"'{playlist['name']}': all {len(slides)} slides already backed up.")
            continue

        title = sanitize(playlist["name"]) or f"playlist-{playlist['id']}"
        dest = os.path.join(args.export_dir, f"Playlist {title}")
        os.makedirs(dest, exist_ok=True)
        manifest_path = os.path.join(dest, "_manifest.json")
        manifest = {}
        if os.path.exists(manifest_path):
            with open(manifest_path) as fh:
                manifest = json.load(fh)

        print(f"'{playlist['name']}': {len(fresh)} of {len(slides)} slides need export")
        for slide in fresh:
            key = photo_key(slide)
            url = slide.get("originalUrl")
            if not url:
                print(f"  no originalUrl for {slide.get('filename')!r} (id {key}) - skipping.")
                failed += 1
                continue
            photo = slide_to_photo(slide)
            if slide.get("mediaType") == "video":
                # originalUrl of a shared video is only a JPEG still frame;
                # the real video is only accessible to its owning account.
                videos += 1
                photo["filename"] = video_still_name(photo["filename"])
            try:
                saved = download(client, url, dest, photo, key, by_year=args.by_year)
            except requests.RequestException as exc:
                print(f"  FAILED {slide.get('filename')!r} (id {key}): {exc}")
                failed += 1
                continue
            manifest[key] = saved
            seen.add(key)
            done += 1
            if done % 100 == 0:
                print(f"  {done} downloaded so far...")
                with open(manifest_path, "w") as fh:
                    json.dump(manifest, fh, indent=2)

        with open(manifest_path, "w") as fh:
            json.dump(manifest, fh, indent=2)
        with open(os.path.join(dest, "_metadata.json"), "w") as fh:
            json.dump(slides, fh, indent=2)

    print(
        f"\nExport done: {done} downloaded, {failed} failed"
        + (
            f", {videos} shared videos saved as still frames (originals need the owning account)"
            if videos
            else ""
        )
    )
    return 1 if failed else 0


def cmd_unlink(client, args):
    """Remove old, locally backed-up items from playlists (dry run default)."""
    cutoff_date = datetime.date.today() - datetime.timedelta(days=args.older_than_days)
    cutoff = datetime.datetime.combine(cutoff_date, datetime.time())
    print(
        f"Cutoff: playlist items added before {cutoff_date.isoformat()} "
        f"({'EXECUTE' if args.execute else 'DRY RUN'})"
    )

    backup = load_backup_index(args.export_dir)
    if not backup:
        raise SystemExit(
            f"No export manifests found under {args.export_dir!r} - "
            "refusing to run without a local backup to check against."
        )
    print(f"Backup index: {len(backup)} exported items found on disk.")

    plan, not_backed_up = [], []
    for playlist in client.list_playlists():
        if not playlist.get("picture_count"):
            continue
        slides = list(client.iter_playlist_slides(playlist["id"]))
        old = old_slides(slides, cutoff)
        for slide in old:
            entry = (playlist, slide)
            (plan if photo_key(slide) in backup else not_backed_up).append(entry)
        if old:
            print(f"'{playlist['name']}': {len(old)} of {len(slides)} items are old")

    print(f"\nOld items backed up locally (will unlink): {len(plan)}")
    if not_backed_up:
        print(f"SKIPPED - old but NOT in local backup: {len(not_backed_up)}")
        for playlist, slide in not_backed_up[:10]:
            print(f"  not backed up: {slide.get('filename')!r} in '{playlist['name']}'")

    if not args.execute:
        print("\nDry run only - nothing was unlinked. Re-run with --execute to proceed.")
        return 0

    removed = failed = 0
    for playlist, slide in plan:
        try:
            client.remove_playlist_item(playlist["id"], slide["playlistItemId"])
            removed += 1
        except (requests.RequestException, RuntimeError) as exc:
            failed += 1
            print(f"  FAILED to unlink {slide.get('filename')!r} from '{playlist['name']}': {exc}")
        if removed % 100 == 0 and removed:
            print(f"  {removed}/{len(plan)} unlinked")

    print(f"\nUnlink done: {removed} removed, {failed} failed.")

    print("Verifying...")
    leftover = 0
    for playlist in client.list_playlists():
        if not playlist.get("picture_count"):
            continue
        slides = list(client.iter_playlist_slides(playlist["id"]))
        old = old_slides(slides, cutoff)
        if old:
            leftover += len(old)
            print(f"  '{playlist['name']}' still has {len(old)} old items")
    print(
        "Verification:",
        "all clean"
        if leftover == 0
        else f"{leftover} old items remain (check skipped/not-backed-up above)",
    )
    return 1 if failed or leftover else 0


def merge_additions(src_slides, dst_ids):
    """Picture IDs from src that are not yet in dst, in playlist order."""
    return [s["dbId"] for s in src_slides if s["dbId"] not in dst_ids]


def cmd_merge(client, args):
    """Merge source playlist into dest, verify, then delete the source."""
    playlists = {p["name"]: p for p in client.list_playlists()}
    for name in (args.source, args.dest):
        if name not in playlists:
            raise SystemExit(f"No playlist named {name!r}. Available: {sorted(playlists)}")
    src, dst = playlists[args.source], playlists[args.dest]
    if src["id"] == dst["id"]:
        raise SystemExit("Source and destination are the same playlist.")

    dst_ids = {s["dbId"] for s in client.iter_playlist_slides(dst["id"])}
    src_slides = list(client.iter_playlist_slides(src["id"]))
    to_add = merge_additions(src_slides, dst_ids)
    dupes = len(src_slides) - len(to_add)
    print(
        f"'{args.source}' ({len(src_slides)} items) -> '{args.dest}' ({len(dst_ids)} items): "
        f"{len(to_add)} to add, {dupes} already present"
        + ("" if args.keep_source else "; source will be deleted after verification")
    )

    if not args.execute:
        print("Dry run only - nothing was changed. Re-run with --execute to merge.")
        return 0

    if to_add:
        client.add_playlist_items(dst["id"], to_add)

    merged_ids = {s["dbId"] for s in client.iter_playlist_slides(dst["id"])}
    missing = [s["dbId"] for s in src_slides if s["dbId"] not in merged_ids]
    if missing:
        raise SystemExit(
            f"VERIFICATION FAILED: {len(missing)} items from '{args.source}' are not in "
            f"'{args.dest}' after the merge. Source playlist was NOT deleted."
        )
    print(
        f"Verified: '{args.dest}' now has {len(merged_ids)} unique pictures, "
        f"including all {len(src_slides)} from '{args.source}'."
    )

    if args.keep_source:
        return 0
    client.delete_playlist(src["id"])
    remaining = [p["name"] for p in client.list_playlists()]
    if args.source in remaining:
        raise SystemExit(f"Deleted '{args.source}' but it still appears in the playlist list.")
    print(f"Deleted source playlist '{args.source}'.")
    return 0


def main():
    """CLI entry point."""
    sys.stdout.reconfigure(line_buffering=True)
    parser = argparse.ArgumentParser(description="Export and unlink Nixplay playlist items.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_export = sub.add_parser("export", help="download playlist photos missing from backup")
    p_export.add_argument("--export-dir", required=True)
    p_export.add_argument(
        "--by-year", action="store_true", help="place downloads in per-year subfolders"
    )

    p_unlink = sub.add_parser("unlink", help="remove old items from playlists")
    p_unlink.add_argument("--export-dir", required=True)
    p_unlink.add_argument("--older-than-days", type=int, default=365)
    p_unlink.add_argument("--execute", action="store_true")

    p_merge = sub.add_parser("merge", help="merge one playlist into another")
    p_merge.add_argument("--source", required=True, help="playlist to merge from (deleted after)")
    p_merge.add_argument("--dest", required=True, help="playlist to merge into")
    p_merge.add_argument(
        "--keep-source", action="store_true", help="do not delete the source playlist after merging"
    )
    p_merge.add_argument("--execute", action="store_true")

    args = parser.parse_args()
    client = NixplaySession(*get_credentials())
    if args.command == "export":
        return cmd_export(client, args)
    if args.command == "merge":
        return cmd_merge(client, args)
    return cmd_unlink(client, args)


if __name__ == "__main__":
    sys.exit(main())
