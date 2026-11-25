"""CỔNG KIỂM SOÁT DUY NHẤT giữa Dataset và mọi bước xử lý phía sau.

Hợp đồng bắt buộc cho các module ở Giai đoạn 3–7 (frame extraction, YOLO, temporal segment,
Spark, Kafka, HDFS/Parquet, Streamlit, benchmark):

* Chỉ lấy danh sách video đầu vào từ ``accepted_videos()`` – không tự đọc ``raw/videos`` hay
  ``videos.csv``.
* Trước khi xử lý / phát sự kiện cho một video: ``assert_accepted(video_id)``.
* Khi đọc kết quả đã lưu (detections, segments, sự kiện Kafka): lọc bằng ``accepted_ids()`` /
  ``filter_records()`` (Spark: inner join với bảng accepted_ids). Nhờ vậy video bị loại sau này
  tự động biến mất khỏi truy vấn mà KHÔNG cần xóa dữ liệu cũ.
* Timestamp / segment phải nằm trong [0, duration_seconds]: ``check_time_range()``.

Cổng kiểm tra lại tại thời điểm gọi (không tin metadata cũ một cách mù quáng): trạng thái
``accepted``, file còn tồn tại, cùng dung lượng (tùy chọn: cùng sha256), MP4, thời lượng trong
khoảng cho phép. Video không qua được bị loại khỏi kết quả và được ghi log.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Mapping

from config.settings import Settings, get_settings
from src.common.io_utils import read_csv, sha256_file
from src.dataset.validation.metadata_validator import check_duration, check_format, positive

logger = logging.getLogger(__name__)


class NotAcceptedError(RuntimeError):
    """Video không ở trạng thái accepted (hoặc file đã thay đổi) – không được xử lý."""


@dataclass(frozen=True)
class AcceptedVideo:
    video_id: str
    filename: str
    path: Path
    title: str
    duration_seconds: float
    fps: float
    total_frames: int
    width: int
    height: int
    sha256: str


def _recheck(row: dict, settings: Settings, verify_hash: bool) -> str | None:
    """Trả về lý do không hợp lệ, hoặc None nếu video vẫn đạt."""
    if row.get("validation_status") != "accepted":
        return f"validation_status={row.get('validation_status') or '(trống)'}"
    if issue := check_format(row.get("filename", ""), settings):
        return issue.message
    if issue := check_duration(row.get("duration_seconds"), settings):
        return issue.message
    path = settings.paths.root / (row.get("filepath") or "")
    if not row.get("filepath") or not path.is_file():
        return "file không tồn tại"
    if not positive(row.get("file_size")) or path.stat().st_size != int(float(row["file_size"])):
        return "file đã thay đổi (dung lượng khác metadata)"
    if verify_hash and sha256_file(path) != row.get("sha256"):
        return "file đã thay đổi (sha256 khác metadata)"
    return None


def accepted_videos(settings: Settings | None = None, verify_hash: bool = False) -> list[AcceptedVideo]:
    """Danh sách video được phép xử lý. ``verify_hash=True`` băm lại file (chậm hơn, chắc chắn hơn)."""
    settings = settings or get_settings()
    result = []
    for row in read_csv(settings.paths.videos_csv):
        problem = _recheck(row, settings, verify_hash)
        if problem:
            if row.get("validation_status") == "accepted":
                logger.warning("Bỏ %s khỏi pipeline: %s – chạy lại scan/validate", row.get("video_id"), problem)
            continue
        result.append(AcceptedVideo(
            video_id=row["video_id"], filename=row["filename"], path=settings.paths.root / row["filepath"],
            title=row.get("title", ""), duration_seconds=float(row["duration_seconds"]),
            fps=float(row["fps"]), total_frames=int(float(row["total_frames"])),
            width=int(float(row["width"])), height=int(float(row["height"])), sha256=row.get("sha256", "")))
    return result


def accepted_ids(settings: Settings | None = None) -> set[str]:
    return {v.video_id for v in accepted_videos(settings)}


def assert_accepted(video_id: str, settings: Settings | None = None, verify_hash: bool = False) -> AcceptedVideo:
    for video in accepted_videos(settings, verify_hash=verify_hash):
        if video.video_id == video_id:
            return video
    raise NotAcceptedError(f"{video_id} không ở trạng thái accepted – không được xử lý / truy vấn")


def filter_records(records: Iterable[Mapping], allowed: set[str], key: str = "video_id") -> Iterator[Mapping]:
    """Lọc bản ghi (detection, segment, sự kiện) chỉ giữ video accepted."""
    for record in records:
        if record.get(key) in allowed:
            yield record


def check_time_range(video: AcceptedVideo, start: float, end: float | None = None,
                     tolerance: float = 0.5) -> None:
    """Timestamp / segment phải nằm trong thời lượng video (cho phép sai số làm tròn nhỏ)."""
    end = start if end is None else end
    if start < 0 or end < start or end > video.duration_seconds + tolerance:
        raise ValueError(f"{video.video_id}: khoảng [{start}, {end}] ngoài thời lượng "
                         f"{video.duration_seconds:.3f} s")
