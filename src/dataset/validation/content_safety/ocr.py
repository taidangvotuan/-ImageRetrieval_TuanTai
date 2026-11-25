"""OCR chữ trên khung hình (M2) bằng EasyOCR trên frame lấy mẫu.

Quy trình:
1. ``frame_sampler`` lấy frame mỗi ``sample_every_sec`` giây (đọc tuần tự, thu nhỏ ``max_width``).
2. Frame gần giống frame đã OCR (sai khác trung bình ảnh xám 64x36 < ``dedup_threshold``) được bỏ qua –
   MV thường giữ nguyên cảnh vài giây; tiết kiệm thời gian CPU (≈ 3 s / frame).
3. Chữ có độ tin cậy nhận dạng ≥ ``min_text_confidence`` được kiểm tra bằng lexicon. Hộp chữ dưới
   ngưỡng được đếm là "không đọc được" (thường là chữ ngoài ngôn ngữ OCR, vd. Thái/Hàn): tỷ lệ cao
   -> ``unknown``.
4. Ngôn ngữ của toàn bộ chữ OCR được nhận diện (không đoán); từ ``strong`` chỉ giữ mức strong khi
   ngôn ngữ khớp lexicon, và chỉ TỰ ĐỘNG LOẠI khi xuất hiện ở ≥ ``auto_reject.ocr_min_frames`` frame
   khác nhau (giảm nhận dạng sai ngẫu nhiên).

Giới hạn: lấy mẫu không bắt được chữ xuất hiện rất ngắn; EasyOCR chỉ đọc các ngôn ngữ đã nạp;
``safe`` của bước này nghĩa là "không thấy từ vi phạm trong chữ đọc được ở các frame lấy mẫu" –
không phải kết luận video an toàn.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Callable, Mapping

import numpy as np

from src.common.io_utils import atomic_json
from src.dataset.validation.content_safety import language as lang
from src.dataset.validation.content_safety.lexicon import STRONG, mask
from src.dataset.validation.content_safety.providers import CheckResult, Flag
from src.dataset.validation.content_safety.text_moderator import TextModerator
from src.video_processing.frame_sampler import SamplerStats, sample_frames

SCOPE = "ocr"
TEXT_CATEGORIES = ["profanity_or_abusive_language", "violence_incitement", "explicit_or_obscene_content"]


def easyocr_reader(languages: list[str]):
    """Reader EasyOCR trên CPU (mô hình tải về ~/.EasyOCR ở lần chạy đầu)."""
    import easyocr

    return easyocr.Reader(languages, gpu=False, verbose=False)


def easyocr_version() -> str:
    try:
        from importlib.metadata import version

        return f"easyocr@{version('easyocr')}"
    except Exception:
        return "easyocr@unavailable"


def _thumb(image: np.ndarray) -> np.ndarray:
    import cv2

    return cv2.resize(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), (64, 36), interpolation=cv2.INTER_AREA).astype(np.float32)


class OcrModerator:
    def __init__(self, moderator: TextModerator, config: Mapping, reader_factory: Callable | None = None):
        self.moderator = moderator
        self.config = config
        self.reader_factory = reader_factory or easyocr_reader
        self._reader = None

    def method(self) -> str:
        c = self.config
        return (f"{easyocr_version()}(langs={','.join(c['languages'])}; every={c['sample_every_sec']}s; "
                f"max_width={c['max_width']}; min_conf={c['min_text_confidence']}) + {self.moderator.lexicon.describe()}")

    def reader(self):
        if self._reader is None:
            self._reader = self.reader_factory(list(self.config["languages"]))
        return self._reader

    def read_video(self, path: Path) -> dict:
        """OCR các frame lấy mẫu -> {'frames': [{t, texts, low_conf}], 'stats': {...}}."""
        c = self.config
        stats = SamplerStats()
        frames, last, skipped, started = [], None, 0, time.perf_counter()
        for frame in sample_frames(path, float(c["sample_every_sec"]), int(c["max_width"]), stats=stats):
            thumb = _thumb(frame.image)
            if last is not None and float(np.abs(thumb - last).mean()) < float(c["dedup_threshold"]):
                skipped += 1
                continue
            last = thumb
            texts, low = [], 0
            for _bbox, text, conf in self.reader().readtext(frame.image):
                if float(conf) >= float(c["min_text_confidence"]):
                    texts.append(str(text))
                else:
                    low += 1
            frames.append({"t": frame.timestamp, "texts": texts, "low_conf": low})
        return {"frames": frames, "stats": {
            "frames_read": stats.frames_read, "frames_sampled": stats.frames_sampled, "frames_ocr": len(frames),
            "frames_skipped_similar": skipped, "timestamp_source": stats.timestamp_source,
            "seconds": round(time.perf_counter() - started, 1), "errors": stats.errors}}

    def evaluate(self, ocr: dict, auto_min_frames: int) -> CheckResult:
        frames = ocr["frames"]
        method = self.method()
        if ocr["stats"].get("errors") and not frames:
            return CheckResult(SCOPE, "unknown", method, notes=[f"ocr: lỗi đọc video {ocr['stats']['errors'][:2]}"])
        all_text = " ".join(t for f in frames for t in f["texts"])
        readable = sum(len(f["texts"]) for f in frames)
        unreadable = sum(f["low_conf"] for f in frames)
        detected = lang.detect(all_text, self.moderator.config.get("language_detection")) if all_text else None
        languages = detected.languages if detected and detected.status == "detected" else []

        occurrences: dict[tuple, list[float]] = defaultdict(list)
        entries, matched = {}, {}
        for f in frames:
            text = " ; ".join(f["texts"])
            for hit in self.moderator.lexicon.match(text, self.moderator.config.get("text_variants")):
                key = (hit.entry.language, hit.entry.term)
                occurrences[key].append(f["t"])
                entries[key], matched[key] = hit.entry, hit.matched
        flags = []
        for key, times in occurrences.items():
            entry = entries[key]
            same = entry.language in languages
            strong = entry.level == STRONG and same
            auto = strong and len(set(times)) >= auto_min_frames
            note = f"{len(set(times))} frame" + ("" if same else f"; ngôn ngữ chữ OCR={languages or 'unknown'} ≠ lexicon {entry.language}")
            flags.append(Flag(entry.category, self.moderator._reason(entry.category), STRONG if strong else "ambiguous",
                              auto_reject=auto, field="ocr_frame", evidence=mask(matched[key]),
                              language=entry.language, timestamp=min(times), note=note))
        notes = [f"ocr: {ocr['stats']['frames_ocr']} frame OCR / {ocr['stats']['frames_sampled']} frame lấy mẫu "
                 f"({ocr['stats']['frames_skipped_similar']} frame giống cảnh trước được bỏ qua), {ocr['stats']['seconds']} s"]
        total = readable + unreadable
        too_unreadable = total and unreadable / total >= float(self.config.get("unreadable_ratio_for_unknown", 0.3))
        if too_unreadable:
            notes.append(f"ocr: {unreadable}/{total} hộp chữ không đọc được (có thể ngôn ngữ ngoài "
                         f"{','.join(self.config['languages'])})")
        if flags:
            status = "flagged"
        elif readable == 0 and unreadable == 0:
            status, languages = "safe", []
            notes.append("ocr: không thấy chữ trên các frame lấy mẫu")
        elif too_unreadable or not languages or any(not self.moderator.lexicon.supports(code) for code in languages):
            status = "unknown"
            if all_text and not languages:
                notes.append(f"ocr: ngôn ngữ chữ chưa xác định ({detected.note if detected else ''})")
        else:
            status = "safe"
        language_status = ("not_applicable" if not all_text else "detected" if languages and
                           all(self.moderator.lexicon.supports(c) for c in languages) else
                           "unsupported" if languages else "unknown")
        return CheckResult(SCOPE, status, method, flags=flags,
                           covered_categories=TEXT_CATEGORIES if status == "safe" else [],
                           languages=languages, language_status=language_status, notes=notes)

    def run(self, path: Path, text_out: Path | None = None, auto_min_frames: int = 2) -> CheckResult:
        ocr = self.read_video(path)
        if text_out is not None:
            atomic_json(text_out, ocr)      # chữ OCR theo frame – không commit
        return self.evaluate(ocr, auto_min_frames)


def load_ocr_text(path: Path) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
