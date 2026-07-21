# nixplay-tools

[![CI](https://github.com/TokyoDanInJapan/nixplay-tools/actions/workflows/ci.yml/badge.svg)](https://github.com/TokyoDanInJapan/nixplay-tools/actions/workflows/ci.yml)

Scripts for managing a [Nixplay](https://www.nixplay.com) photo frame library
via Nixplay's (unofficial, undocumented) web API.

- **`nixplay_export.py`** - Download every photo and video from all albums at
  full resolution. Files are named `YYMMDD HHMM Caption -- OriginalName.jpg`
  (EXIF capture date when available, upload date otherwise), following the
  naming scheme of the [RWTH Aachen nixplay-export
  guide](https://hci.rwth-aachen.de/nixplay-export). Raw album metadata is
  saved alongside as `_metadata.json`; the export is resumable via
  `_manifest.json`.
- **`nixplay_prune.py`** - Delete photos from the Nixplay cloud library that
  are older than a cutoff (by upload date, default 365 days). Dry run by
  default; requires `--execute` to delete. A photo is only ever deleted if it
  is verifiably present in a local export, and every deletion is validated
  against the API (Nixplay sessions expire after ~15 minutes and deletes then
  silently no-op - the script re-authenticates and verifies to catch this).
  The dry run writes the full deletion list to `prune_plan.json` for review;
  executed deletions are logged to `prune_log.json`.
- **`nixplay_playlists.py`** - Manage playlists, which can contain photos
  shared from *other* Nixplay accounts that albums never show:
  - `export` Downloads playlist photos missing from the local backup
    (for a shared video only its still frame is available - the full
    video can only be exported by the account that owns it);
  - `unlink` Removes items older than the cutoff from playlists (the photos
    stay in their owners' accounts); dry run by default, backup-interlocked,
    verified after;
  - `merge` Appends one playlist's pictures into another (deduplicated),
    verifies every picture arrived, and only then deletes the source
    playlist (`--keep-source` to skip deletion).
- **`nixplay_verify.py`** - Offline integrity check of a local backup: every
  manifest entry exists and is non-empty, video files contain real video
  data, and (`--md5`) image content matches the checksums Nixplay recorded.
- **`nixplay_sort.py`** - Reorganize a local backup into per-year folders
  (capture year from the filename prefix, upload year as fallback),
  rewriting manifests so prune/verify keep working. Dry run by default,
  idempotent.

## Setup

Requires Python 3.9+.

```sh
pip install -e .       # installs the nixplay-* commands
cp .env.example .env   # then fill in your Nixplay credentials
# (NIXPLAY_EMAIL / NIXPLAY_PASSWORD environment variables work too,
#  and take precedence over .env)
```

## Usage

```sh
nixplay-export [--out DIR] [--albums SUBSTRING] [--by-year] [--flat]

nixplay-prune [--older-than-days N | --cutoff-date YYYY-MM-DD] [--export-dir DIR]  # dry run
nixplay-prune --export-dir DIR --execute                # delete

nixplay-playlists export --export-dir DIR [--by-year]
nixplay-playlists unlink --export-dir DIR [--older-than-days N] [--execute]
nixplay-playlists merge --source "Old Playlist" --dest "Main Playlist" [--execute]

nixplay-verify --export-dir DIR [--md5]
nixplay-sort --export-dir DIR [--execute]
```

Running the scripts directly (`python3 nixplay_export.py`) works too.

For an incremental export straight into an existing year-sorted backup
(single album, no album subfolder, new files into `<year>/`):

```sh
nixplay-export --out /path/to/backup --flat --by-year
```

## Development

```sh
pip install -e ".[dev]"
ruff check . && ruff format --check .   # lint
pytest --cov                            # tests + coverage (no network required)
```

CI runs lint and tests (with an 80% coverage gate) on every push and
pull request, on Python 3.9 and 3.12.

## Notes

- Nixplay's API is unofficial and may change without warning. Endpoint
  knowledge derives from [anitschke/go-nixplay](https://github.com/anitschke/go-nixplay)
  and network observation.
- For videos, the API's `url`/`originalUrl` fields point to a JPEG still
  frame; the actual MP4 is fetched from the signed playback URL in the
  `video` object.
- **`nixplay_prune.py --execute` permanently deletes photos from your Nixplay
  account.** Keep (and verify) an export before using it.
