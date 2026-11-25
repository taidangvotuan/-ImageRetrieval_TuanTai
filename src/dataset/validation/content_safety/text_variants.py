"""Khử cách viết né bộ lọc trước khi so khớp lexicon.

Sinh nhiều biến thể của cùng một văn bản (đều đã chuẩn hóa: bỏ dấu, chữ thường, giữ ranh giới đoạn):

* gốc – ``segment(text)`` như luật loại trừ Dataset;
* leetspeak – ``0→o``, ``@→a``, ``$→s``… (áp dụng TRƯỚC khi bỏ dấu câu, vì ``@`` / ``$`` là dấu câu);
* ký tự lặp – chuỗi ≥ 3 ký tự giống nhau rút còn ``max_repeat`` và còn 1 (``baaaad`` → ``baad`` / ``bad``);
  chuỗi 2 ký tự (``good``) giữ nguyên để không tạo từ mới;
* chữ bị tách – ≥ 3 chữ cái đơn cách nhau (``b . a . d`` / ``b a d``) được nối lại.

Cấu hình trong ``content_safety.text_variants`` của ``config/dataset_criteria.json``.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Mapping

from src.dataset.validation.exclusion_rules import _BOUNDARY, _PUNCT, _SPACES, SEG, segment

_SPACED = re.compile(r"(?<!\w)(\w)(?: (\w)){2,}(?!\w)")


def _collapse(text: str, keep: int) -> str:
    return re.sub(r"(\w)\1{2,}", lambda m: m.group(1) * keep, text)


def _join_spaced(text: str) -> str:
    def join(m: re.Match) -> str:
        return m.group(0).replace(" ", "")
    return SEG.join(_SPACED.sub(join, part) for part in text.split(SEG))


def normalize_keep_marks(text: str | None) -> str:
    """Chữ thường + NFC + dấu câu -> khoảng trắng, NHƯNG giữ dấu tiếng Việt (và 'đ')."""
    text = unicodedata.normalize("NFC", text or "").lower()
    return _SPACES.sub(" ", _PUNCT.sub(" ", text)).strip()


def segment_keep_marks(text: str | None) -> str:
    parts = [normalize_keep_marks(p) for p in _BOUNDARY.split(text or "")]
    return SEG.join(p for p in parts if p)


def variants(text: str | None, config: Mapping | None = None, keep_marks: bool = False) -> list[str]:
    """Các biến thể chuẩn hóa (không trùng lặp, biến thể gốc đứng đầu).

    ``keep_marks=True``: giữ dấu – dùng cho mục lexicon ``match=exact`` (vd. từ tiếng Việt ngắn mà
    bỏ dấu sẽ trùng từ thường: 'lồn'/'lớn' -> 'lon').
    """
    config = config or {}
    seg = segment_keep_marks if keep_marks else segment
    raw = text or ""
    out = [seg(raw)]
    leet = config.get("leet_map") or {}
    if leet:
        swapped = "".join(leet.get(ch, ch) for ch in raw.lower())
        out.append(seg(swapped))
    max_repeat = int(config.get("max_repeat", 2))
    for base in list(out):
        out.append(_collapse(base, max_repeat))
        out.append(_collapse(base, 1))
    if config.get("join_spaced_letters", True):
        out += [_join_spaced(v) for v in list(out)]
    seen, unique = set(), []
    for v in out:
        if v and v not in seen:
            seen.add(v)
            unique.append(v)
    return unique
