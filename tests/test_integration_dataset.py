"""Kiểm thử tích hợp §8.2: import nhiều loại video -> scan -> validate -> duyệt -> cổng accepted_registry.

Ngưỡng thời lượng THẬT (150–420 s). Video tổng hợp ghi 1 fps, 64x48 (độ phân giải được nới trong
fixture để giữ test nhẹ) – chỉ để kiểm thử logic, không dùng làm dữ liệu đồ án.
Phần Spark / Kafka / Streamlit chưa được xây (GĐ5–7): ở đây kiểm tra HỢP ĐỒNG mà các module đó
bắt buộc dùng (accepted_videos / assert_accepted / filter_records).
"""
from __future__ import annotations

import pytest

from src.common.io_utils import read_csv
from src.dataset.dataset_report import build_report
from src.dataset.review_video import export_sheet, import_sheet, review_one
from src.dataset.scan_videos import register_source, scan, write_source_info
from src.dataset.validation.accepted_registry import (NotAcceptedError, accepted_ids, accepted_videos,
                                                      assert_accepted, check_time_range, filter_records)
from src.dataset.validation.dataset_validator import validate
from src.dataset.validation.review_store import ReviewError
from tests.conftest import FULL_REVIEW, by_filename, make_video

REVIEWER = "Người duyệt thử nghiệm"
EVIDENCE = "kiểm thử: metadata mẫu"


def add_mv(settings, name, seconds, title, description="", uploader="Artist X", seed=0):
    make_video(settings.paths.raw_videos / name, seconds=seconds, fps=1, seed=seed)
    register_source(settings, name, source="youtube", license="CC BY", title=title, author=uploader,
                    source_url=f"https://www.youtube.com/watch?v={name[:11].ljust(11, 'x')}")
    write_source_info(settings, name, {"title": title, "description": description, "channel": uploader})


def review_all(settings, filename, **overrides):
    vid = by_filename(settings)[filename]["video_id"]
    review_one(settings, vid, REVIEWER, EVIDENCE, **{**FULL_REVIEW, **overrides})
    validate(settings, deep=False)
    return vid


@pytest.fixture
def mixed(mv_settings):
    s = mv_settings
    add_mv(s, "valid.mp4", 240, "Song A (Official MV)", seed=1)
    add_mv(s, "bts.mp4", 200, "Song A – Behind The Scenes", seed=2)
    add_mv(s, "lyrics.mp4", 210, "Song B (Official Lyric Video)", seed=3)
    add_mv(s, "ai.mp4", 230, "Song C (AI Generated Music Video)", seed=4)
    add_mv(s, "unverified.mp4", 250, "Song D (Official Music Video)", seed=5)
    add_mv(s, "short.mp4", 120, "Song E (Official MV)", seed=6)
    add_mv(s, "long.mp4", 450, "Song F (Official MV)", seed=7)
    scan(s)
    validate(s)
    return s


def test_import_statuses(mixed):
    rows = by_filename(mixed)
    status = {name: r["validation_status"] for name, r in rows.items()}
    assert status == {"valid.mp4": "pending_review", "bts.mp4": "rejected", "lyrics.mp4": "rejected",
                      "ai.mp4": "rejected", "unverified.mp4": "pending_review", "short.mp4": "rejected",
                      "long.mp4": "rejected"}
    assert rows["bts.mp4"]["content_type"] == "behind_the_scenes"
    assert rows["bts.mp4"]["behind_the_scenes_status"] == "rejected"
    assert rows["lyrics.mp4"]["content_type"] == "lyrics_video" and rows["lyrics.mp4"]["lyrics_status"] == "rejected"
    assert rows["ai.mp4"]["ai_generated_status"] == "rejected"
    assert rows["short.mp4"]["rejection_reason"] == "duration_out_of_range"
    assert rows["long.mp4"]["rejection_reason"] == "duration_out_of_range"
    assert accepted_videos(mixed) == []                     # chưa duyệt -> không video nào vào pipeline


def test_only_reviewed_valid_video_is_accepted(mixed):
    vid = review_all(mixed, "valid.mp4")
    assert by_filename(mixed)["valid.mp4"]["validation_status"] == "accepted"
    assert by_filename(mixed)["valid.mp4"]["reviewed_by"] == REVIEWER
    assert [v.video_id for v in accepted_videos(mixed)] == [vid]
    # Người duyệt không thể "cứu" video sai thời lượng.
    review_all(mixed, "short.mp4")
    assert by_filename(mixed)["short.mp4"]["validation_status"] == "rejected"
    assert accepted_ids(mixed) == {vid}


def test_validation_lists_and_splits_follow_status(mixed):
    vid = review_all(mixed, "valid.mp4")
    lists = {s: [r["filename"] for r in read_csv(mixed.paths.validation / f"{s}.csv")]
             for s in ("accepted", "rejected", "pending_review")}
    assert lists["accepted"] == ["valid.mp4"]
    assert sorted(lists["pending_review"]) == ["unverified.mp4"]
    assert len(lists["rejected"]) == 5
    rejected = {r["filename"]: r["rejection_reason"] for r in read_csv(mixed.paths.validation / "rejected.csv")}
    assert rejected["ai.mp4"] == "ai_generated" and rejected["bts.mp4"] == "behind_the_scenes"
    split_ids = [r["video_id"] for s in ("train", "validation", "test") for r in read_csv(mixed.paths.splits / f"{s}.csv")]
    assert split_ids == [vid]


def test_report_counts_only_accepted(mixed):
    review_all(mixed, "valid.mp4")
    stats, md = build_report(mixed)
    assert (stats["total_checked"], stats["accepted"], stats["rejected"], stats["pending_review"]) == (7, 1, 5, 1)
    expected = {"ai_generated": 1, "behind_the_scenes": 1, "lyrics_video": 1, "duration": 2, "duplicate": 0,
                "profanity_or_abusive_language": 0, "violent_or_gory_content": 0, "violence_incitement": 0,
                "explicit_or_obscene_content": 0, "age_restricted": 0}
    assert stats["rejected_by_group"] == expected
    assert stats["review_required"] == 6 and "Kiểm duyệt an toàn nội dung" in md
    assert stats["accepted_duration_seconds"] == pytest.approx(240, abs=1)
    assert stats["accepted_in_target_range"] == 1
    assert mixed.paths.report_json.exists() and mixed.paths.report_csv.exists()
    assert len(read_csv(mixed.paths.report_csv)) == 7 and "Bị loại do lyric video | 1" in md


# ---------------------------------------------------------------- cổng kiểm soát (hợp đồng GĐ3–7)
def test_gate_blocks_non_accepted_videos(mixed):
    vid = review_all(mixed, "valid.mp4")
    assert assert_accepted(vid, mixed).filename == "valid.mp4"
    for name in ("bts.mp4", "unverified.mp4", "long.mp4"):
        with pytest.raises(NotAcceptedError):
            assert_accepted(by_filename(mixed)[name]["video_id"], mixed)


def test_downstream_results_of_rejected_videos_are_filtered_not_deleted(mixed):
    vid = review_all(mixed, "valid.mp4")
    other = by_filename(mixed)["bts.mp4"]["video_id"]
    # Giả lập kết quả detection đã lưu (như Spark / Streamlit sẽ đọc ở GĐ5/7).
    detections = [{"video_id": vid, "object_name": "person", "timestamp": 12.0},
                  {"video_id": other, "object_name": "person", "timestamp": 3.0}]
    assert [d["video_id"] for d in filter_records(detections, accepted_ids(mixed))] == [vid]
    # Video đang accepted bị người duyệt loại sau đó -> kết quả cũ tự biến mất khỏi truy vấn.
    review_one(mixed, vid, REVIEWER, "xem lại: hình ảnh do AI tạo", ai_generated_status="rejected")
    validate(mixed, deep=False)
    assert list(filter_records(detections, accepted_ids(mixed))) == []
    assert len(detections) == 2                                         # dữ liệu không bị xóa


def test_gate_rechecks_file_changes(mixed):
    vid = review_all(mixed, "valid.mp4")
    path = mixed.paths.raw_videos / "valid.mp4"
    path.write_bytes(path.read_bytes() + b"\0")                          # file bị thay đổi sau khi duyệt
    assert vid not in accepted_ids(mixed)
    with pytest.raises(NotAcceptedError):
        assert_accepted(vid, mixed)


def test_changed_file_invalidates_review(mixed):
    review_all(mixed, "valid.mp4")
    make_video(mixed.paths.raw_videos / "valid.mp4", seconds=240, fps=1, seed=99)   # thay nội dung
    scan(mixed)
    validate(mixed)
    row = by_filename(mixed)["valid.mp4"]
    assert row["validation_status"] == "pending_review" and "sha256 khác" in row["verification_notes"]


def test_time_range_check(mixed):
    vid = review_all(mixed, "valid.mp4")
    video = assert_accepted(vid, mixed)
    check_time_range(video, 12.0, 38.0)
    for start, end in ((-1, 5), (30, 10), (200, 300)):
        with pytest.raises(ValueError):
            check_time_range(video, start, end)


# ---------------------------------------------------------------- duyệt tay
def test_review_requires_reviewer_and_evidence(mixed):
    vid = by_filename(mixed)["valid.mp4"]["video_id"]
    with pytest.raises(ReviewError):
        review_one(mixed, vid, "", EVIDENCE, **FULL_REVIEW)
    with pytest.raises(ReviewError):
        review_one(mixed, vid, REVIEWER, "", **FULL_REVIEW)
    with pytest.raises(ReviewError):
        review_one(mixed, vid, REVIEWER, EVIDENCE, ai_generated_status="maybe")
    assert read_csv(mixed.paths.reviews_csv) == []                       # không ghi gì khi lỗi


def test_review_sheet_round_trip(mixed):
    sheet = export_sheet(mixed)
    rows = read_csv(sheet)
    assert sorted(r["filename"] for r in rows) == ["unverified.mp4", "valid.mp4"]   # chỉ video pending_review
    assert all(not r["content_type"] for r in rows)                                 # cột quyết định để trống
    next(r for r in rows if r["filename"] == "valid.mp4").update(FULL_REVIEW, evidence="Kênh chính chủ, MV quay thực tế")
    from src.common.io_utils import write_csv
    from src.dataset.review_video import SHEET_COLUMNS
    write_csv(sheet, SHEET_COLUMNS, rows)
    written = import_sheet(mixed, sheet, REVIEWER)
    assert len(written) == 1 and written[0]["reviewer"] == REVIEWER
    validate(mixed, deep=False)
    assert by_filename(mixed)["valid.mp4"]["validation_status"] == "accepted"
    assert by_filename(mixed)["unverified.mp4"]["validation_status"] == "pending_review"


def test_review_sheet_rejects_outdated_hash(mixed):
    sheet = export_sheet(mixed)
    rows = read_csv(sheet)
    rows[0].update(FULL_REVIEW, evidence="x", sha256="khac")
    from src.common.io_utils import write_csv
    from src.dataset.review_video import SHEET_COLUMNS
    write_csv(sheet, SHEET_COLUMNS, rows)
    with pytest.raises(ReviewError):
        import_sheet(mixed, sheet, REVIEWER)
    assert read_csv(mixed.paths.reviews_csv) == []


def test_ai_mention_in_description_stays_pending(mv_settings):
    add_mv(mv_settings, "desc.mp4", 240, "Song G (Official MV)",
           description="Some background shots were made with AI. Lyrics: em ơi ...")
    scan(mv_settings)
    validate(mv_settings)
    row = by_filename(mv_settings)["desc.mp4"]
    assert row["validation_status"] == "pending_review" and row["rejection_reason"] == ""
    assert "ai_generated:weak:description" in row["verification_notes"]
