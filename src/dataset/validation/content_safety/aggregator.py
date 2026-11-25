"""Gộp kết quả các bước kiểm duyệt + quyết định của người duyệt -> trường metadata và lý do loại.

Chính sách (đã chốt, xem docs/CONTENT_MODERATION.md):

* Tự động ``rejected`` chỉ khi có cờ ``auto_reject`` (từ strong, không mơ hồ, trên tiêu đề / tags /
  tên file; OCR từ M2; giới hạn tuổi 18+) hoặc người duyệt kết luận vi phạm.
* Cờ ``ambiguous`` / ``model`` (mô tả, phụ đề, transcript, mô hình hình ảnh chưa hiệu chỉnh) chỉ đưa
  video vào ``pending_review`` kèm timestamp cho người duyệt.
* ``content_safety_status = safe`` CHỈ khi người duyệt ghi ``safe`` (sau khi xem toàn video).
* Mâu thuẫn (hai công cụ cùng bước kết luận ngược nhau, hoặc người duyệt ghi ``safe`` trong khi có
  cờ tự động loại) -> không loại, không chấp nhận: ``pending_review`` với lý do
  ``content_safety_conflict``. Cách giải quyết: sửa lexicon nếu đó là nhận diện sai, hoặc người
  duyệt ghi ``flagged``.
* Trường ``*_detected``: ``true`` chỉ khi có cờ strong hoặc người duyệt xác nhận; ``false`` chỉ khi
  người duyệt ghi safe hoặc MỌI bước trong phạm vi của nhóm đó đều đã chạy và ``safe``; còn lại
  ``unknown`` (không bao giờ mặc định false).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from src.dataset.schema import SAFETY_CATEGORIES, SAFETY_FLAG_FIELDS
from src.dataset.validation.content_safety.providers import CheckResult

TEXT_SCOPES = ("metadata", "subtitles", "ocr")
AUDIO_SCOPES = ("audio_transcript",)
VISUAL_SCOPES = ("visual",)


@dataclass
class SafetySummary:
    fields: dict[str, str]
    reject_reasons: list[str] = field(default_factory=list)
    reviewer_safe: bool = False
    conflict: bool = False
    notes: list[str] = field(default_factory=list)


def _group_status(results: list[CheckResult]) -> str:
    if not results:
        return "unknown"
    statuses = [r.status for r in results]
    if "flagged" in statuses:
        return "flagged"
    if all(s in ("safe", "not_applicable") for s in statuses) and "safe" in statuses:
        return "safe"
    if all(s == "not_applicable" for s in statuses):
        return "not_applicable"
    return "unknown"


def _language_status(results: list[CheckResult]) -> str:
    statuses = [r.language_status for r in results if r.language_status != "not_applicable"]
    if not statuses:
        return "not_applicable" if results else "unknown"
    if "unsupported" in statuses:
        return "unsupported"
    if all(s == "detected" for s in statuses):
        return "detected"
    return "unknown"


def _conflicts(results: list[CheckResult]) -> list[str]:
    """Cùng một bước có công cụ báo flagged và công cụ khác báo safe cho cùng nhóm nội dung."""
    out = []
    by_scope: dict[str, list[CheckResult]] = {}
    for r in results:
        by_scope.setdefault(r.scope, []).append(r)
    for scope, group in by_scope.items():
        flagged = {f.category for r in group if r.status == "flagged" for f in r.flags}
        safe = {c for r in group if r.status == "safe" for c in r.covered_categories}
        if both := sorted(flagged & safe):
            out.append(f"{scope}: công cụ mâu thuẫn về {', '.join(both)}")
    return out


def summarize(results: list[CheckResult], safety_config: Mapping, review: Mapping[str, str] | None = None) -> SafetySummary:
    review = review or {}
    reviewer_status = review.get("content_safety_status", "")
    reviewer_cats = [c for c in (review.get("content_safety_categories") or "").split(";") if c]
    by_scope = {r.scope: [x for x in results if x.scope == r.scope] for r in results}
    flags = [f for r in results for f in r.flags]
    auto_flags = [f for f in flags if f.auto_reject]
    category_cfg = safety_config.get("categories", {})

    notes = _conflicts(results)
    conflict = bool(notes)
    if reviewer_status == "safe" and auto_flags:
        conflict = True
        notes.append("người duyệt ghi safe nhưng có cờ tự động loại: "
                     + "; ".join(f.describe() for f in auto_flags)
                     + " – sửa lexicon nếu nhận diện sai, hoặc ghi flagged")

    reasons: list[str] = []
    if reviewer_status == "flagged":
        for c in reviewer_cats:
            reasons.append(category_cfg.get(c, {}).get("reason", c))
    if not conflict:
        for f in auto_flags:
            if f.reason not in reasons:
                reasons.append(f.reason)

    if conflict:
        status = "unknown"
    elif reviewer_status in ("safe", "flagged"):
        status = reviewer_status
    elif flags:
        status = "flagged"
    else:
        status = "unknown"

    detected = {}
    for category in SAFETY_CATEGORIES:
        if category in reviewer_cats or any(f.category == category and f.level == "strong" for f in flags):
            detected[category] = "true"
        elif reviewer_status == "safe" and not conflict:
            detected[category] = "false"
        else:
            scope = category_cfg.get(category, {}).get("scope", [])
            covered = all(any(r.status == "safe" and category in r.covered_categories for r in by_scope.get(s, []))
                          for s in scope) if scope else False
            detected[category] = "false" if covered and not any(f.category == category for f in flags) else "unknown"

    scored = [f for f in flags if f.score is not None]
    top = max(scored, key=lambda f: f.score) if scored else None
    method = "; ".join(f"{r.scope}:{r.method}" + (f" [điểm: {r.score_semantics}]" if r.score_semantics else "")
                       for r in results)
    if reviewer_status in ("safe", "flagged") and not conflict:
        confidence = "high"
    elif auto_flags:
        confidence = "high"
    elif flags:
        confidence = "low"
    else:
        confidence = "unknown"
    review_notes = [f"cờ: {f.describe()}" for f in flags]
    review_notes += [n for r in results for n in r.notes if r.status in ("unknown", "flagged")]
    review_notes += notes

    reason_codes = list(reasons)
    if conflict:
        reason_codes.append("content_safety_conflict")
    for f in flags:
        if not f.auto_reject and f.reason not in reason_codes:
            reason_codes.append(f.reason)
    fields = {
        "content_safety_status": status,
        "language_detection_status": _language_status([r for r in results if r.scope in TEXT_SCOPES + AUDIO_SCOPES]),
        "detected_languages": ";".join(sorted({c for r in results for c in r.languages})),
        "text_moderation_status": _group_status([r for r in results if r.scope in TEXT_SCOPES]),
        "audio_moderation_status": _group_status([r for r in results if r.scope in AUDIO_SCOPES]),
        "visual_moderation_status": _group_status([r for r in results if r.scope in VISUAL_SCOPES]),
        **{SAFETY_FLAG_FIELDS[c]: v for c, v in detected.items()},
        "content_safety_score": "" if top is None else f"{top.score:.4f}",
        "content_safety_reason": ";".join(reason_codes),
        "moderation_confidence": confidence,
        "moderation_method": method,
        "review_required": "false" if reviewer_status in ("safe", "flagged") and not conflict else "true",
        "review_notes": " | ".join(review_notes),
    }
    return SafetySummary(fields, reasons, reviewer_status == "safe" and not conflict, conflict, notes)
