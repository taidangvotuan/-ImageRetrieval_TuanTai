"""Test logic kiểm tra giấy phép / kênh / domain – không truy cập mạng."""
from __future__ import annotations

import pytest

from src.common.io_utils import read_csv
from src.dataset import download_videos as dl
from tests.conftest import make_video

@pytest.mark.parametrize("text,ok", [
    ("Creative Commons Attribution license (reuse allowed)", True),   # chuỗi YouTube trả về
    ("https://creativecommons.org/licenses/by/4.0/", True),
    ("CC BY-NC 4.0", False),
    ("Creative Commons Attribution-NonCommercial", False),
    ("Creative Commons Attribution-ShareAlike 4.0", False),
    ("Standard YouTube License", False),
    (None, False),
    ("", False),
])
def test_classify_license(text, ok):
    assert dl.classify_license(text)[0] is ok


def info(**kw):
    base = {"id": "abc", "title": "Artist A - Song B (Official Music Video)", "channel": "Artist A",
            "channel_id": "UCartist", "webpage_url": "https://youtu.be/abc", "duration": 225,
            "license": "Creative Commons Attribution license (reuse allowed)"}
    base.update(kw)
    return base


def mv_rules(**kw):
    preset = dl.load_preset("music_video")
    params = dict(min_duration=150, max_duration=330, category=preset["category"],
                  exclude_keywords=preset["exclude_keywords"])
    params.update(kw)
    return dl.Rules(**params)


def test_normalize_vietnamese():
    assert dl.normalize("ANH TRAI VƯỢT NGÀN  CHÔNG GAI") == "anh trai vuot ngan chong gai"
    assert dl.normalize("Đêm nhạc") == "dem nhac"


def test_evaluate_duration_window():
    r = mv_rules()
    assert dl.evaluate(info(), r).accepted
    assert dl.evaluate(info(duration=150), r).accepted and dl.evaluate(info(duration=330), r).accepted
    assert "quá ngắn" in dl.evaluate(info(duration=149), r).reason
    assert "quá dài" in dl.evaluate(info(duration=1452), r).reason     # video tổng hợp 24 phút
    assert not dl.evaluate(info(license=None), r).accepted
    assert not dl.evaluate(info(duration=None), r).accepted
    assert not dl.evaluate(info(live_status="is_live"), r).accepted


@pytest.mark.parametrize("title", [
    "Artist A - Song B (Lyric Video)", "Song B | Official Audio", "Song B [Visualizer]",
    "Song B 1 HOUR loop", "Song B (slowed + reverb)", "Song B karaoke",
])
def test_evaluate_excludes_non_mv_titles(title):
    cand = dl.evaluate(info(title=title), mv_rules())
    assert not cand.accepted and "loại trừ" in cand.reason


def test_exclude_matches_whole_words_only():
    # "audio" không được khớp nhầm "Audiovisual"
    assert dl.evaluate(info(title="Audiovisual Dreams (Official MV)"), mv_rules()).accepted


def test_evaluate_trusted_channel():
    r = mv_rules(trusted_channels={"UCartist": "Artist A"})
    assert dl.evaluate(info(), r).accepted
    reup = dl.evaluate(info(channel="Reup Channel", channel_id="UCfake"), r)
    assert not reup.accepted and "kênh tin cậy" in reup.reason


def test_load_preset_unknown():
    with pytest.raises(KeyError):
        dl.load_preset("khong_co")
    with pytest.raises(KeyError):
        dl.load_preset("atvncg")          # preset định hướng cũ đã bỏ


def test_run_downloads_only_accepted(settings, monkeypatch):
    infos = {"good": info(id="good", webpage_url="good"),
             "nc": info(id="nc", webpage_url="nc", license="CC BY-NC"),
             "long": info(id="long", webpage_url="long", duration=1800)}
    monkeypatch.setattr(dl, "inspect", lambda url, rules: dl.evaluate(infos[url], rules))
    downloaded = []

    def fake_download(cand, s, h, rules):
        downloaded.append(cand.video_id)
        return make_video(s.paths.raw_videos / f"yt_{cand.video_id}.mp4", seconds=3)

    monkeypatch.setattr(dl, "download", fake_download)
    summary = dl.run(settings, ["good", "nc", "long", "good"], dry_run=False, max_height=720, rules=mv_rules())
    assert downloaded == ["good"]
    assert {c["video_id"] for c in summary["rejected"]} == {"nc", "long"}  # URL trùng chỉ xét 1 lần
    src = read_csv(settings.paths.sources_csv)[0]
    assert (src["filename"], src["license"], src["channel_id"], src["category"]) == \
        ("yt_good.mp4", "CC BY", "UCartist", "music_video")
    video = read_csv(settings.paths.videos_csv)[0]
    assert (video["video_id"], video["license"], video["source"], video["category"], video["title"]) == \
        ("MV_001", "CC BY", "youtube", "music_video", "Artist A - Song B (Official Music Video)")


def test_run_limit(settings, monkeypatch):
    monkeypatch.setattr(dl, "inspect", lambda url, rules: dl.evaluate(info(id=url, webpage_url=url), rules))
    summary = dl.run(settings, ["a", "b", "c"], dry_run=True, max_height=720, rules=mv_rules(), limit=2)
    assert len(summary["accepted"]) == 2


def test_run_dry_run_writes_candidates_and_downloads_nothing(settings, monkeypatch):
    infos = {"ok": info(id="ok", webpage_url="ok"), "lyric": info(id="ly", webpage_url="lyric", title="X (Lyrics)")}
    monkeypatch.setattr(dl, "inspect", lambda url, rules: dl.evaluate(infos[url], rules))
    monkeypatch.setattr(dl, "download", lambda *a: pytest.fail("không được tải khi dry-run"))
    summary = dl.run(settings, ["ok", "lyric"], dry_run=True, max_height=720, rules=mv_rules())
    assert len(summary["accepted"]) == 1 and not summary["downloaded"]
    rows = read_csv(settings.paths.metadata / "download_candidates.csv")
    assert {(r["url"], r["accepted"]) for r in rows} == {("ok", "yes"), ("lyric", "no")}
    assert read_csv(settings.paths.videos_csv) == [] and not any(settings.paths.raw_videos.iterdir()) or \
        [p.name for p in settings.paths.raw_videos.iterdir()] == [".gitkeep"]


def test_metadata_failure_is_reported(settings, monkeypatch):
    def boom(url, rules):
        raise RuntimeError("network down")
    monkeypatch.setattr(dl, "inspect", boom)
    summary = dl.run(settings, ["x"], dry_run=True, max_height=720, rules=mv_rules())
    assert summary["failed"] and summary["failed"][0]["error"] == "network down"
