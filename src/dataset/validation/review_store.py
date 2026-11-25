"""Quyết định duyệt tay – nguồn DUY NHẤT của giá trị ``confirmed``.

``metadata/reviews.csv`` chỉ được **ghi thêm** (không sửa/xóa dòng cũ) để giữ lịch sử ai đã
xác minh gì, căn cứ nào. Mỗi dòng gắn với sha256 của file: nếu file bị thay, quyết định cũ hết
hiệu lực và video quay về ``pending_review``.

Với từng tiêu chí, quyết định mới nhất (ô không trống) thắng. Người duyệt và căn cứ là bắt buộc.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from src.common.io_utils import read_csv, write_csv
from src.dataset.schema import (CONTENT_TYPES, REVIEW_COLUMNS, SAFETY_CATEGORIES, SAFETY_VALUES,
                                VERIFICATION_FIELDS, VERIFICATION_VALUES)

SAFETY_DECISION_FIELDS = ("content_safety_status", "content_safety_categories")
DECISION_FIELDS = ("content_type",) + VERIFICATION_FIELDS + SAFETY_DECISION_FIELDS


class ReviewError(ValueError):
    pass


@dataclass
class ReviewState:
    decisions: dict[str, str] = field(default_factory=dict)  # field -> giá trị mới nhất
    reviewers: list[str] = field(default_factory=list)
    reviewed_at: str = ""
    evidence: list[str] = field(default_factory=list)
    stale: int = 0                                            # số dòng bị bỏ qua vì sha256 khác


def validate_decision(decision: dict) -> dict:
    """Chuẩn hóa và kiểm tra một quyết định trước khi ghi."""
    clean = {k: str(decision.get(k) or "").strip() for k in REVIEW_COLUMNS}
    if not clean["reviewer"]:
        raise ReviewError("Thiếu người duyệt (reviewer) – không ghi quyết định ẩn danh")
    if not clean["video_id"] and not clean["filename"]:
        raise ReviewError("Cần video_id hoặc filename")
    if not clean["sha256"]:
        raise ReviewError("Thiếu sha256 – chạy scan_videos trước khi duyệt")
    values = {f: clean[f] for f in DECISION_FIELDS if clean[f]}
    if not values:
        raise ReviewError("Quyết định không có tiêu chí nào")
    if clean["content_type"] and clean["content_type"] not in CONTENT_TYPES:
        raise ReviewError(f"content_type phải thuộc {CONTENT_TYPES}")
    for f in VERIFICATION_FIELDS:
        if clean[f] and clean[f] not in VERIFICATION_VALUES:
            raise ReviewError(f"{f} phải thuộc {VERIFICATION_VALUES}")
    safety = clean["content_safety_status"]
    categories = [c for c in clean["content_safety_categories"].replace(",", ";").split(";") if c.strip()]
    categories = [c.strip() for c in categories]
    if safety and safety not in SAFETY_VALUES:
        raise ReviewError(f"content_safety_status phải thuộc {SAFETY_VALUES}")
    if bad := [c for c in categories if c not in SAFETY_CATEGORIES]:
        raise ReviewError(f"content_safety_categories không hợp lệ: {bad} (hợp lệ: {SAFETY_CATEGORIES})")
    if safety == "flagged" and not categories:
        raise ReviewError("content_safety_status=flagged cần content_safety_categories (nhóm vi phạm)")
    if categories and safety != "flagged":
        raise ReviewError("content_safety_categories chỉ dùng khi content_safety_status=flagged")
    clean["content_safety_categories"] = ";".join(categories)
    if not clean["evidence"]:
        raise ReviewError("Thiếu căn cứ (evidence): ghi rõ đã kiểm tra gì (kênh, mô tả, xem video…)")
    clean["reviewed_at"] = clean["reviewed_at"] or datetime.now(timezone.utc).isoformat(timespec="seconds")
    return clean


def append_reviews(path, decisions: list[dict]) -> list[dict]:
    """Ghi thêm các quyết định (đã kiểm tra hết trước khi ghi – một dòng lỗi thì không ghi gì)."""
    clean = [validate_decision(d) for d in decisions]
    if clean:
        write_csv(path, REVIEW_COLUMNS, read_csv(path) + clean)
    return clean


def load_reviews(path) -> list[dict]:
    return read_csv(path)


def state_for(reviews: list[dict], video_id: str, filename: str, sha256: str) -> ReviewState:
    state = ReviewState()
    for r in sorted(reviews, key=lambda r: r.get("reviewed_at", "")):
        if not (r.get("video_id") == video_id or (not r.get("video_id") and r.get("filename") == filename)):
            continue
        if r.get("sha256") != sha256:
            state.stale += 1
            continue
        for f in DECISION_FIELDS:
            if (value := (r.get(f) or "").strip()):
                state.decisions[f] = value
        if r.get("reviewer") and r["reviewer"] not in state.reviewers:
            state.reviewers.append(r["reviewer"])
        state.reviewed_at = r.get("reviewed_at", state.reviewed_at)
        if r.get("evidence"):
            state.evidence.append(r["evidence"])
    return state
