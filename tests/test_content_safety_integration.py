"""Tích hợp an toàn nội dung: scan -> validate (lexicon tạm) -> duyệt -> cổng; tải; migration v2 -> v3."""
from __future__ import annotations

import json

import pytest

from src.common.io_utils import read_csv, write_csv
from src.dataset import download_videos as dl
from src.dataset.migrate_metadata import migrate
from src.dataset.review_video import review_one
from src.dataset.scan_videos import register_source, scan, write_source_info
from src.dataset.schema import VIDEO_COLUMNS
from src.dataset.validation.accepted_registry import accepted_ids
from src.dataset.validation.content_safety import save_result
from src.dataset.validation.content_safety.lexicon import Lexicon
from src.dataset.validation.content_safety.providers import CheckResult, Flag
from src.dataset.validation.content_safety.text_moderator import TextModerator
from src.dataset.validation.dataset_validator import validate
from src.dataset.validation.review_store import ReviewError
from tests.conftest import FULL_REVIEW, by_filename, make_settings, make_video
from tests.test_content_safety import EN_TITLE, SAFETY, write_lexicon

REVIEWER, EVIDENCE = "Người duyệt thử nghiệm", "kiểm thử: đã xem toàn video (dữ liệu mẫu)"


@pytest.fixture
def s(tmp_path):
    lex = write_lexicon(tmp_path / "lex", {"en": [("zorbix", "profanity_or_abusive_language", "strong"),
                                                  ("glimpf", "violent_or_gory_content", "ambiguous")]})
    return make_settings(tmp_path, lexicon_dir=lex)


def add(s, name, title, description="", age_limit=0, seed=0):
    make_video(s.paths.raw_videos / name, seconds=3, seed=seed)
    register_source(s, name, source="youtube", license="CC BY", title=title, author="Artist X",
                    source_url=f"https://www.youtube.com/watch?v={name[:11].ljust(11, 'q')}")
    write_source_info(s, name, {"title": title, "description": description, "channel": "Artist X",
                                "age_limit": age_limit})


@pytest.fixture
def mixed(s):
    add(s, "clean.mp4", f"{EN_TITLE} (Official MV)", "Official music video. Thanks for watching our new song.", seed=1)
    add(s, "profane.mp4", f"{EN_TITLE} zorbix (Official MV)", seed=2)
    add(s, "desc.mp4", f"{EN_TITLE} (Official MV)", "Lyrics: zorbix zorbix, all night long we sing together", seed=3)
    add(s, "adult.mp4", f"{EN_TITLE} (Official MV)", "Official music video for the new single release.",
        age_limit=18, seed=4)
    add(s, "ambig.mp4", f"{EN_TITLE} glimpf (Official MV)", seed=5)
    scan(s)
    validate(s)
    return s


def test_validator_applies_content_safety(mixed):
    rows = by_filename(mixed)
    assert rows["profane.mp4"]["validation_status"] == "rejected"
    assert rows["profane.mp4"]["rejection_reason"] == "profanity_or_abusive_language"
    assert rows["profane.mp4"]["profanity_detected"] == "true"
    assert rows["adult.mp4"]["rejection_reason"] == "age_restricted"
    for name in ("clean.mp4", "desc.mp4", "ambig.mp4"):
        assert rows[name]["validation_status"] == "pending_review", name     # chỉ gắn cờ / chưa duyệt
    assert rows["desc.mp4"]["content_safety_status"] == "flagged"
    assert "z*****" in rows["desc.mp4"]["review_notes"]                    # từ nhạy cảm được che
    for name, row in rows.items():                                       # ghi chú do hệ thống sinh ra đều che từ
        for f in ("review_notes", "verification_notes", "content_safety_reason", "moderation_method"):
            assert "zorbix" not in row[f], (name, f)
    assert rows["clean.mp4"]["text_moderation_status"] == "unknown"        # phụ đề / OCR chưa triển khai
    assert rows["clean.mp4"]["audio_moderation_status"] == "unknown"
    assert rows["clean.mp4"]["visual_moderation_status"] == "unknown"
    assert "not_implemented" in rows["clean.mp4"]["moderation_method"]
    for f in ("profanity_detected", "violent_content_detected", "explicit_content_detected"):
        assert rows["clean.mp4"][f] == "unknown"                           # không mặc định false


def test_moderation_results_written_outside_processed(mixed):
    vid = by_filename(mixed)["clean.mp4"]["video_id"]
    saved = json.loads((mixed.paths.moderation / vid / "metadata.json").read_text(encoding="utf-8"))
    assert saved["result"]["scope"] == "metadata" and len(saved["sha256"]) == 64
    assert not any(mixed.paths.processed.rglob("*.json"))


def test_only_reviewer_safe_leads_to_accepted(mixed):
    rows = by_filename(mixed)
    vid = rows["clean.mp4"]["video_id"]
    no_safety = {k: v for k, v in FULL_REVIEW.items() if k != "content_safety_status"}
    review_one(mixed, vid, REVIEWER, EVIDENCE, **no_safety)
    validate(mixed, deep=False)
    assert by_filename(mixed)["clean.mp4"]["validation_status"] == "pending_review"
    review_one(mixed, vid, REVIEWER, EVIDENCE, content_safety_status="safe")
    validate(mixed, deep=False)
    row = by_filename(mixed)["clean.mp4"]
    assert row["validation_status"] == "accepted" and row["review_required"] == "false"
    assert accepted_ids(mixed) == {vid}


def test_flagged_description_can_be_cleared_by_reviewer(mixed):
    vid = by_filename(mixed)["desc.mp4"]["video_id"]
    review_one(mixed, vid, REVIEWER, EVIDENCE, **FULL_REVIEW)
    validate(mixed, deep=False)
    assert by_filename(mixed)["desc.mp4"]["validation_status"] == "accepted"


def test_reviewer_cannot_clear_auto_reject_conflict(mixed):
    vid = by_filename(mixed)["profane.mp4"]["video_id"]
    review_one(mixed, vid, REVIEWER, EVIDENCE, **FULL_REVIEW)
    validate(mixed, deep=False)
    row = by_filename(mixed)["profane.mp4"]
    assert row["validation_status"] == "pending_review"
    assert "content_safety_conflict" in row["content_safety_reason"]


def test_reviewer_flag_requires_categories(mixed):
    vid = by_filename(mixed)["clean.mp4"]["video_id"]
    with pytest.raises(ReviewError):
        review_one(mixed, vid, REVIEWER, EVIDENCE, content_safety_status="flagged")
    with pytest.raises(ReviewError):
        review_one(mixed, vid, REVIEWER, EVIDENCE, content_safety_status="flagged",
                   content_safety_categories="not_a_category")
    review_one(mixed, vid, REVIEWER, EVIDENCE, content_safety_status="flagged",
               content_safety_categories="violent_or_gory_content")
    validate(mixed, deep=False)
    row = by_filename(mixed)["clean.mp4"]
    assert row["validation_status"] == "rejected" and row["violent_content_detected"] == "true"


def test_stored_stage_result_used_and_stale_ignored(mixed):
    row = by_filename(mixed)["clean.mp4"]
    vid, sha = row["video_id"], row["sha256"]
    visual = CheckResult("visual", "flagged", "nudenet@test", flags=[
        Flag("explicit_or_obscene_content", "explicit_or_obscene_content", "model", False, field="frame",
             timestamp=12.5, score=0.8)])
    save_result(mixed, vid, sha, visual)
    validate(mixed, deep=False)
    row = by_filename(mixed)["clean.mp4"]
    assert row["visual_moderation_status"] == "flagged" and "@12.5s" in row["review_notes"]
    save_result(mixed, vid, "OTHER_SHA", visual)                         # kết quả của file cũ
    validate(mixed, deep=False)
    row = by_filename(mixed)["clean.mp4"]
    assert row["visual_moderation_status"] == "unknown" and "file đã thay đổi" in row["review_notes"]


# ---------------------------------------------------------------- tải
def info(**kw):
    base = {"id": "abc", "title": f"{EN_TITLE} (Official Music Video)", "channel": "Artist A", "channel_id": "UC1",
            "webpage_url": "https://youtu.be/abc", "duration": 225,
            "license": "Creative Commons Attribution license (reuse allowed)", "age_limit": 0}
    base.update(kw)
    return base


@pytest.fixture
def rules(s):
    return dl.Rules(min_duration=150, max_duration=420, moderator=TextModerator(Lexicon.load(s.lexicon_dir), SAFETY))


def test_download_screens_content_safety(rules):
    assert dl.evaluate(info(), rules).screen_result == "pass"
    cand = dl.evaluate(info(title=f"{EN_TITLE} zorbix (Official Music Video)"), rules)
    assert not cand.accepted and "an toàn nội dung" in cand.reason
    assert not dl.evaluate(info(age_limit=18), rules).accepted
    cand = dl.evaluate(info(description="Lyrics: zorbix all night long we sing together"), rules)
    assert cand.accepted and cand.screen_result == "review"


def test_age_gated_video_is_rejected_not_failed(s, monkeypatch, rules):
    def gate(url, r):
        raise RuntimeError("ERROR: [youtube] x: Sign in to confirm your age. This video may be inappropriate")
    monkeypatch.setattr(dl, "inspect", gate)
    summary = dl.run(s, ["u"], dry_run=True, max_height=720, rules=rules)
    assert not summary["failed"] and summary["rejected"][0]["reason"].startswith("age_restricted")


# ---------------------------------------------------------------- migration v2 -> v3
def test_v2_metadata_migrates_to_v3_without_confirming_safety(s):
    v2_columns = [c for c in VIDEO_COLUMNS if c not in (
        "content_safety_status", "language_detection_status", "detected_languages", "text_moderation_status",
        "audio_moderation_status", "visual_moderation_status", "profanity_detected", "violent_content_detected",
        "violence_incitement_detected", "explicit_content_detected", "content_safety_score",
        "content_safety_reason", "moderation_confidence", "moderation_method", "review_required", "review_notes")]
    s.paths.metadata.mkdir(parents=True)
    write_csv(s.paths.videos_csv, v2_columns, [{"video_id": "MV_001", "filename": "a.mp4",
                                                "validation_status": "accepted", "sha256": "h"}])
    s.paths.dataset_info.write_text(json.dumps({"schema_version": 2}), encoding="utf-8")
    summary = migrate(s)
    assert summary["migrated"] and summary["from_version"] == 2
    row = read_csv(s.paths.videos_csv)[0]
    assert row["content_safety_status"] == "unknown" and row["profanity_detected"] == "unknown"
    assert row["review_required"] == "true"
    assert any(s.paths.backups.iterdir())
