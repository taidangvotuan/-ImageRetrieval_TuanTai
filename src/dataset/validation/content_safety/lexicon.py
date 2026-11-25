"""Nạp và so khớp lexicon từ ``config/moderation/lexicons/<lang>.csv`` (nơi DUY NHẤT chứa từ nhạy cảm).

Mỗi dòng: ``term, category, level (strong|ambiguous), match (normalized|exact), notes, added_by``.
File bắt đầu bằng ``_`` (vd. ``_TEMPLATE.csv``) và thư mục con (vd. ``drafts/``) KHÔNG được nạp.
So khớp nguyên từ trên mọi biến thể (``text_variants``):

* ``match=normalized`` (mặc định) – bỏ dấu: "phía sau" khớp "phia sau";
* ``match=exact`` – GIỮ dấu: bắt buộc với từ tiếng Việt ngắn mà bỏ dấu sẽ trùng từ thường
  ('lồn' / 'lớn' / 'lon bia' đều thành 'lon'; 'cặc' / 'các' -> 'cac'; 'đĩ' / 'đi' -> 'di').
"""
from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from src.dataset.schema import SAFETY_CATEGORIES
from src.dataset.validation.content_safety.text_variants import normalize_keep_marks, variants
from src.dataset.validation.exclusion_rules import normalize, phrase_regex

STRONG, AMBIGUOUS = "strong", "ambiguous"
NORMALIZED, EXACT = "normalized", "exact"


def _exact_regex(term: str) -> re.Pattern:
    words = normalize_keep_marks(term).split()
    return re.compile(r"(?<!\w)" + r"\s".join(re.escape(w) for w in words) + r"(?!\w)")


class LexiconError(ValueError):
    pass


@dataclass(frozen=True)
class Entry:
    term: str
    language: str
    category: str
    level: str
    regex: re.Pattern
    match_mode: str = NORMALIZED


@dataclass(frozen=True)
class Hit:
    entry: Entry
    matched: str


def mask(term: str) -> str:
    """Che từ nhạy cảm khi ghi vào metadata được commit: 'abcd' -> 'a***'."""
    term = term.strip()
    return term[:1] + "*" * max(len(term) - 1, 0)


class Lexicon:
    def __init__(self, entries: list[Entry], fingerprint: str):
        self.entries = entries
        self.fingerprint = fingerprint
        self.languages = sorted({e.language for e in entries})

    @classmethod
    def load(cls, directory: Path) -> "Lexicon":
        directory = Path(directory)
        entries: list[Entry] = []
        digest = hashlib.sha256()
        for file in sorted(directory.glob("*.csv")) if directory.is_dir() else []:
            if file.name.startswith("_"):
                continue
            language = file.stem.lower()
            data = file.read_bytes()
            digest.update(file.name.encode() + b"\0" + data)
            for line, row in enumerate(csv.DictReader(data.decode("utf-8-sig").splitlines()), start=2):
                term = (row.get("term") or "").strip()
                if not term or term.startswith("#"):
                    continue
                category = (row.get("category") or "").strip()
                level = (row.get("level") or "").strip().lower()
                if category not in SAFETY_CATEGORIES:
                    raise LexiconError(f"{file.name}:{line}: category '{category}' không hợp lệ ({SAFETY_CATEGORIES})")
                if level not in (STRONG, AMBIGUOUS):
                    raise LexiconError(f"{file.name}:{line}: level phải là strong hoặc ambiguous")
                mode = (row.get("match") or NORMALIZED).strip().lower()
                if mode not in (NORMALIZED, EXACT):
                    raise LexiconError(f"{file.name}:{line}: match phải là normalized hoặc exact")
                if not normalize(term):
                    raise LexiconError(f"{file.name}:{line}: term rỗng sau chuẩn hóa")
                regex = _exact_regex(term) if mode == EXACT else phrase_regex(term)
                entries.append(Entry(term, language, category, level, regex, mode))
        return cls(entries, digest.hexdigest()[:12] if entries else "empty")

    def supports(self, language: str) -> bool:
        return language in self.languages

    def describe(self) -> str:
        return f"lexicon@{self.fingerprint}({len(self.entries)} mục; {','.join(self.languages) or 'chưa có ngôn ngữ nào'})"

    def match(self, text: str | None, variant_config: Mapping | None = None) -> list[Hit]:
        if not text or not self.entries:
            return []
        forms = {NORMALIZED: variants(text, variant_config),
                 EXACT: variants(text, variant_config, keep_marks=True)}
        hits: dict[tuple, Hit] = {}
        for entry in self.entries:
            for form in forms[entry.match_mode]:
                if (m := entry.regex.search(form)):
                    hits.setdefault((entry.language, entry.term), Hit(entry, m.group(0)))
                    break
        return list(hits.values())
