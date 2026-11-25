"""Sàng lọc trước khi tải: giấy phép, thời lượng, luật loại trừ dùng CHUNG với validator – không truy cập mạng."""
from __future__ import annotations

import json

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
            "license": "Creative Commons Attribution license (reuse allowed)",
            "description": "Official music video of Song B.", "tags": ["indie", "pop"]}
    base.update(kw)
    return base


def mv_rules(**kw):
    """Luật sàng lọc dùng CHUNG file tiêu chí với validator (150–420 s)."""
    params = dict(min_duration=150, max_duration=420, category="music_video")
    params.update(kw)
    return dl.Rules(**params)


def test_normalize_vietnamese():
    assert dl.normalize("ANH TRAI VƯỢT NGÀN  CHÔNG GAI") == "anh trai vuot ngan chong gai"
    assert dl.normalize("Đêm nhạc") == "dem nhac"


def test_evaluate_duration_window():
    r = mv_rules()
    assert dl.evaluate(info(), r).accepted
    assert dl.evaluate(info(duration=150), r).accepted and dl.evaluate(info(duration=420), r).accepted
    assert "quá ngắn" in dl.evaluate(info(duration=149), r).reason
    assert "quá dài" in dl.evaluate(info(duration=421), r).reason
    assert "quá dài" in dl.evaluate(info(duration=450), r).reason      # 7 phút 30 giây
    assert not dl.evaluate(info(license=None), r).accepted
    assert not dl.evaluate(info(duration=None), r).accepted
    assert not dl.evaluate(info(live_status="is_live"), r).accepted


@pytest.mark.parametrize("title", [
    "Artist A - Song B (Lyric Video)", "Song B | Official Audio", "Song B [Visualizer]",
    "Song B 1 HOUR", "Song B (slowed + reverb)", "Song B karaoke", "Song B – Behind The Scenes",
    "Song B | Lời bài hát", "Song B (AI Generated Music Video)", "Song B MV Teaser",
])
def test_evaluate_excludes_non_mv_titles(title):
    cand = dl.evaluate(info(title=title), mv_rules())
    assert not cand.accepted and "luật loại trừ" in cand.reason and cand.screen_result == "reject"


def test_weak_signal_needs_review_not_reject():
    cand = dl.evaluate(info(title="Papers and Pens - Official Music Video | Dukamer x Suno"), mv_rules())
    assert cand.accepted and cand.screen_result == "review" and "suno" in " ".join(cand.weak_signals)


def test_description_mentioning_ai_does_not_reject():
    cand = dl.evaluate(info(description="Color grading assisted by AI tools. Not an AI generated video."),
                       mv_rules())
    assert cand.accepted and cand.screen_result == "review"


def test_cli_exclude_keyword_adds_strong_rule():
    cand = dl.evaluate(info(title="Song B (Official MV) – Dance Cover"), mv_rules(exclude_keywords=["dance cover"]))
    assert not cand.accepted


def test_exclude_matches_whole_words_only():
    # "audio" không được khớp nhầm "Audiovisual"
    cand = dl.evaluate(info(title="Audiovisual Dreams (Official MV)"), mv_rules())
    assert cand.accepted and cand.screen_result == "pass"


def test_evaluate_trusted_channel():
    r = mv_rules(trusted_channels={"UCartist": "Artist A"})
    assert dl.evaluate(info(), r).accepted
    reup = dl.evaluate(info(channel="Reup Channel", channel_id="UCfake"), r)
    assert not reup.accepted and "kênh tin cậy" in reup.reason


def test_load_preset():
    preset = dl.load_preset("music_video")
    assert preset["queries"] and "exclude_keywords" not in preset   # tiêu chí chỉ nằm trong dataset_criteria.json
    with pytest.raises(KeyError):
        dl.load_preset("khong_co")
    with pytest.raises(KeyError):
        dl.load_preset("atvncg")          # preset định hướng cũ đã bỏ


def test_run_downloads_only_screened_and_stays_pending(settings, monkeypatch):
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
    assert (src["filename"], src["license"], src["channel_id"], src["license_verified"]) == \
        ("yt_good.mp4", "CC BY", "UCartist", "no")                        # khai báo, chưa xác minh
    saved = json.loads((settings.paths.source_info / "yt_good.mp4.json").read_text(encoding="utf-8"))
    assert saved["description"] == "Official music video of Song B." and saved["tags"] == ["indie", "pop"]
    video = read_csv(settings.paths.videos_csv)[0]
    assert (video["video_id"], video["license"], video["source"], video["title"], video["validation_status"]) == \
        ("MV_001", "CC BY", "youtube", "Artist A - Song B (Official Music Video)", "pending_review")


def test_run_limit(settings, monkeypatch):
    monkeypatch.setattr(dl, "inspect", lambda url, rules: dl.evaluate(info(id=url, webpage_url=url), rules))
    summary = dl.run(settings, ["a", "b", "c"], dry_run=True, max_height=720, rules=mv_rules(), limit=2)
    assert len(summary["accepted"]) == 2


def test_run_dry_run_writes_candidates_and_downloads_nothing(settings, monkeypatch):
    infos = {"ok": info(id="ok", webpage_url="ok"), "lyric": info(id="ly", webpage_url="lyric", title="X (Lyrics)"),
             "suno": info(id="su", webpage_url="suno", title="X (Official MV) | made in Suno")}
    monkeypatch.setattr(dl, "inspect", lambda url, rules: dl.evaluate(infos[url], rules))
    monkeypatch.setattr(dl, "download", lambda *a: pytest.fail("không được tải khi dry-run"))
    summary = dl.run(settings, ["ok", "lyric", "suno"], dry_run=True, max_height=720, rules=mv_rules())
    assert len(summary["accepted"]) == 2 and not summary["downloaded"]
    rows = read_csv(settings.paths.metadata / "download_candidates.csv")
    assert {(r["url"], r["screen_result"]) for r in rows} == {("ok", "pass"), ("lyric", "reject"), ("suno", "review")}
    assert read_csv(settings.paths.videos_csv) == []
    assert [p.name for p in settings.paths.raw_videos.iterdir()] == [".gitkeep"]


def test_metadata_failure_is_reported(settings, monkeypatch):
    def boom(url, rules):
        raise RuntimeError("network down")
    monkeypatch.setattr(dl, "inspect", boom)
    summary = dl.run(settings, ["x"], dry_run=True, max_height=720, rules=mv_rules())
    assert summary["failed"] and summary["failed"][0]["error"] == "network down"
