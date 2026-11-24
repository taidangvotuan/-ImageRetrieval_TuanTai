"""Test logic kiểm tra giấy phép / kênh / domain – không truy cập mạng."""
from __future__ import annotations

import pytest

from src.common.io_utils import read_csv
from src.dataset import download_videos as dl
from tests.conftest import make_video

YEAH1 = "UC9G7oeTsVSoD2Z2Lt7eKdjQ"


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
    base = {"id": "abc", "title": "ANH TRAI VƯỢT NGÀN CHÔNG GAI | Công Diễn 5", "channel": "YeaH1 Channel",
            "channel_id": YEAH1, "webpage_url": "https://youtu.be/abc", "duration": 1200,
            "license": "Creative Commons Attribution license (reuse allowed)"}
    base.update(kw)
    return base


def test_normalize_vietnamese():
    assert dl.normalize("ANH TRAI VƯỢT NGÀN  CHÔNG GAI") == "anh trai vuot ngan chong gai"
    assert dl.normalize("Đêm nhạc") == "dem nhac"


def test_evaluate_basic_rules():
    r = dl.Rules(min_duration=600)
    assert dl.evaluate(info(), r).accepted
    assert "quá ngắn" in dl.evaluate(info(duration=120), r).reason
    assert not dl.evaluate(info(license=None), r).accepted
    assert not dl.evaluate(info(duration=None), r).accepted
    assert not dl.evaluate(info(live_status="is_live"), r).accepted


def test_evaluate_trusted_channel_and_title():
    preset = dl.load_preset("atvncg")
    r = dl.Rules(600, preset["category"], preset["series"], preset["trusted_channels"], preset["title_keywords"])
    assert dl.evaluate(info(), r).accepted
    # Kênh reup gắn CC BY -> từ chối dù giấy phép hợp lệ trên giấy
    reup = dl.evaluate(info(channel="Reup Channel", channel_id="UCfake"), r)
    assert not reup.accepted and "chính chủ" in reup.reason
    # Cùng kênh YeaH1 nhưng chương trình khác (không phải ca nhạc)
    other = dl.evaluate(info(title="Gia Đình Haha | tập 5"), r)
    assert not other.accepted and "từ khóa" in other.reason
    # So khớp không dấu: tiêu đề viết không dấu vẫn nhận
    assert dl.evaluate(info(title="anh trai vuot ngan chong gai concert"), r).accepted


def test_load_preset_unknown():
    with pytest.raises(KeyError):
        dl.load_preset("khong_co")


def test_run_downloads_only_accepted(settings, monkeypatch):
    infos = {"good": info(id="good", webpage_url="good"),
             "nc": info(id="nc", webpage_url="nc", license="CC BY-NC"),
             "short": info(id="short", webpage_url="short", duration=1)}
    monkeypatch.setattr(dl, "inspect", lambda url, rules: dl.evaluate(infos[url], rules))
    downloaded = []

    def fake_download(cand, s, h, rules):
        downloaded.append(cand.video_id)
        return make_video(s.paths.raw_videos / f"yt_{cand.video_id}.mp4", seconds=3)

    monkeypatch.setattr(dl, "download", fake_download)
    rules = dl.Rules(2, "music_show", "Anh Trai Vượt Ngàn Chông Gai")
    summary = dl.run(settings, ["good", "nc", "short", "good"], dry_run=False, max_height=720, rules=rules)
    assert downloaded == ["good"]
    assert {c["video_id"] for c in summary["rejected"]} == {"nc", "short"}  # URL trùng chỉ xét 1 lần
    src = read_csv(settings.paths.sources_csv)[0]
    assert (src["filename"], src["license"], src["channel_id"], src["category"]) == \
        ("yt_good.mp4", "CC BY", YEAH1, "music_show")
    video = read_csv(settings.paths.videos_csv)[0]
    assert (video["license"], video["source"], video["category"], video["series"]) == \
        ("CC BY", "youtube", "music_show", "Anh Trai Vượt Ngàn Chông Gai")


def test_run_limit(settings, monkeypatch):
    monkeypatch.setattr(dl, "inspect", lambda url, rules: dl.evaluate(info(id=url, webpage_url=url), rules))
    summary = dl.run(settings, ["a", "b", "c"], dry_run=True, max_height=720, rules=dl.Rules(600), limit=2)
    assert len(summary["accepted"]) == 2


def test_run_dry_run_downloads_nothing(settings, monkeypatch):
    monkeypatch.setattr(dl, "inspect", lambda url, rules: dl.evaluate(info(), rules))
    monkeypatch.setattr(dl, "download", lambda *a: pytest.fail("không được tải khi dry-run"))
    summary = dl.run(settings, ["x"], dry_run=True, max_height=720, rules=dl.Rules(600))
    assert len(summary["accepted"]) == 1 and not summary["downloaded"]


def test_metadata_failure_is_reported(settings, monkeypatch):
    def boom(url, rules):
        raise RuntimeError("network down")
    monkeypatch.setattr(dl, "inspect", boom)
    summary = dl.run(settings, ["x"], dry_run=True, max_height=720, rules=dl.Rules(600))
    assert summary["failed"] and summary["failed"][0]["error"] == "network down"
