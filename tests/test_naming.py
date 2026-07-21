"""Tests for filename construction in nixplay_export."""

from PIL import Image

from nixplay_export import (
    dest_rel_name,
    exif_short_date,
    is_video,
    pick_url,
    sanitize,
    sortdate_short_date,
    target_name,
    unique_path,
)


def make_jpeg(path, exif_datetime=None):
    img = Image.new("RGB", (4, 4))
    if exif_datetime is not None:
        exif = img.getexif()
        exif[306] = exif_datetime
        img.save(path, exif=exif)
    else:
        img.save(path)
    return path


def test_sanitize_replaces_separators():
    assert sanitize("a:b/c") == "a-b-c"
    assert sanitize("  line\nbreak\ttab  ") == "line break tab"


def test_sortdate_short_date():
    assert sortdate_short_date("20210417084351") == "210417 0843"
    assert sortdate_short_date(None) is None
    assert sortdate_short_date("garbage") is None


def test_exif_short_date(tmp_path):
    path = make_jpeg(tmp_path / "a.jpg", "2016:10:18 17:25:20")
    assert exif_short_date(path) == "161018 1725"


def test_exif_short_date_absent(tmp_path):
    path = make_jpeg(tmp_path / "b.jpg")
    assert exif_short_date(path) is None


def test_exif_short_date_not_an_image(tmp_path):
    path = tmp_path / "c.jpg"
    path.write_bytes(b"not an image")
    assert exif_short_date(path) is None


def test_target_name_with_caption_and_sortdate_fallback(tmp_path):
    path = tmp_path / "x.bin"
    path.write_bytes(b"")
    photo = {"filename": "IMG_7994.jpg", "caption": "Sam the dog", "sortDate": "20240303171800"}
    assert target_name(photo, path) == "240303 1718 Sam the dog -- IMG_7994.jpg"


def test_target_name_prefers_exif_over_sortdate(tmp_path):
    path = make_jpeg(tmp_path / "d.jpg", "2016:10:18 17:25:20")
    photo = {"filename": "d.jpg", "caption": "", "sortDate": "20240303171800"}
    assert target_name(photo, path) == "161018 1725 d.jpg"


def test_target_name_video_uses_sortdate_not_exif(tmp_path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"")
    photo = {"filename": "clip.mp4", "caption": "", "sortDate": "20210417084351"}
    assert target_name(photo, path) == "210417 0843 clip.mp4"


def test_target_name_sanitizes_caption_and_filename(tmp_path):
    path = tmp_path / "y.bin"
    path.write_bytes(b"")
    photo = {"filename": "a:b/c.jpg", "caption": "x/y:z", "sortDate": None}
    assert target_name(photo, path) == "x-y-z -- a-b-c.jpg"


def test_unique_path_appends_counter(tmp_path):
    (tmp_path / "photo.jpg").write_bytes(b"")
    (tmp_path / "photo (2).jpg").write_bytes(b"")
    assert unique_path(str(tmp_path), "photo.jpg").endswith("photo (3).jpg")
    assert unique_path(str(tmp_path), "fresh.jpg").endswith("fresh.jpg")


def test_is_video_by_field_and_extension():
    assert is_video({"video": {"url": "x"}, "filename": "a.jpg"})
    assert is_video({"filename": "a.MOV"})
    assert not is_video({"filename": "a.jpg"})


def test_pick_url_photo_prefers_original():
    photo = {"filename": "a.jpg", "originalUrl": "orig", "url": "fallback"}
    assert pick_url(photo) == "orig"
    assert pick_url({"filename": "a.jpg", "url": "fallback"}) == "fallback"


def test_pick_url_video_uses_playback_never_still():
    photo = {
        "filename": "a.mp4",
        "originalUrl": "still.jpg",
        "url": "still.jpg",
        "video": {"url": "playback.mp4"},
    }
    assert pick_url(photo) == "playback.mp4"


def test_pick_url_video_without_playback_is_none():
    photo = {
        "filename": "a.mp4",
        "originalUrl": "still.jpg",
        "url": "still.jpg",
        "video": {"state": "PROCESSING"},
    }
    assert pick_url(photo) is None


def test_dest_rel_name_flat_matches_target_name(tmp_path):
    path = tmp_path / "x.bin"
    path.write_bytes(b"")
    photo = {"filename": "a.jpg", "caption": "", "sortDate": "20230404152000"}
    assert dest_rel_name(photo, path) == "230404 1520 a.jpg"


def test_dest_rel_name_by_year_uses_prefix(tmp_path):
    path = make_jpeg(tmp_path / "d.jpg", "2016:10:18 17:25:20")
    photo = {"filename": "d.jpg", "caption": "", "sortDate": "20230404152000"}
    assert dest_rel_name(photo, path, by_year=True) == "2016/161018 1725 d.jpg"


def test_dest_rel_name_by_year_metadata_fallback(tmp_path):
    path = make_jpeg(tmp_path / "z.jpg", "0000:00:00 00:00:00")
    photo = {"filename": "z.jpg", "caption": "", "sortDate": "20091231235959"}
    assert dest_rel_name(photo, path, by_year=True) == "2009/000000 0000 z.jpg"


def test_dest_rel_name_by_year_unknown(tmp_path):
    path = tmp_path / "u.bin"
    path.write_bytes(b"")
    photo = {"filename": "u.jpg", "caption": "", "sortDate": None}
    assert dest_rel_name(photo, path, by_year=True) == "unknown-year/u.jpg"
