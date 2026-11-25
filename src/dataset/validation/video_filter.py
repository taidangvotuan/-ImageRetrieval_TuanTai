"""Phân loại nội dung video từ metadata nguồn (tiêu đề, tên file, mô tả, tags, kênh).

Chỉ suy luận được những gì luật strong chứng minh: kết quả có thể là ``rejected`` cho một tiêu
chí, hoặc ``unknown``. Bộ lọc **không bao giờ** trả về ``confirmed`` hay
``content_type=official_music_video`` – hai giá trị đó chỉ đến từ người duyệt (review_store).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from src.dataset.validation.exclusion_rules import STRONG, RuleSet, Signal

# Thứ tự ưu tiên khi suy ra content_type từ nhiều dấu hiệu strong.
_CONTENT_PRIORITY = ("behind_the_scenes", "lyrics_video", "teaser", "reaction")


@dataclass
class ContentAssessment:
    content_type: str = "unknown"
    statuses: dict[str, str] = field(default_factory=dict)  # status_field -> rejected / unknown
    reasons: list[str] = field(default_factory=list)          # mã lý do từ luật strong
    signals: list[Signal] = field(default_factory=list)

    @property
    def strong(self) -> list[Signal]:
        return [s for s in self.signals if s.level == STRONG]

    @property
    def weak(self) -> list[Signal]:
        return [s for s in self.signals if s.level != STRONG]


def load_source_info(path: Path) -> dict:
    """Đọc metadata gốc từ nguồn (``metadata/source_info/<filename>.json``) nếu có."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def assess_content(rules: RuleSet, *, title: str = "", filename: str = "", source_title: str = "",
                   description: str = "", tags=None, uploader: str = "") -> ContentAssessment:
    signals = rules.evaluate({"title": title, "source_title": source_title, "filename": filename,
                              "description": description, "tags": tags or []}, uploader=uploader)
    result = ContentAssessment(signals=signals)
    for rule in rules.rules:
        if rule.status_field:
            result.statuses[rule.status_field] = "unknown"
    content_types = []
    for sig in result.strong:
        rule = rules.rule(sig.category)
        if sig.reason not in result.reasons:
            result.reasons.append(sig.reason)
        if rule.status_field:
            result.statuses[rule.status_field] = "rejected"
        if rule.content_type:
            content_types.append(rule.content_type)
    for ct in _CONTENT_PRIORITY:
        if ct in content_types:
            result.content_type = ct
            break
    else:
        if content_types:
            result.content_type = "other"
    return result
