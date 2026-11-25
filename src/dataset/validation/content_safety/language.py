"""Nhận diện ngôn ngữ cho văn bản (tiêu đề, mô tả, phụ đề, OCR, transcript).

Dùng ``lingua-language-detector`` (offline, ~75 ngôn ngữ). Nguyên tắc: **không đoán**.

* Văn bản ít chữ cái hơn ``min_letters`` hoặc độ tin cậy cao nhất < ``min_confidence`` -> ``unknown``.
  (Thử thực tế: "크래시2 OST Smash It" ra tiếng Swahili 0,12; "ok" ra tiếng Zulu 0,06.)
* Văn bản dài: nhận diện từng đoạn (``detect_multiple_languages_of``) để bắt được văn bản trộn ngôn ngữ.
* Thiếu thư viện -> ``unknown`` kèm ghi chú, không giả lập.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Mapping

logger = logging.getLogger(__name__)

MULTI_THRESHOLD_LETTERS = 200


@dataclass
class LanguageResult:
    status: str                                   # detected / unknown
    languages: list[str] = field(default_factory=list)   # ISO 639-1
    confidence: float | None = None
    note: str = ""


@lru_cache(maxsize=1)
def _detector():
    try:
        from lingua import LanguageDetectorBuilder
    except ImportError:
        return None
    return LanguageDetectorBuilder.from_all_languages().build()


def detector_version() -> str:
    try:
        from importlib.metadata import version

        return f"lingua@{version('lingua-language-detector')}"
    except Exception:
        return "lingua@unavailable"


def _code(language) -> str:
    return language.iso_code_639_1.name.lower()


def detect(text: str | None, config: Mapping | None = None) -> LanguageResult:
    config = config or {}
    min_conf = float(config.get("min_confidence", 0.5))
    min_letters = int(config.get("min_letters", 12))
    text = (text or "").strip()
    letters = sum(ch.isalpha() for ch in text)
    if letters == 0:
        return LanguageResult("unknown", note="không có chữ")
    if letters < min_letters:
        return LanguageResult("unknown", note=f"quá ngắn ({letters} chữ cái < {min_letters})")
    det = _detector()
    if det is None:
        return LanguageResult("unknown", note="chưa cài lingua-language-detector")

    if letters >= MULTI_THRESHOLD_LETTERS:
        langs: list[str] = []
        for part in det.detect_multiple_languages_of(text):
            segment = text[part.start_index:part.end_index]
            if sum(ch.isalpha() for ch in segment) < min_letters:
                continue
            top = det.compute_language_confidence_values(segment)[0]
            if top.value >= min_conf and _code(top.language) not in langs:
                langs.append(_code(top.language))
        if langs:
            return LanguageResult("detected", langs, note="nhiều đoạn")
        return LanguageResult("unknown", note="không đoạn nào đủ độ tin cậy")

    top = det.compute_language_confidence_values(text)[0]
    if top.value < min_conf:
        return LanguageResult("unknown", confidence=round(top.value, 3),
                              note=f"độ tin cậy {top.value:.2f} < {min_conf} (đoán {_code(top.language)})")
    return LanguageResult("detected", [_code(top.language)], round(top.value, 3))
