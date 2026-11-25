"""M2: frame sampler, phụ đề YouTube, OCR, CLI moderate_videos.

OCR dùng reader giả (trả chữ theo độ sáng frame) để kiểm tra LOGIC; một test dùng EasyOCR thật trên
video tổng hợp có chữ (bỏ qua nếu máy chưa tải mô hình EasyOCR). Không truy cập mạng.
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from src.dataset.moderate_videos import run as run_moderation
from src.dataset.scan_videos import register_source, scan, write_source_info
from src.dataset.validation.content_safety.lexicon import Lexicon
from src.dataset.validation.content_safety.ocr import OcrModerator
from src.dataset.validation.content_safety.subtitles import (base_language, choose_languages, moderate_subtitles,
                                                             parse_vtt)
from src.dataset.validation.content_safety.text_moderator import TextModerator
from src.dataset.validation.dataset_validator import validate
from src.video_processing.frame_sampler import SamplerStats, sample_frames
from tests.conftest import by_filename, make_settings, make_video
from tests.test_content_safety import SAFETY, write_lexicon

EN_LINE = "we keep walking home through the quiet city streets tonight"


def shades_video(path: Path, shades: list[int], fps: int = 2, size=(64, 48)) -> Path:
    """Video 1 frame / 0,5 s; mỗi phần tử là độ sáng của một frame (để reader giả nhận ra frame)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
    for v in shades:
        w.write(np.full((size[1], size[0], 3), v, np.uint8))
    w.release()
    return path


class FakeReader:
    """readtext(image) -> [(bbox, text, conf)] theo độ sáng trung bình của frame."""

    def __init__(self, table):
        self.table = table
        self.calls = 0

    def readtext(self, image):
        self.calls += 1
        level = int(round(float(image.mean()) / 10.0)) * 10
        return [(None, text, conf) for text, conf in self.table.get(level, [])]


@pytest.fixture
def lexicon_dir(tmp_path):
    return write_lexicon(tmp_path / "lex", {"en": [("zorbix", "profanity_or_abusive_language", "strong"),
                                                   ("glimpf", "profanity_or_abusive_language", "ambiguous")]})


@pytest.fixture
def moderator(lexicon_dir):
    return TextModerator(Lexicon.load(lexicon_dir), SAFETY)


def ocr_cfg(**kw):
    cfg = dict(SAFETY["ocr"], sample_every_sec=0.5, max_width=64)
    cfg.update(kw)
    return cfg


# ---------------------------------------------------------------- frame sampler
def test_sampler_uses_decoder_timestamps(tmp_path):
    path = make_video(tmp_path / "v.mp4", seconds=5.5, fps=10)
    stats = SamplerStats()
    frames = list(sample_frames(path, 1.0, stats=stats))
    assert [f.timestamp for f in frames] == pytest.approx([0, 1, 2, 3, 4, 5], abs=0.11)
    assert [f.index for f in frames] == [0, 10, 20, 30, 40, 50]
    assert stats.frames_read == 55 and stats.frames_sampled == 6 and stats.non_monotonic == 0
    assert stats.timestamp_source == "pts"


def test_sampler_interval_window_and_resize(tmp_path):
    path = make_video(tmp_path / "v.mp4", seconds=4, fps=10, size=(128, 96))
    assert [f.timestamp for f in sample_frames(path, 0.5, end_sec=1.6)] == pytest.approx([0, 0.5, 1.0, 1.5], abs=0.11)
    assert [f.timestamp for f in sample_frames(path, 1.0, start_sec=2.0)] == pytest.approx([2, 3], abs=0.11)
    assert next(sample_frames(path, 1.0, max_width=64)).image.shape == (48, 64, 3)
    with pytest.raises(ValueError):
        next(sample_frames(path, 0))


def test_sampler_unreadable_file(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    stats = SamplerStats()
    assert list(sample_frames(bad, 1.0, stats=stats)) == [] and stats.errors


# ---------------------------------------------------------------- phụ đề
VTT = """WEBVTT

00:00:01.000 --> 00:00:03.000
<c>hello</c> my <b>friend</b>

00:00:12.500 --> 00:00:14.000
we sing zorbix together

00:01:05.250 --> 00:01:07.000
we sing zorbix together
"""


def test_parse_vtt():
    cues = parse_vtt(VTT)
    assert [(c.start, c.text) for c in cues] == [(1.0, "hello my friend"), (12.5, "we sing zorbix together"),
                                                 (65.25, "we sing zorbix together")]
    assert parse_vtt("1\n00:00:02,000 --> 00:00:03,000\nsrt line\n")[0].text == "srt line"


def test_base_language_and_choice():
    assert [base_language(c) for c in ("en-US", "vi-orig", "pt_BR", "ko")] == ["en", "vi", "pt", "ko"]
    info = {"language": "vi", "subtitles": {"en": [], "live_chat": []},
            "automatic_captions": {"vi-orig": [], "vi": [], "fr": [], "de": []}}
    assert choose_languages(info, SAFETY["subtitles"]) == {"en": "manual", "vi-orig": "auto", "vi": "auto"}
    assert choose_languages({"automatic_captions": {"fr": []}}, SAFETY["subtitles"]) == {}   # bỏ bản dịch máy


def write_subs(directory: Path, items: dict[str, tuple[str, str]], error=None):
    directory.mkdir(parents=True, exist_ok=True)
    langs = []
    for code, (kind, text) in items.items():
        (directory / f"sub.{code}.vtt").write_text(text, encoding="utf-8")
        langs.append({"code": code, "kind": kind, "file": f"sub.{code}.vtt"})
    (directory / "manifest.json").write_text(json.dumps({"languages": langs, "error": error}), encoding="utf-8")
    return directory


def test_subtitle_flags_with_timestamp_never_auto_reject(moderator, tmp_path):
    r = moderate_subtitles(moderator, write_subs(tmp_path / "s", {"en": ("manual", VTT)}))
    assert r.status == "flagged" and len(r.flags) == 1                     # cùng từ chỉ gắn cờ một lần / file
    flag = r.flags[0]
    assert (flag.timestamp, flag.level, flag.auto_reject, flag.evidence) == (12.5, "strong", False, "z*****")


def test_subtitle_status_cases(moderator, tmp_path):
    clean = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n" + EN_LINE + "\n"
    assert moderate_subtitles(moderator, write_subs(tmp_path / "a", {"en": ("manual", clean)})).status == "safe"
    r = moderate_subtitles(moderator, write_subs(tmp_path / "b", {"th": ("manual", clean)}))
    assert r.status == "unknown" and r.language_status == "unsupported"
    assert moderate_subtitles(moderator, write_subs(tmp_path / "c", {})).status == "not_applicable"
    assert moderate_subtitles(moderator, write_subs(tmp_path / "d", {}, error="HTTP 429")).status == "unknown"
    assert moderate_subtitles(moderator, tmp_path / "never").method == "not_run"
    r = moderate_subtitles(moderator, write_subs(tmp_path / "e", {"vi": ("auto", VTT)}))
    assert r.flags[0].level == "ambiguous"                                  # phụ đề vi, lexicon en


# ---------------------------------------------------------------- OCR (reader giả)
TABLE = {0: [], 100: [(f"{EN_LINE} zorbix", 0.9)], 200: [(EN_LINE, 0.9)], 50: [("???", 0.1), ("???", 0.1)]}


def test_ocr_strong_on_two_frames_auto_rejects(moderator, tmp_path):
    path = shades_video(tmp_path / "v.mp4", [0, 100, 200, 100, 0])          # chữ vi phạm ở 2 frame
    r = OcrModerator(moderator, ocr_cfg(dedup_threshold=0), lambda langs: FakeReader(TABLE)).run(path)
    assert r.status == "flagged" and r.flags[0].auto_reject and r.flags[0].timestamp == pytest.approx(0.5, abs=0.11)


def test_ocr_strong_on_single_frame_only_flags(moderator, tmp_path):
    path = shades_video(tmp_path / "v.mp4", [0, 100, 200, 0])
    r = OcrModerator(moderator, ocr_cfg(dedup_threshold=0), lambda langs: FakeReader(TABLE)).run(path)
    assert r.status == "flagged" and not r.flags[0].auto_reject and "1 frame" in r.flags[0].note


def test_ocr_skips_similar_frames(moderator, tmp_path):
    path = shades_video(tmp_path / "v.mp4", [200, 200, 200, 200, 0])
    reader = FakeReader(TABLE)
    data = OcrModerator(moderator, ocr_cfg(), lambda langs: reader).read_video(path)
    assert data["stats"]["frames_skipped_similar"] == 3 and reader.calls == 2


@pytest.mark.parametrize("shades,status", [([0, 0, 0], "safe"), ([200, 0, 200], "safe"), ([50, 200, 50], "unknown")])
def test_ocr_status(moderator, tmp_path, shades, status):
    path = shades_video(tmp_path / "v.mp4", shades)
    r = OcrModerator(moderator, ocr_cfg(dedup_threshold=0), lambda langs: FakeReader(TABLE)).run(path)
    assert r.status == status


def test_ocr_unsupported_language_is_unknown(tmp_path):
    lex = write_lexicon(tmp_path / "lex", {"vi": [("đồ quạch", "profanity_or_abusive_language", "strong")]})
    mod = TextModerator(Lexicon.load(lex), SAFETY)
    path = shades_video(tmp_path / "v.mp4", [200, 0])
    r = OcrModerator(mod, ocr_cfg(dedup_threshold=0), lambda langs: FakeReader(TABLE)).run(path)
    assert r.status == "unknown" and r.language_status == "unsupported"


def test_real_easyocr_reads_text_on_synthetic_video(tmp_path, moderator):
    if not (Path.home() / ".EasyOCR" / "model").exists():
        pytest.skip("chưa tải mô hình EasyOCR")
    path = tmp_path / "text.mp4"
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 1, (640, 360))
    for _ in range(2):
        img = np.full((360, 640, 3), 255, np.uint8)
        cv2.putText(img, "HELLO WORLD", (40, 200), cv2.FONT_HERSHEY_SIMPLEX, 2.5, (0, 0, 0), 6)
        w.write(img)
    w.release()
    data = OcrModerator(moderator, ocr_cfg(max_width=640, sample_every_sec=1.0)).read_video(path)
    assert "HELLO" in " ".join(t for f in data["frames"] for t in f["texts"]).upper()


# ---------------------------------------------------------------- CLI moderate_videos
@pytest.fixture
def ds(tmp_path, lexicon_dir):
    s = make_settings(tmp_path, lexicon_dir=lexicon_dir)
    for name, shades in (("ok.mp4", [200, 0, 200, 0]), ("bad.mp4", [0, 100, 0, 100])):
        shades_video(s.paths.raw_videos / name, shades * 2)
        register_source(s, name, license="CC BY", source="youtube", title=f"{EN_LINE} (Official MV)",
                        source_url=f"https://www.youtube.com/watch?v={name[:3]}xxxxxxxx")
        write_source_info(s, name, {"title": f"{EN_LINE} (Official MV)", "age_limit": 0})
    scan(s)
    validate(s)
    return s


def fake_fetch(url, out_dir, cfg):
    write_subs(Path(out_dir), {"en": ("manual", "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n" + EN_LINE + "\n")})


def test_moderate_videos_end_to_end(ds):
    calls = []

    def factory(langs):
        calls.append(langs)
        return FakeReader(TABLE)

    summary = run_moderation(ds, ("subtitles", "ocr"), ocr_every=0.5, ocr_factory=factory, fetcher=fake_fetch)
    assert not summary["errors"] and len(summary["processed"]) == 4
    rows = by_filename(ds)
    assert rows["bad.mp4"]["validation_status"] == "rejected"                     # chữ vi phạm ≥ 2 frame
    assert rows["bad.mp4"]["rejection_reason"] == "profanity_or_abusive_language"
    assert rows["ok.mp4"]["validation_status"] == "pending_review"                # vẫn cần người duyệt
    vid = rows["ok.mp4"]["video_id"]
    assert (ds.paths.moderation / vid / "ocr.json").exists() and (ds.paths.moderation / vid / "ocr_text.json").exists()
    assert not any(ds.paths.processed.rglob("*.json"))                            # không ghi vào processed/
    # Checkpoint: chạy lại không OCR lại.
    again = run_moderation(ds, ("ocr",), ocr_every=0.5, ocr_factory=factory, fetcher=fake_fetch)
    assert again["processed"] == [] and len(again["skipped"]) == 2             # đã có kết quả cho đúng sha256
    assert len(calls) == 1                                                       # reader chỉ được tạo một lần


def test_moderate_videos_skips_technical_rejects(ds):
    rows = by_filename(ds)
    from src.common.io_utils import read_csv, write_csv
    from src.dataset.schema import VIDEO_COLUMNS
    data = read_csv(ds.paths.videos_csv)
    for r in data:
        if r["filename"] == "bad.mp4":
            r["duration_seconds"] = "9999"                                       # sai thời lượng -> lỗi kỹ thuật
    write_csv(ds.paths.videos_csv, VIDEO_COLUMNS, data)
    summary = run_moderation(ds, ("ocr",), ocr_every=0.5, ocr_factory=lambda l: FakeReader(TABLE), fetcher=fake_fetch)
    assert [vid for vid, *_ in summary["processed"]] == [rows["ok.mp4"]["video_id"]]
