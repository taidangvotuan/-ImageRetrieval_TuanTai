"""Phụ đề YouTube (M2): lấy, đọc VTT, kiểm duyệt từng câu kèm timestamp.

* Lấy MỌI phụ đề do người đăng cung cấp + phụ đề tự động CHỈ của ngôn ngữ gốc (YouTube cung cấp
  hàng trăm bản dịch máy – không dùng vì sai lệch và không phản ánh nội dung gốc).
* File lưu trong ``dataset/moderation/subtitles/<filename>/`` (KHÔNG commit – là lời bài hát).
* Chính sách: phụ đề CHỈ gắn cờ (không tự loại) – phụ đề tự động có thể nhận sai lời hát.
* Ngôn ngữ lấy từ mã ngôn ngữ của file phụ đề (vd. ``en``, ``vi-orig`` -> ``vi``); ngôn ngữ không có
  lexicon -> ``unknown`` (``unsupported``). Không có phụ đề nào -> ``not_applicable``.
"""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from src.dataset.validation.content_safety.lexicon import STRONG, mask
from src.dataset.validation.content_safety.providers import CheckResult, Flag
from src.dataset.validation.content_safety.text_moderator import TextModerator

SCOPE = "subtitles"
TEXT_CATEGORIES = ["profanity_or_abusive_language", "violence_incitement", "explicit_or_obscene_content"]
_TIME = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{3})\s*-->")
_TAG = re.compile(r"<[^>]+>")
# Phụ đề tự động cuộn từng dòng: cùng nội dung lặp lại trong vòng vài giây là bản sao.
ROLLING_WINDOW_SEC = 2.0


@dataclass
class Cue:
    start: float
    text: str


def parse_vtt(text: str) -> list[Cue]:
    """Đọc WebVTT / SRT đơn giản: bỏ thẻ định dạng, bỏ dòng lặp của phụ đề tự động (cuộn từng dòng).

    Chỉ bỏ dòng lặp khi hai cue cách nhau < ROLLING_WINDOW_SEC; điệp khúc lặp lại sau đó được giữ
    (để có đủ timestamp của mọi lần xuất hiện).
    """
    cues: list[Cue] = []
    start, lines = None, []

    def flush():
        if start is not None and lines:
            body = " ".join(lines).strip()
            recent = cues and start - cues[-1].start < ROLLING_WINDOW_SEC
            if body and not (recent and body == cues[-1].text):
                cues.append(Cue(start, body))

    for raw in text.splitlines() + [""]:
        line = raw.strip()
        m = _TIME.search(line)
        if m:
            flush()
            h, mnt, s, ms = m.groups()
            start, lines = int(h or 0) * 3600 + int(mnt) * 60 + int(s) + int(ms) / 1000, []
        elif not line:
            flush()
            start, lines = None, []
        elif start is not None and not line.isdigit():
            clean = html.unescape(_TAG.sub("", line)).strip()
            rolling = cues and start - cues[-1].start < ROLLING_WINDOW_SEC and clean in cues[-1].text
            if clean and (not lines or clean != lines[-1]) and not rolling:
                lines.append(clean)
    return cues


def base_language(code: str) -> str:
    """'en-US' / 'vi-orig' / 'pt_BR' -> 'en' / 'vi' / 'pt'."""
    return re.split(r"[-_]", code or "")[0].lower()


def subtitle_dir(settings, filename: str) -> Path:
    return settings.paths.moderation / "subtitles" / filename


def choose_languages(info: Mapping, config: Mapping) -> dict[str, str]:
    """{mã ngôn ngữ yt-dlp: 'manual'|'auto'} cần tải."""
    exclude = set(config.get("exclude_languages", []))
    chosen = {code: "manual" for code in (info.get("subtitles") or {}) if code not in exclude}
    if config.get("include_automatic", True):
        original = info.get("language")
        for code in info.get("automatic_captions") or {}:
            if code in exclude or code in chosen:
                continue
            if code.endswith("-orig") or (original and code == original):
                chosen[code] = "auto"
    return chosen


def fetch_subtitles(url: str, out_dir: Path, config: Mapping) -> dict:
    """Tải phụ đề (không tải video). Trả manifest {'languages': [...], 'error': ...}."""
    import yt_dlp

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"url": url, "languages": [], "error": None}
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False)
        chosen = choose_languages(info, config)
        if chosen:
            fmt = config.get("format", "vtt")
            opts = {"quiet": True, "no_warnings": True, "skip_download": True, "writesubtitles": True,
                    "writeautomaticsub": True, "subtitleslangs": list(chosen), "subtitlesformat": fmt,
                    "outtmpl": str(out_dir / "sub.%(ext)s")}
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.extract_info(url, download=True)
            for code, kind in chosen.items():
                file = out_dir / f"sub.{code}.{fmt}"
                if file.exists():
                    manifest["languages"].append({"code": code, "kind": kind, "file": file.name})
    except Exception as exc:  # lỗi mạng / giới hạn tốc độ / video bị gỡ
        manifest["error"] = str(exc)[:500]
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def moderate_subtitles(moderator: TextModerator, out_dir: Path) -> CheckResult:
    method = f"youtube_subtitles + {moderator.lexicon.describe()} (chỉ gắn cờ)"
    try:
        manifest = json.loads((Path(out_dir) / "manifest.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return CheckResult(SCOPE, "unknown", "not_run",
                           notes=["subtitles: chưa chạy – python -m src.dataset.moderate_videos --stages subtitles"])
    if manifest.get("error"):
        return CheckResult(SCOPE, "unknown", method, notes=[f"subtitles: không lấy được phụ đề ({manifest['error'][:120]})"])
    if not manifest.get("languages"):
        return CheckResult(SCOPE, "not_applicable", method, notes=["subtitles: video không có phụ đề"])

    flags, languages, unsupported, notes = [], [], [], []
    for item in manifest["languages"]:
        code = base_language(item["code"])
        languages.append(code)
        cues = parse_vtt((Path(out_dir) / item["file"]).read_text(encoding="utf-8", errors="replace"))
        if not moderator.lexicon.supports(code):
            unsupported.append(code)
        seen = set()
        for cue in cues:
            for hit in moderator.lexicon.match(cue.text, moderator.config.get("text_variants")):
                entry = hit.entry
                key = (entry.term, item["code"])
                if key in seen:
                    continue
                seen.add(key)
                same = entry.language == code
                flags.append(Flag(entry.category, moderator._reason(entry.category),
                                  STRONG if entry.level == STRONG and same else "ambiguous", auto_reject=False,
                                  field=f"subtitle:{item['kind']}:{item['code']}", evidence=mask(hit.matched),
                                  language=entry.language, timestamp=round(cue.start, 1),
                                  note="" if same else f"phụ đề {code} ≠ lexicon {entry.language}"))
        if item["kind"] == "auto":
            notes.append(f"subtitles: phụ đề tự động ({item['code']}) có thể nhận sai lời hát")
    if unsupported:
        notes.append(f"subtitles: chưa có lexicon cho {', '.join(sorted(set(unsupported)))}")
    if flags:
        status = "flagged"
    elif unsupported:
        status = "unknown"
    else:
        status = "safe"
    return CheckResult(SCOPE, status, method, flags=flags,
                       covered_categories=TEXT_CATEGORIES if status == "safe" else [],
                       languages=sorted(set(languages)),
                       language_status="unsupported" if unsupported else "detected", notes=notes)
