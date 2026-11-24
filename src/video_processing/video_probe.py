"""Trích xuất metadata và kiểm tra khả năng đọc của một file video.

Dùng hai nguồn độc lập để đối chiếu:

* **OpenCV** (``cv2.VideoCapture``): FPS, số frame, kích thước, đọc thử frame.
* **FFmpeg** (binary đi kèm ``imageio-ffmpeg``): thời lượng của container và codec,
  đọc từ header (``ffmpeg -i``) nên không phải giải mã toàn bộ video.

Không đọc toàn bộ video vào RAM: chỉ giải mã vài frame mẫu (đầu, giữa, cuối).
Nếu thời lượng tính từ ``frame_count / fps`` lệch đáng kể so với container,
video có thể là VFR (variable frame rate) hoặc header sai -> cảnh báo, và các
giai đoạn sau phải dùng PTS thay vì ``frame_idx / fps``.
"""
from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_VIDEO_STREAM_RE = re.compile(r"Stream #\S+.*?: Video: (\w+)")
_FPS_RE = re.compile(r"(\d+(?:\.\d+)?) fps")

# Thời lượng lệch > 2% hoặc > 2 giây giữa OpenCV và container -> cảnh báo.
DURATION_MISMATCH_RATIO = 0.02
DURATION_MISMATCH_ABS_SEC = 2.0


@dataclass
class VideoProbe:
    readable: bool
    duration_seconds: float | None = None
    container_duration_seconds: float | None = None
    fps: float | None = None
    total_frames: int | None = None
    width: int | None = None
    height: int | None = None
    codec: str | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def ffmpeg_executable() -> str | None:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # thiếu gói hoặc không tải được binary
        logger.warning("Không tìm thấy FFmpeg (imageio-ffmpeg): %s", exc)
        return None


def probe_container(path: Path, timeout: float = 60) -> dict:
    """Đọc header bằng ``ffmpeg -i`` (stderr). Trả về dict có thể rỗng."""
    exe = ffmpeg_executable()
    if exe is None:
        return {}
    try:
        result = subprocess.run(
            [exe, "-hide_banner", "-i", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"error": "ffmpeg timeout"}
    text = result.stderr
    info: dict = {}
    if m := _DURATION_RE.search(text):
        h, mnt, s = m.groups()
        info["duration"] = int(h) * 3600 + int(mnt) * 60 + float(s)
    if m := _VIDEO_STREAM_RE.search(text):
        info["codec"] = m.group(1)
        line = text[m.start(): text.find("\n", m.start())]
        if f := _FPS_RE.search(line):
            info["fps"] = float(f.group(1))
    if "Invalid data found" in text or "moov atom not found" in text:
        info["error"] = "ffmpeg: invalid data / container hỏng"
    return info


def _read_frame_at(cap, index: int) -> bool:
    import cv2

    cap.set(cv2.CAP_PROP_POS_FRAMES, max(index, 0))
    ok, frame = cap.read()
    return bool(ok and frame is not None and frame.size > 0)


def probe_video(path: Path, check_frames: bool = True) -> VideoProbe:
    """Mở video, lấy metadata, giải mã thử frame đầu/giữa/cuối."""
    import cv2

    path = Path(path)
    if not path.is_file():
        return VideoProbe(readable=False, errors=["file không tồn tại"])
    if path.stat().st_size == 0:
        return VideoProbe(readable=False, errors=["file rỗng (0 byte)"])

    container = probe_container(path)
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            errors = ["OpenCV không mở được video"]
            if container.get("error"):
                errors.append(container["error"])
            return VideoProbe(readable=False, errors=errors,
                              container_duration_seconds=container.get("duration"), codec=container.get("codec"))

        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        probe = VideoProbe(
            readable=True, fps=round(fps, 6) if fps > 0 else None, total_frames=frames or None,
            width=width or None, height=height or None,
            container_duration_seconds=container.get("duration"), codec=container.get("codec"),
        )
        if container.get("error"):
            probe.warnings.append(container["error"])
        if fps <= 0:
            probe.errors.append("FPS không hợp lệ (0)")
        if frames <= 0:
            probe.errors.append("Không xác định được số frame")
        if not width or not height:
            probe.errors.append("Không xác định được độ phân giải")

        cv_duration = frames / fps if fps > 0 and frames > 0 else None
        # Ưu tiên thời lượng container (chính xác hơn với VFR); fallback OpenCV.
        probe.duration_seconds = round(container.get("duration") or cv_duration or 0, 3) or None
        if cv_duration and probe.container_duration_seconds:
            diff = abs(cv_duration - probe.container_duration_seconds)
            if diff > max(DURATION_MISMATCH_ABS_SEC, DURATION_MISMATCH_RATIO * probe.container_duration_seconds):
                probe.warnings.append(
                    f"Thời lượng lệch: OpenCV {cv_duration:.1f}s vs container "
                    f"{probe.container_duration_seconds:.1f}s (có thể VFR) -> dùng PTS khi xử lý"
                )

        if check_frames and frames > 0:
            # Frame cuối đôi khi không seek được do frame_count trong header ước lượng;
            # thử lùi vài frame trước khi kết luận hỏng.
            checks = {"đầu": [0], "giữa": [frames // 2], "cuối": [frames - 1, frames - 5, frames - 30]}
            for label, candidates in checks.items():
                if not any(_read_frame_at(cap, i) for i in candidates if i >= 0):
                    probe.errors.append(f"Không giải mã được frame {label}")
        if probe.errors:
            probe.readable = False
        return probe
    finally:
        cap.release()
