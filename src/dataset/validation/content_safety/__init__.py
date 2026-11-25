"""Kiểm duyệt an toàn nội dung (tiêu chuẩn cộng đồng) – xem docs/CONTENT_MODERATION.md.

Các bước (``scope``) và trạng thái triển khai:

| scope              | phương pháp                                   | giai đoạn |
|--------------------|-----------------------------------------------|-----------|
| metadata           | lexicon đa ngôn ngữ + nhận diện ngôn ngữ       | M1 ✅     |
| age_restriction    | ``age_limit`` / lỗi giới hạn tuổi của nguồn    | M1 ✅     |
| subtitles, ocr     | phụ đề YouTube, EasyOCR trên frame lấy mẫu     | M2 ✅ (chạy bằng ``moderate_videos``) |
| audio_transcript   | faster-whisper + kiểm duyệt văn bản            | M3        |
| visual             | NudeNet + CLIP zero-shot                       | M4        |

Bước chưa triển khai trả ``unknown`` (không giả lập). Kết quả của các bước nặng được lưu trong
``dataset/moderation/<video_id>/<scope>.json`` (KHÔNG commit – có thể chứa lời bài hát), gắn với
sha256 của file: file đổi thì kết quả cũ bị bỏ qua.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Mapping

from src.common.io_utils import atomic_json
from src.dataset.validation.content_safety.aggregator import SafetySummary, summarize
from src.dataset.validation.content_safety.lexicon import Lexicon
from src.dataset.validation.content_safety.providers import CheckResult, not_implemented
from src.dataset.validation.content_safety.text_moderator import TextModerator, check_age_restriction

STORED_SCOPES = ("subtitles", "ocr", "audio_transcript", "visual")

__all__ = ["CheckResult", "SafetySummary", "moderate_video", "summarize", "text_moderator_for"]


@lru_cache(maxsize=8)
def _lexicon(directory: str, mtime_key: float) -> Lexicon:
    return Lexicon.load(Path(directory))


def text_moderator_for(settings) -> TextModerator:
    directory = Path(settings.lexicon_dir)
    key = max((f.stat().st_mtime for f in directory.glob("*.csv")), default=0.0) if directory.is_dir() else 0.0
    return TextModerator(_lexicon(str(directory), key), settings.criteria["content_safety"])


def _load_stored(settings, video_id: str, sha256: str, scope: str) -> CheckResult | None:
    path = settings.paths.moderation / video_id / f"{scope}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    if data.get("sha256") != sha256:
        result = not_implemented(scope)
        result.method = "stale"
        result.notes = [f"{scope}: kết quả cũ bị bỏ qua vì file đã thay đổi – chạy lại kiểm duyệt"]
        return result
    return CheckResult.from_dict(data["result"])


def save_result(settings, video_id: str, sha256: str, result: CheckResult) -> Path:
    path = settings.paths.moderation / video_id / f"{result.scope}.json"
    atomic_json(path, {"video_id": video_id, "sha256": sha256, "result": result.to_dict()})
    return path


def moderate_video(settings, row: Mapping, info: Mapping, source: Mapping) -> list[CheckResult]:
    """Chạy các bước nhẹ (metadata, giới hạn tuổi) và nạp kết quả đã lưu của các bước nặng."""
    safety = settings.criteria["content_safety"]
    moderator = text_moderator_for(settings)
    title = row.get("title") or source.get("title") or ""
    results = [
        moderator.check("metadata", {"title": title, "source_title": info.get("title") if info.get("title") != title else None,
                                     "description": info.get("description"), "tags": info.get("tags") or [],
                                     "filename": row.get("filename")}),
        check_age_restriction(info, safety),
    ]
    planned = safety.get("methods_not_implemented", {})
    for scope in STORED_SCOPES:
        stored = _load_stored(settings, row.get("video_id", ""), row.get("sha256", ""), scope)
        if stored is None:
            if scope in planned:
                stored = not_implemented(scope, planned[scope])
            else:
                stored = CheckResult(scope, "unknown", "not_run", notes=[
                    f"{scope}: chưa chạy – python -m src.dataset.moderate_videos --stages {scope}"])
        results.append(stored)
    if row.get("video_id") and settings.paths.metadata.exists():
        save_result(settings, row["video_id"], row.get("sha256", ""), results[0])
    return results
