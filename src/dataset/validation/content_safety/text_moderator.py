"""Kiểm duyệt văn bản bằng lexicon đa ngôn ngữ + nhận diện ngôn ngữ.

Dùng chung cho metadata (M1) và cho phụ đề / OCR / transcript ở các giai đoạn sau.

Quy tắc với mỗi đoạn văn bản (trường):

1. Nhận diện ngôn ngữ (không đoán – dưới ngưỡng là ``unknown``). Trường phụ (tên file, tags) quá
   ngắn để nhận diện nên dùng ngôn ngữ của tiêu đề làm ngữ cảnh.
2. So khớp mọi lexicon trên mọi biến thể (leetspeak, ký tự lặp, chữ bị tách).
3. Từ ``strong`` chỉ giữ mức strong khi ngôn ngữ của trường KHỚP ngôn ngữ của lexicon (cùng một
   chuỗi có thể tục ở ngôn ngữ này nhưng trung tính ở ngôn ngữ khác); ngược lại hạ thành ``ambiguous``.
4. Chỉ strong trên các trường trong ``auto_reject.metadata_fields`` mới được tự động loại.
5. Trường không có dấu hiệu nhưng ngôn ngữ ``unknown`` hoặc không có lexicon -> trường đó ``unknown``.
   "Không khớp từ nào" chỉ được coi là ``safe`` khi lexicon có ngôn ngữ đó.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from src.dataset.schema import SAFETY_CATEGORIES
from src.dataset.validation.content_safety import language as lang
from src.dataset.validation.content_safety.lexicon import STRONG, Lexicon, mask
from src.dataset.validation.content_safety.providers import CheckResult, Flag

AUXILIARY_FIELDS = ("filename", "tags")   # quá ngắn để nhận diện ngôn ngữ, không quyết định độ phủ


@dataclass
class _FieldOutcome:
    status: str
    language_status: str
    languages: list[str] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)
    note: str = ""


class TextModerator:
    def __init__(self, lexicon: Lexicon, safety_config: Mapping):
        self.lexicon = lexicon
        self.config = safety_config
        self.auto_fields = set(safety_config.get("auto_reject", {}).get("metadata_fields", []))
        self.categories = safety_config.get("categories", {})

    def method(self) -> str:
        cfg = self.config.get("language_detection", {})
        return (f"{self.lexicon.describe()} + {lang.detector_version()}"
                f"(min_confidence={cfg.get('min_confidence')}) – so khớp quy tắc, KHÔNG có điểm xác suất")

    def _reason(self, category: str) -> str:
        return self.categories.get(category, {}).get("reason", category)

    def _field(self, name: str, text: str, context_lang: list[str], auto_ok: bool) -> _FieldOutcome:
        if name in AUXILIARY_FIELDS:
            detected = lang.LanguageResult("detected", context_lang) if context_lang else lang.LanguageResult(
                "unknown", note="trường phụ, không có ngữ cảnh ngôn ngữ")
        else:
            detected = lang.detect(text, self.config.get("language_detection"))
        flags = []
        for hit in self.lexicon.match(text, self.config.get("text_variants")):
            entry = hit.entry
            same_lang = entry.language in detected.languages
            strong = entry.level == STRONG and same_lang
            note = "" if same_lang else f"ngôn ngữ trường={detected.languages or 'unknown'} ≠ lexicon {entry.language}"
            flags.append(Flag(entry.category, self._reason(entry.category), STRONG if strong else "ambiguous",
                              auto_reject=strong and auto_ok, field=name, evidence=mask(hit.matched),
                              language=entry.language, note=note))
        if flags:
            return _FieldOutcome("flagged", detected.status, detected.languages, flags)
        if detected.status != "detected":
            return _FieldOutcome("unknown", "unknown", [], note=f"{name}: ngôn ngữ chưa xác định ({detected.note})")
        unsupported = [code for code in detected.languages if not self.lexicon.supports(code)]
        if unsupported:
            return _FieldOutcome("unknown", "unsupported", detected.languages,
                                 note=f"{name}: chưa có lexicon cho {', '.join(unsupported)}")
        return _FieldOutcome("safe", "detected", detected.languages)

    def check(self, scope: str, fields: Mapping[str, str | list | None],
              auto_reject_fields: set[str] | None = None) -> CheckResult:
        """Kiểm duyệt các trường văn bản của một bước (``scope``)."""
        auto_fields = self.auto_fields if auto_reject_fields is None else auto_reject_fields
        texts = {}
        for name, value in fields.items():
            if value is None:
                continue
            if not isinstance(value, str):
                value = " ; ".join(str(v) for v in value)
            if value.strip():
                texts[name] = value
        if not texts:
            return CheckResult(scope, "not_applicable", self.method(), notes=[f"{scope}: không có văn bản"])

        title_lang = []
        outcomes = {}
        for name in sorted(texts, key=lambda n: n in AUXILIARY_FIELDS):   # trường chính trước
            outcome = self._field(name, texts[name], title_lang, name in auto_fields)
            outcomes[name] = outcome
            if name in ("title", "source_title") and outcome.languages and not title_lang:
                title_lang = outcome.languages

        flags = [f for o in outcomes.values() for f in o.flags]
        primary = {n: o for n, o in outcomes.items() if n not in AUXILIARY_FIELDS}
        languages = sorted({code for o in outcomes.values() for code in o.languages})
        if any(o.language_status == "unsupported" for o in primary.values()):
            language_status = "unsupported"
        elif primary and all(o.language_status == "detected" for o in primary.values()):
            language_status = "detected"
        elif primary:
            language_status = "unknown"
        else:
            language_status = "not_applicable"
        if flags:
            status = "flagged"
        elif primary and all(o.status == "safe" for o in primary.values()):
            status = "safe"
        else:
            status = "unknown"
        covered = list(SAFETY_CATEGORIES) if status == "safe" else []
        return CheckResult(scope, status, self.method(), flags=flags, covered_categories=covered,
                           languages=languages, language_status=language_status,
                           notes=[o.note for o in outcomes.values() if o.note])


def check_age_restriction(info: Mapping, safety_config: Mapping) -> CheckResult:
    """Giới hạn độ tuổi của nguồn (YouTube ``age_limit`` ≥ 18 hoặc lỗi 'confirm your age')."""
    age = info.get("age_limit")
    restricted = bool(info.get("age_restricted")) or (age is not None and int(age) >= 18)
    action = safety_config.get("auto_reject", {}).get("age_restricted", "reject")
    if restricted:
        return CheckResult("age_restriction", "flagged", "source_metadata(age_limit)", flags=[Flag(
            "age_restricted", "age_restricted", "strong", auto_reject=(action == "reject"),
            field="age_limit", evidence=str(age if age is not None else "18+"),
            note="nguồn giới hạn độ tuổi")])
    if age is None:
        return CheckResult("age_restriction", "unknown", "source_metadata(age_limit)",
                           notes=["age_restriction: nguồn không cung cấp age_limit"])
    return CheckResult("age_restriction", "safe", "source_metadata(age_limit)",
                       notes=[f"age_limit={age}"])
