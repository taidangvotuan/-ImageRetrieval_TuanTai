"""Unit test hàm quyết định (§8.1): accepted / rejected / pending_review từ metadata + duyệt tay.

Dùng metadata mẫu (không cần file video): quyết định phụ thuộc vào luật, kiểm tra kỹ thuật và
quyết định duyệt – đúng như hệ thống thật.
"""
from __future__ import annotations

import pytest

from src.dataset.validation import metadata_validator as mv
from src.dataset.validation.dataset_validator import decide
from src.dataset.validation.exclusion_rules import RuleSet
from src.dataset.validation.review_store import ReviewState
from src.dataset.validation.video_filter import assess_content
from tests.conftest import FULL_REVIEW, make_settings


@pytest.fixture(scope="module")
def rules():
    return RuleSet.from_settings()


@pytest.fixture
def mv_cfg(tmp_path):
    return make_settings(tmp_path, min_video_duration_sec=150, max_video_duration_sec=420, min_long_side_px=1280)


def run(rules, settings, title="Song X (Official MV)", description="", duration=240, size=(1920, 1080),
        filename="yt_x.mp4", review=None, uploader="Artist X"):
    tech = [i for i in (mv.check_duration(duration, settings), mv.check_resolution(*size, settings),
                        mv.check_format(filename, settings)) if i]
    assessment = assess_content(rules, title=title, filename=filename, description=description, uploader=uploader)
    return decide(assessment, tech, ReviewState(decisions=dict(review or {}), reviewers=["Người duyệt"]), rules)


# ---------------------------------------------------------------- bảng §8.1
def test_official_mv_4min_accepted_only_when_fully_verified(rules, mv_cfg):
    assert run(rules, mv_cfg, review=FULL_REVIEW).validation_status == "accepted"
    partly = {k: v for k, v in FULL_REVIEW.items() if k != "source_status"}
    d = run(rules, mv_cfg, review=partly)
    assert d.validation_status == "pending_review" and "nguồn gốc" in d.notes[0]


def test_unverified_source_is_pending_review(rules, mv_cfg):
    d = run(rules, mv_cfg)                    # không có quyết định duyệt nào
    assert d.validation_status == "pending_review" and not d.reasons
    assert set(d.statuses.values()) == {"unknown"} and d.content_type == "unknown"   # không tự điền confirmed


@pytest.mark.parametrize("title,reason", [
    ("Song X (AI Generated Music Video)", "ai_generated"),
    ("Song X – Behind The Scenes", "behind_the_scenes"),
    ("Phía Sau Hậu Trường MV Song X", "behind_the_scenes"),
    ("Song X (Lyric Video)", "lyrics_video"),
    ("Song X | Lời Bài Hát", "lyrics_video"),
    ("Song X Official Teaser", "teaser"),
    ("Reaction to Song X", "reaction_or_review"),
    ("Song X (Live)", "live_performance"),
])
def test_excluded_content_rejected_even_without_review(rules, mv_cfg, title, reason):
    d = run(rules, mv_cfg, title=title)
    assert d.validation_status == "rejected" and reason in d.reasons


def test_ai_generated_confirmed_by_reviewer_is_rejected(rules, mv_cfg):
    review = {**FULL_REVIEW, "ai_generated_status": "rejected"}   # người duyệt: hình ảnh do AI tạo
    d = run(rules, mv_cfg, review=review)
    assert d.validation_status == "rejected" and d.reasons == ["ai_generated"]


@pytest.mark.parametrize("title,description,uploader", [
    ("BTS (방탄소년단) 'Dynamite' Official MV", "", "BANGTANTV"),          # tên nghệ sĩ BTS
    ("Song X (Official MV) [Vietsub + Lời bài hát]", "", "Artist X"),     # MV có phụ đề lời
    ("Song X (Official MV)", "Lời bài hát: ...", "Artist X"),             # lời trong mô tả
    ("Song X (Official MV) – VFX & CGI by Studio Y", "", "Artist X"),     # VFX thông thường
    ("Song X (Official MV) – AI-assisted color grading", "", "Artist X"),  # AI hậu kỳ nhỏ
    ("Song X (Official MV)", "Colour grading assisted by AI.", "Artist X"),
])
def test_not_automatically_rejected(rules, mv_cfg, title, description, uploader):
    d = run(rules, mv_cfg, title=title, description=description, uploader=uploader)
    assert d.validation_status == "pending_review" and not d.reasons
    # Sau khi người duyệt xác minh đầy đủ thì được chấp nhận.
    d = run(rules, mv_cfg, title=title, description=description, uploader=uploader, review=FULL_REVIEW)
    assert d.validation_status == "accepted"


@pytest.mark.parametrize("seconds,ok", [
    (120, False),      # 2 phút
    (149, False), (150, True),
    (240, True),       # 4 phút
    (420, True), (421, False),
    (450, False),      # 7 phút 30 giây
    (None, False),     # không xác định
])
def test_duration_rule(rules, mv_cfg, seconds, ok):
    d = run(rules, mv_cfg, duration=seconds, review=FULL_REVIEW)
    assert (d.validation_status == "accepted") is ok
    if not ok:
        assert d.validation_status == "rejected"
        assert d.reasons[0] in ("duration_out_of_range", "duration_unknown")


def test_technical_rejection_cannot_be_overridden(rules, mv_cfg):
    for kwargs, reason in (({"size": (854, 480)}, "low_resolution"), ({"filename": "x.mkv"}, "not_mp4"),
                           ({"duration": 90}, "duration_out_of_range")):
        d = run(rules, mv_cfg, review=FULL_REVIEW, **kwargs)
        assert d.validation_status == "rejected" and reason in d.reasons


def test_resolution_720p_and_above_ok(rules, mv_cfg):
    for size in ((1280, 720), (1920, 1080), (3840, 2160), (1280, 536)):
        assert run(rules, mv_cfg, size=size, review=FULL_REVIEW).validation_status == "accepted"


def test_duplicate_content_rejected(rules, mv_cfg):
    dup = mv.TechIssue("duplicate_content", "trùng với MV_001", "MV_002", "b.mp4",
                       reason="duplicate_content", duplicate_of="MV_001")
    assessment = assess_content(rules, title="Song X (Official MV)")
    d = decide(assessment, [dup], ReviewState(decisions=dict(FULL_REVIEW)), rules)
    assert d.validation_status == "rejected" and d.reasons == ["duplicate_content"]


def test_reviewer_can_override_content_rule_with_evidence(rules, mv_cfg):
    # Bài hát tên "Lyrics" của nghệ sĩ: luật bắt nhầm, người duyệt xác nhận đây là MV chính thức.
    title = "Artist Y – Lyrics (Official Music Video)"
    assert run(rules, mv_cfg, title=title).validation_status == "rejected"
    d = run(rules, mv_cfg, title=title, review=FULL_REVIEW)
    assert d.validation_status == "accepted" and any("ghi đè" in n for n in d.notes)


def test_reviewer_marks_not_official(rules, mv_cfg):
    d = run(rules, mv_cfg, review={**FULL_REVIEW, "content_type": "other"})
    assert d.validation_status == "rejected" and d.reasons == ["not_official_music_video"]


def test_license_or_source_rejected_by_reviewer(rules, mv_cfg):
    assert run(rules, mv_cfg, review={**FULL_REVIEW, "license_status": "rejected"}).reasons == ["license_rejected"]
    assert run(rules, mv_cfg, review={**FULL_REVIEW, "source_status": "rejected"}).reasons == ["source_rejected"]


def test_not_applicable_is_not_enough_for_acceptance(rules, mv_cfg):
    d = run(rules, mv_cfg, review={**FULL_REVIEW, "lyrics_status": "not_applicable"})
    assert d.validation_status == "pending_review"


def test_stale_review_is_ignored(rules, mv_cfg):
    from src.dataset.validation.review_store import state_for
    reviews = [{"reviewed_at": "2026-01-01T00:00:00+00:00", "video_id": "MV_001", "filename": "a.mp4",
                "sha256": "OLD", "reviewer": "A", "evidence": "x", **FULL_REVIEW}]
    state = state_for(reviews, "MV_001", "a.mp4", "NEW")
    assert state.decisions == {} and state.stale == 1
    assessment = assess_content(rules, title="Song X (Official MV)")
    d = decide(assessment, [], state, rules)
    assert d.validation_status == "pending_review" and any("sha256 khác" in n for n in d.notes)


def test_latest_review_wins_per_field():
    from src.dataset.validation.review_store import state_for
    base = {"video_id": "MV_001", "filename": "a.mp4", "sha256": "H", "evidence": "x"}
    reviews = [{**base, "reviewed_at": "2026-01-01", "reviewer": "A", "ai_generated_status": "confirmed",
                "license_status": "unknown"},
               {**base, "reviewed_at": "2026-01-02", "reviewer": "B", "license_status": "confirmed"}]
    state = state_for(reviews, "MV_001", "a.mp4", "H")
    assert state.decisions == {"ai_generated_status": "confirmed", "license_status": "confirmed"}
    assert state.reviewers == ["A", "B"]


def test_cover_rejected_and_reviewer_can_override_real_song_title(rules, mv_cfg):
    d = run(rules, mv_cfg, title="Song X – Acoustic Cover")
    assert d.validation_status == "rejected" and d.reasons == ["cover_version"]
    # Bài hát thật có tên "Cover": người duyệt xác nhận MV chính thức kèm căn cứ.
    d = run(rules, mv_cfg, title="Artist Z – Cover (Official MV)", review=FULL_REVIEW)
    assert d.validation_status == "accepted"
