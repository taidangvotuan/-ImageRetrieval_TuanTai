"""Bộ máy luật loại trừ: chuẩn hóa văn bản và so khớp luật từ ``config/dataset_criteria.json``.

Nguyên tắc (xem docs/DATASET_CRITERIA.md):

* Chuẩn hóa: bỏ dấu tiếng Việt, chữ thường, dấu câu / gạch nối -> khoảng trắng, gộp khoảng trắng.
  Nhờ vậy "Behind-the-Scenes", "behind the scenes" và "BEHIND  THE SCENES" là một.
* So khớp **nguyên cụm theo ranh giới từ**: "lyric" không khớp "lyrical", "audio" không khớp
  "audiovisual". Không dùng tìm chuỗi con.
* ``strong`` chỉ có hiệu lực trên tiêu đề / tên file. Khớp trong mô tả, tags luôn bị hạ thành
  ``weak`` (ghi chú cho người duyệt, không tự loại). Luật có ``scan_description=false`` bỏ qua mô tả.
* Không có luật nào dùng "ai" đứng một mình: chỉ cụm rõ nghĩa ("ai generated"…) hoặc luật
  khoảng cách "ai … music video".
* Khớp ``ambiguous`` (vd. "BTS") bị bỏ qua khi ngữ cảnh cho thấy đó là tên nghệ sĩ.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, Mapping

STRONG, WEAK = "strong", "weak"
# Trường được dùng làm căn cứ và mức tối đa của luật trên trường đó.
PRIMARY_FIELDS = ("title", "source_title", "filename")
SECONDARY_FIELDS = ("description", "tags")

_PUNCT = re.compile(r"[^\w\s]|_", re.UNICODE)
_SPACES = re.compile(r"\s+")
# Ranh giới đoạn: ngoặc, |, :, ;, /, dấu gạch đứng riêng (có khoảng trắng hai bên), – —, dấu phẩy.
# Cụm từ KHÔNG được khớp xuyên qua ranh giới: "Song (Official MV) BTS" không phải "mv bts".
_BOUNDARY = re.compile(r"[()\[\]{}（）【】「」『』|｜:;/,，–—]|\s-+\s|^-+\s|\s-+$")
SEG = " | "


def _strip_marks(text: str) -> str:
    text = (text or "").replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return unicodedata.normalize("NFC", text).lower()


def normalize(text: str | None) -> str:
    """'Phía Sau Hậu-Trường!' -> 'phia sau hau truong'. Giữ chữ không phải Latin (vd. 방탄소년단)."""
    return _SPACES.sub(" ", _PUNCT.sub(" ", _strip_marks(text))).strip()


def segment(text: str | None) -> str:
    """Như ``normalize`` nhưng giữ ranh giới đoạn dưới dạng ' | ' để luật không khớp xuyên đoạn."""
    parts = [normalize(p) for p in _BOUNDARY.split(text or "")]
    return SEG.join(p for p in parts if p)


def _filename_text(name: str) -> str:
    """'yt_abc-Behind_The_Scenes.mp4' -> văn bản so khớp (bỏ phần mở rộng)."""
    return re.sub(r"\.[A-Za-z0-9]{2,4}$", "", name or "")


def phrase_regex(phrase: str) -> re.Pattern:
    words = normalize(phrase).split()
    if not words:
        raise ValueError(f"Cụm từ rỗng sau chuẩn hóa: {phrase!r}")
    return re.compile(r"(?<!\w)" + r"\s".join(re.escape(w) for w in words) + r"(?!\w)")


@dataclass(frozen=True)
class Signal:
    """Một dấu hiệu khớp luật."""

    category: str          # tên nhóm luật (ai_generated, lyrics_video, …)
    reason: str            # mã lý do loại trừ (xem schema.REJECTION_REASONS)
    level: str             # strong / weak
    field: str             # trường khớp (title, description, …)
    matched: str           # đoạn văn bản / cụm khớp
    note: str = ""

    def describe(self) -> str:
        extra = f", {self.note}" if self.note else ""
        return f"{self.category}:{self.level}:{self.field}='{self.matched}'{extra}"


@dataclass
class _Pattern:
    level: str
    ambiguous: bool
    label: str
    regex: re.Pattern | None = None            # phrase (trên văn bản chuẩn hóa) hoặc regex thô
    raw: bool = False                          # True: regex chạy trên văn bản gốc (chữ thường)
    left: list[re.Pattern] = field(default_factory=list)
    right: list[re.Pattern] = field(default_factory=list)
    max_gap: int = 0
    uppercase_token: str | None = None         # vd. "AI": chỉ strong khi văn bản gốc viết đúng "AI"

    def find(self, text_norm: str, text_raw: str) -> str | None:
        if self.left:
            return self._find_proximity(text_norm)
        target = text_raw.lower() if self.raw else text_norm
        m = self.regex.search(target)
        return m.group(0) if m else None

    def _find_proximity(self, text: str) -> str | None:
        for left in self.left:
            for lm in left.finditer(text):
                tail = text[lm.end():].split(SEG)[0]          # không vượt ranh giới đoạn
                for right in self.right:
                    rm = right.search(tail)
                    if rm and len(tail[:rm.start()].split()) <= self.max_gap:
                        return text[lm.start(): lm.end() + rm.end()]
        return None


@dataclass
class Rule:
    category: str
    reason: str
    status_field: str | None
    content_type: str | None
    scan_description: bool
    patterns: list[_Pattern]
    downgrade: list[re.Pattern]
    exception_phrases: list[re.Pattern]
    exception_uploaders: set[str]
    downgrade_if_official_and_subtitled: bool


def _compile_pattern(spec: Mapping) -> _Pattern:
    level = spec.get("level", STRONG)
    if level not in (STRONG, WEAK):
        raise ValueError(f"level không hợp lệ: {spec}")
    ambiguous = bool(spec.get("ambiguous", False))
    if "phrase" in spec:
        pattern = _Pattern(level, ambiguous, spec["phrase"], regex=phrase_regex(spec["phrase"]))
    elif "regex" in spec:
        pattern = _Pattern(level, ambiguous, spec["regex"], regex=re.compile(spec["regex"], re.IGNORECASE), raw=True)
    elif "proximity" in spec:
        prox = spec["proximity"]
        pattern = _Pattern(level, ambiguous, f"{prox['left']}…{prox['right']}",
                           left=[phrase_regex(x) for x in prox["left"]],
                           right=[phrase_regex(x) for x in prox["right"]], max_gap=int(prox.get("max_gap", 3)))
    else:
        raise ValueError(f"Pattern cần phrase / regex / proximity: {spec}")
    pattern.uppercase_token = spec.get("requires_uppercase")
    return pattern


def _has_uppercase_token(raw: str, token: str) -> bool:
    """Token viết hoa đúng (vd. 'AI') và văn bản không viết hoa toàn bộ (khi đó không phân biệt được)."""
    if raw.upper() == raw:
        return False
    return re.search(rf"(?<![^\W\d_]){re.escape(token)}(?![^\W\d_])", raw) is not None


class RuleSet:
    """Tập luật loại trừ, nạp từ mục ``exclusion_rules`` của file tiêu chí."""

    def __init__(self, criteria: Mapping):
        self.official = [phrase_regex(p) for p in criteria.get("official_markers", [])]
        self.subtitle = [phrase_regex(p) for p in criteria.get("subtitle_markers", [])]
        self.rules: list[Rule] = []
        for category, spec in criteria["exclusion_rules"].items():
            if category.startswith("_"):
                continue
            exceptions = spec.get("ambiguous_exceptions", {})
            self.rules.append(Rule(
                category=category,
                reason=spec.get("reason", category),
                status_field=spec.get("status_field"),
                content_type=spec.get("content_type"),
                scan_description=bool(spec.get("scan_description", False)),
                patterns=[_compile_pattern(p) for p in spec["patterns"]],
                downgrade=[phrase_regex(p) for p in spec.get("downgrade_if", {}).get("phrases", [])],
                exception_phrases=[phrase_regex(p) for p in exceptions.get("phrases", [])],
                exception_uploaders={normalize(u) for u in exceptions.get("uploaders", [])},
                downgrade_if_official_and_subtitled=bool(spec.get("downgrade_if_official_and_subtitled", False)),
            ))

    @classmethod
    def from_settings(cls, settings=None) -> "RuleSet":
        from config.settings import load_criteria

        return cls(settings.criteria if settings is not None else load_criteria())

    def add_custom(self, phrases: Iterable[str], reason: str = "unofficial_or_fan_made") -> None:
        """Thêm cụm strong từ CLI (vd. --exclude-keyword) vào nhóm 'custom'."""
        phrases = [p for p in phrases if p.strip()]
        if phrases:
            self.rules.append(Rule("custom", reason, None, "other", False,
                                   [_Pattern(STRONG, False, p, regex=phrase_regex(p)) for p in phrases],
                                   [], [], set(), False))

    # ------------------------------------------------------------------ đánh giá
    def has_official_marker(self, text: str) -> bool:
        norm = normalize(text)
        return any(r.search(norm) for r in self.official)

    def evaluate(self, fields: Mapping[str, str | Iterable[str] | None],
                 uploader: str | None = None) -> list[Signal]:
        """Trả về mọi dấu hiệu khớp trên các trường (title, source_title, filename, description, tags)."""
        texts: dict[str, tuple[str, str]] = {}
        for name, value in fields.items():
            if value is None:
                continue
            if not isinstance(value, str):
                value = " ; ".join(str(v) for v in value)
            if name == "filename":
                value = _filename_text(value)
            if value.strip():
                texts[name] = (segment(value), value)
        # Marker / ngoại lệ so trên văn bản không có ranh giới (vd. "BTS (방탄소년단)").
        plain = {name: normalize(raw) for name, (_, raw) in texts.items()}
        primary = " ".join(plain[f] for f in PRIMARY_FIELDS if f in plain)
        uploader_norm = normalize(uploader)

        signals: list[Signal] = []
        for rule in self.rules:
            for name, (norm, raw) in texts.items():
                secondary = name not in PRIMARY_FIELDS
                if secondary and not rule.scan_description:
                    continue
                for pattern in rule.patterns:
                    matched = pattern.find(norm, raw)
                    if matched is None:
                        continue
                    level, note = pattern.level, ""
                    if pattern.ambiguous and self._is_artist_context(rule, plain[name], uploader_norm):
                        continue  # vd. "BTS" là tên nhóm nhạc, không phải hậu trường
                    if (level == STRONG and pattern.uppercase_token
                            and not _has_uppercase_token(raw, pattern.uppercase_token)):
                        level, note = WEAK, f"'{pattern.uppercase_token.lower()}' có thể là từ tiếng Việt"
                    if level == STRONG and any(r.search(plain[name]) for r in rule.downgrade):
                        level, note = WEAK, "chỉ hỗ trợ hậu kỳ"
                    if (level == STRONG and rule.downgrade_if_official_and_subtitled
                            and any(r.search(primary) for r in self.official)
                            and any(r.search(primary) for r in self.subtitle)):
                        level, note = WEAK, "MV chính thức có phụ đề"
                    if level == STRONG and secondary:
                        level, note = WEAK, "chỉ xuất hiện trong mô tả/tags"
                    signals.append(Signal(rule.category, rule.reason, level, name, matched, note))
        return _dedupe(signals)

    def rule(self, category: str) -> Rule:
        return next(r for r in self.rules if r.category == category)

    @staticmethod
    def _is_artist_context(rule: Rule, text_norm: str, uploader_norm: str) -> bool:
        if uploader_norm and uploader_norm in rule.exception_uploaders:
            return True
        return any(r.search(text_norm) for r in rule.exception_phrases)


def _dedupe(signals: list[Signal]) -> list[Signal]:
    """Mỗi (category, field) giữ dấu hiệu mạnh nhất; strong đứng trước."""
    best: dict[tuple[str, str], Signal] = {}
    for sig in signals:
        key = (sig.category, sig.field)
        if key not in best or (sig.level == STRONG and best[key].level == WEAK):
            best[key] = sig
    return sorted(best.values(), key=lambda s: (s.level != STRONG, s.category, s.field))
