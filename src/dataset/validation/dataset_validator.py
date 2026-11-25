"""Kiểm tra Dataset tập trung: kỹ thuật + nội dung + duyệt tay -> accepted / rejected / pending_review.

Quy tắc quyết định (``decide``, hàm thuần):

* ``rejected``  – có ít nhất một lý do: lỗi kỹ thuật (file, đọc được, MP4, thời lượng 150–420 s,
  ≥ 720p, trùng lặp), luật strong (AI, hậu trường, lyrics, teaser, …) hoặc người duyệt kết luận vi phạm.
  Người duyệt có thể ghi đè kết luận của LUẬT NỘI DUNG (có căn cứ), nhưng KHÔNG ghi đè lỗi kỹ thuật.
* ``accepted``  – không có lý do loại VÀ người duyệt đã xác nhận: content_type =
  official_music_video, ai_generated / behind_the_scenes / lyrics / license / source = confirmed,
  và metadata khớp file hiện tại.
* ``pending_review`` – còn lại (kèm danh sách mục chưa xác minh trong verification_notes).

Chỉ ``accepted`` được đưa vào pipeline (xem ``accepted_registry``).
"""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

from config.settings import Settings, get_settings
from src.common.io_utils import atomic_json, read_csv, write_csv
from src.dataset.schema import (TECHNICAL_REASONS, VALIDATION_LIST_COLUMNS, VALIDATION_STATUSES,
                                VERIFICATION_FIELDS, VIDEO_COLUMNS, normalize_video_row)
from src.dataset.validation import metadata_validator as mv
from src.dataset.validation.content_safety import SafetySummary, moderate_video, summarize
from src.dataset.validation.exclusion_rules import RuleSet
from src.dataset.validation.review_store import ReviewState, load_reviews, state_for
from src.dataset.validation.video_filter import ContentAssessment, assess_content, load_source_info

logger = logging.getLogger(__name__)

# content_type do người duyệt / luật gán -> mã lý do khi không phải MV chính thức.
_CONTENT_REASON = {"behind_the_scenes": "behind_the_scenes", "lyrics_video": "lyrics_video",
                   "teaser": "teaser", "reaction": "reaction_or_review"}
BLOCKING_CODES = ("metadata_stale", "probe_missing")
_FIELD_REASON = {"license_status": "license_rejected", "source_status": "source_rejected"}
_FIELD_LABEL = {"content_type": "loại nội dung (official_music_video?)",
                "ai_generated_status": "không phải AI-generated", "behind_the_scenes_status": "không phải hậu trường",
                "lyrics_status": "không phải lyric video", "license_status": "giấy phép",
                "source_status": "nguồn gốc / kênh chính chủ",
                "content_safety": "an toàn nội dung (người duyệt xem toàn video)",
                "content_safety_conflict": "kết quả kiểm duyệt mâu thuẫn"}


@dataclass
class Decision:
    validation_status: str
    content_type: str
    statuses: dict[str, str]
    reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    reviewed_by: str = ""
    reviewed_at: str = ""
    safety_fields: dict[str, str] = field(default_factory=dict)


def decide(assessment: ContentAssessment, tech_issues: list[mv.TechIssue], review: ReviewState,
           rules: RuleSet, safety: SafetySummary | None = None) -> Decision:
    if safety is None:   # không có kết quả kiểm duyệt nào: mọi bước là unknown
        from config.settings import load_criteria

        safety = summarize([], load_criteria()["content_safety"], review.decisions)
    reasons: list[str] = []
    notes: list[str] = []

    def add(reason: str):
        if reason not in reasons:
            reasons.append(reason)

    # 1. Kỹ thuật – không ghi đè được.
    for issue in tech_issues:
        if issue.reason:
            add(issue.reason)
            notes.append(f"{issue.code}: {issue.message}")
        else:
            notes.append(f"{issue.code}: {issue.message}")
    # Không phải lý do loại, nhưng chặn accepted cho tới khi chạy lại scan / validate sâu.
    blocking = [i.code for i in tech_issues if i.code in BLOCKING_CODES]

    # 2. Nội dung: luật tự động, sau đó quyết định của người duyệt (mới nhất) ghi đè.
    statuses = {f: "unknown" for f in VERIFICATION_FIELDS}
    statuses.update({k: v for k, v in assessment.statuses.items() if k in statuses})
    content_type = assessment.content_type
    decided = review.decisions
    for f in VERIFICATION_FIELDS:
        if f in decided:
            if statuses[f] == "rejected" and decided[f] == "confirmed":
                notes.append(f"{f}: người duyệt ghi đè kết luận của luật tự động")
            statuses[f] = decided[f]
    if "content_type" in decided:
        content_type = decided["content_type"]
    reviewer_says_official = decided.get("content_type") == "official_music_video"

    for sig in assessment.strong:
        rule = rules.rule(sig.category)
        if rule.status_field:
            if statuses.get(rule.status_field) == "rejected":
                add(sig.reason)
        elif not reviewer_says_official:
            add(sig.reason)
        notes.append(f"luật strong: {sig.describe()}")
    for f, value in statuses.items():
        if value == "rejected":
            rule_reason = next((r.reason for r in rules.rules if r.status_field == f), None)
            add(rule_reason or _FIELD_REASON[f])
    if content_type in _CONTENT_REASON:
        add(_CONTENT_REASON[content_type])
    elif content_type == "other" and "content_type" in decided:
        add("not_official_music_video")
    for sig in assessment.weak:
        notes.append(f"cần chú ý: {sig.describe()}")
    if review.stale:
        notes.append(f"{review.stale} quyết định duyệt cũ bị bỏ qua vì file đã thay đổi (sha256 khác)")

    # An toàn nội dung: tự loại (strong / tuổi 18+) hoặc người duyệt kết luận vi phạm.
    for reason in safety.reject_reasons:
        add(reason)
    notes += [f"an toàn nội dung: {n}" for n in safety.notes]
    if safety.fields.get("review_notes") and not safety.reviewer_safe:
        notes.append("kiểm duyệt: " + safety.fields["review_notes"])

    # 3. Kết luận.
    if reasons:
        status = "rejected"
    else:
        missing = [] if content_type == "official_music_video" else ["content_type"]
        missing += [f for f in VERIFICATION_FIELDS if statuses[f] != "confirmed"]
        if safety.conflict:
            missing.append("content_safety_conflict")
        elif not safety.reviewer_safe:
            missing.append("content_safety")
        missing += blocking
        if missing:
            status = "pending_review"
            notes.insert(0, "chưa xác minh: " + ", ".join(_FIELD_LABEL.get(m, m) for m in missing))
        else:
            status = "accepted"
    return Decision(status, content_type, statuses, reasons, notes,
                    ", ".join(review.reviewers), review.reviewed_at, safety.fields)


def _apply(row: dict, decision: Decision) -> None:
    row.update(decision.safety_fields)
    row.update(decision.statuses)
    row["content_type"] = decision.content_type
    row["validation_status"] = decision.validation_status
    row["rejection_reason"] = ";".join(decision.reasons)
    row["verification_notes"] = " | ".join(decision.notes)
    row["reviewed_by"] = decision.reviewed_by
    row["reviewed_at"] = decision.reviewed_at


def write_validation_lists(settings: Settings, rows: list[dict]) -> dict[str, int]:
    """dataset/validation/{accepted,rejected,pending_review}.csv – sinh từ metadata, không chứa video."""
    counts = {}
    for status in VALIDATION_STATUSES:
        items = [r for r in rows if r.get("validation_status") == status]
        write_csv(settings.paths.validation / f"{status}.csv", VALIDATION_LIST_COLUMNS, items)
        counts[status] = len(items)
    return counts


def validate(settings: Settings | None = None, deep: bool = True, write: bool = True) -> dict:
    """Kiểm tra toàn bộ Dataset, ghi trạng thái vào videos.csv + danh sách + validation_report.json."""
    from src.dataset.create_dataset import write_dataset_info
    from src.dataset.migrate_metadata import ensure_schema
    from src.dataset.scan_videos import write_splits

    settings = settings or get_settings()
    paths = settings.paths
    if paths.videos_csv.exists() and write:
        ensure_schema(settings)
    rows = [normalize_video_row(r) for r in read_csv(paths.videos_csv)]
    sources = {r["filename"]: r for r in read_csv(paths.sources_csv) if r.get("filename")}
    reviews = load_reviews(paths.reviews_csv)
    rules = RuleSet.from_settings(settings)

    dataset_issues = mv.check_dataset(settings, rows) if paths.videos_csv.exists() else [
        mv.TechIssue("missing_metadata_file", f"Không có {paths.videos_csv}; chạy create_dataset/scan_videos")]
    duplicates = mv.check_duplicates(rows, sources, root=paths.root)
    per_video: dict[str, list[mv.TechIssue]] = {}
    for issue in duplicates:
        per_video.setdefault(issue.video_id, []).append(issue)

    decisions = []
    for row in rows:
        src = sources.get(row.get("filename"), {})
        for key in ("title", "source", "source_url", "license"):
            if src.get(key):
                row[key] = src[key]
        info = load_source_info(paths.source_info / f"{row.get('filename')}.json")
        assessment = assess_content(
            rules, title=row.get("title", ""), filename=row.get("filename", ""),
            source_title=info.get("title", ""), description=info.get("description", ""),
            tags=info.get("tags") or [], uploader=info.get("channel") or src.get("author", ""))
        tech = mv.check_row(row, settings, deep=deep) + per_video.get(row.get("video_id"), [])
        review = state_for(reviews, row.get("video_id", ""), row.get("filename", ""), row.get("sha256", ""))
        checks = moderate_video(settings, row, info, src) if write else []
        if not write:
            from src.dataset.validation.content_safety import text_moderator_for
            from src.dataset.validation.content_safety.text_moderator import check_age_restriction
            checks = [text_moderator_for(settings).check("metadata", {"title": row.get("title"),
                                                                       "description": info.get("description")}),
                      check_age_restriction(info, settings.criteria["content_safety"])]
        safety = summarize(checks, settings.criteria["content_safety"], review.decisions)
        decision = decide(assessment, tech, review, rules, safety)
        _apply(row, decision)
        decisions.append({"video_id": row.get("video_id"), "filename": row.get("filename"),
                          "validation_status": decision.validation_status,
                          "rejection_reason": decision.reasons, "notes": decision.notes})

    counts = Counter(r["validation_status"] for r in rows)
    report = {
        "validated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "deep" if deep else "quick",
        "total_videos": len(rows),
        "accepted": counts.get("accepted", 0),
        "rejected": counts.get("rejected", 0),
        "pending_review": counts.get("pending_review", 0),
        "dataset_issues": [vars(i) for i in dataset_issues],
        "duplicates": [vars(i) for i in duplicates],
        "videos": decisions,
        "ok": not any(i.code in ("missing_metadata_columns", "duplicate_id", "unregistered_file",
                                  "missing_metadata_file") for i in dataset_issues),
    }
    if write and paths.metadata.exists():
        write_csv(paths.videos_csv, VIDEO_COLUMNS, rows)
        report["lists"] = write_validation_lists(settings, rows)
        write_splits(settings, rows)
        write_dataset_info(paths, settings, rows)
        atomic_json(paths.validation_report, report)
    return report


def is_technical(reason: str) -> bool:
    return reason in TECHNICAL_REASONS
