"""Tests for nixplay_playlists slide handling."""

import datetime

from nixplay_api import photo_key
from nixplay_playlists import old_slides, slide_timestamp, slide_to_photo


def test_slide_timestamp_handles_seconds_and_milliseconds():
    sec = slide_timestamp({"timestamp": 1650000000})
    ms = slide_timestamp({"timestamp": 1650000000000})
    assert sec == ms
    assert slide_timestamp({"timestamp": 0}) is None
    assert slide_timestamp({}) is None


def test_slide_to_photo_maps_fields():
    slide = {
        "dbId": 42,
        "filename": "IMG_1.jpg",
        "caption": "hi",
        "timestamp": 1650000000,
    }
    photo = slide_to_photo(slide)
    assert photo["id"] == 42
    assert photo["filename"] == "IMG_1.jpg"
    assert photo["caption"] == "hi"
    assert photo["sortDate"] == datetime.datetime.fromtimestamp(1650000000).strftime("%Y%m%d%H%M%S")


def test_slide_to_photo_fallbacks():
    photo = slide_to_photo({"dbId": 7, "mediaItemId": "abc"})
    assert photo["filename"] == "abc"
    assert photo["sortDate"] is None


def test_merge_additions_dedupes_and_keeps_order():
    from nixplay_playlists import merge_additions

    src = [{"dbId": 3}, {"dbId": 1}, {"dbId": 2}]
    assert merge_additions(src, {1}) == [3, 2]
    assert merge_additions(src, set()) == [3, 1, 2]
    assert merge_additions([], {1}) == []


def test_old_slides_filters_by_cutoff():
    cutoff = datetime.datetime(2025, 7, 21)
    old = {"dbId": 1, "timestamp": int(datetime.datetime(2023, 1, 1).timestamp())}
    new = {"dbId": 2, "timestamp": int(datetime.datetime(2026, 1, 1).timestamp())}
    undated = {"dbId": 3}
    result = old_slides([old, new, undated], cutoff)
    assert [photo_key(s) for s in result] == ["1"]


def test_video_still_name():
    from nixplay_playlists import video_still_name

    assert video_still_name("clip.mp4") == "clip (video still).jpg"
    assert video_still_name("a b.MOV") == "a b (video still).jpg"
