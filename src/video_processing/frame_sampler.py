"""Lấy mẫu frame theo thời gian – đọc TUẦN TỰ, không nạp cả video vào RAM.

Dùng chung cho kiểm duyệt nội dung (OCR, hình ảnh – M2/M4) và pipeline xử lý chính thức (GĐ3).

* Timestamp lấy từ bộ giải mã (``CAP_PROP_POS_MSEC`` – PTS của frame vừa đọc), không suy ra từ
  ``frame_index / fps`` vì video có thể có FPS biến thiên. Chỉ khi backend không trả PTS (luôn 0)
  mới dùng ``index / fps`` và ghi ``timestamp_source = "index/fps"``.
* ``grab()`` cho mọi frame (giải mã nhẹ), ``retrieve()`` chỉ cho frame được lấy mẫu.
* Một frame được lấy khi timestamp ≥ mốc kế tiếp (0, Δ, 2Δ, …) – sai số timestamp ≤ 1 frame.
* Timestamp không tăng (PTS lỗi) được ghi nhận trong ``SamplerStats.non_monotonic``.

Hàm này KHÔNG kiểm tra trạng thái accepted: pipeline chính thức phải lấy đường dẫn từ
``accepted_registry``; bước kiểm duyệt (đọc video pending_review) là ngoại lệ đã được chấp thuận.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

import numpy as np


@dataclass
class SampledFrame:
    index: int            # chỉ số frame trong video (0-based)
    timestamp: float      # giây
    image: np.ndarray     # BGR, có thể đã thu nhỏ


@dataclass
class SamplerStats:
    frames_read: int = 0
    frames_sampled: int = 0
    non_monotonic: int = 0
    timestamp_source: str = "pts"
    duration_read: float = 0.0
    errors: list[str] = field(default_factory=list)


def _resize(image: np.ndarray, max_width: int | None) -> np.ndarray:
    import cv2

    if not max_width or image.shape[1] <= max_width:
        return image
    scale = max_width / image.shape[1]
    return cv2.resize(image, (max_width, int(round(image.shape[0] * scale))), interpolation=cv2.INTER_AREA)


def sample_frames(path: Path, every_sec: float = 1.0, max_width: int | None = None,
                  start_sec: float = 0.0, end_sec: float | None = None,
                  stats: SamplerStats | None = None) -> Iterator[SampledFrame]:
    """Sinh frame mỗi ``every_sec`` giây trong [start_sec, end_sec]."""
    import cv2

    if every_sec <= 0:
        raise ValueError("every_sec phải > 0")
    stats = stats if stats is not None else SamplerStats()
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        stats.errors.append("OpenCV không mở được video")
        return
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
        next_t, index, last_t, pts_seen = start_sec, -1, -1.0, False
        while True:
            if not cap.grab():
                break
            index += 1
            stats.frames_read += 1
            pts = float(cap.get(cv2.CAP_PROP_POS_MSEC) or 0) / 1000.0
            if pts > 0:
                pts_seen = True
            if pts_seen:
                t = pts
            else:
                t = index / fps if fps > 0 else 0.0
                if index > 0:
                    stats.timestamp_source = "index/fps"
            if t < last_t:
                stats.non_monotonic += 1
            last_t = max(last_t, t)
            stats.duration_read = last_t
            if end_sec is not None and t > end_sec:
                break
            if t + 1e-6 < next_t:
                continue
            ok, frame = cap.retrieve()
            if not ok or frame is None:
                stats.errors.append(f"không giải mã được frame {index}")
                continue
            stats.frames_sampled += 1
            while next_t <= t + 1e-6:
                next_t += every_sec
            yield SampledFrame(index, round(t, 3), _resize(frame, max_width))
    finally:
        cap.release()
