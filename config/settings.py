"""Cấu hình tập trung cho Video Intelligence.

Mọi đường dẫn được tính tương đối so với thư mục gốc repo (không hard-code
đường dẫn tuyệt đối của máy). Có thể ghi đè bằng biến môi trường ``VI_*``
hoặc file ``.env`` ở thư mục gốc (xem ``.env.example``).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CRITERIA_FILE = PROJECT_ROOT / "config" / "dataset_criteria.json"

try:  # python-dotenv là tùy chọn; thiếu thì chỉ đọc biến môi trường hệ thống.
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", override=False)
except ImportError:  # pragma: no cover
    pass


@lru_cache(maxsize=4)
def load_criteria(path: str | None = None) -> dict:
    """Đọc ``config/dataset_criteria.json`` – nguồn tiêu chí Dataset duy nhất."""
    file = Path(path) if path else Path(os.environ.get("VI_CRITERIA_FILE") or CRITERIA_FILE)
    if not file.is_absolute():
        file = PROJECT_ROOT / file
    return json.loads(file.read_text(encoding="utf-8"))


def _criteria(*keys):
    value = load_criteria()
    for key in keys:
        value = value[key]
    return value


def _path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    if not value:
        return default
    path = Path(value)
    return path if path.is_absolute() else (PROJECT_ROOT / path)


def _float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _ints(name: str, default: tuple[int, ...]) -> tuple[int, ...]:
    value = os.environ.get(name)
    if not value:
        return default
    return tuple(int(x) for x in value.split(",") if x.strip())


def _list(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = os.environ.get(name)
    if value is None:
        return default
    return tuple(x.strip().lower() for x in value.split(",") if x.strip())


@dataclass(frozen=True)
class DatasetPaths:
    """Bố cục thư mục Dataset. Tạo từ một thư mục gốc để dễ test."""

    root: Path

    @property
    def raw_videos(self) -> Path:
        return self.root / "raw" / "videos"

    @property
    def metadata(self) -> Path:
        return self.root / "metadata"

    @property
    def videos_csv(self) -> Path:
        return self.metadata / "videos.csv"

    @property
    def sources_csv(self) -> Path:
        return self.metadata / "sources.csv"

    @property
    def dataset_info(self) -> Path:
        return self.metadata / "dataset_info.json"

    @property
    def source_info(self) -> Path:
        """Metadata gốc từ nguồn (tiêu đề, mô tả, tags, kênh…): căn cứ xác minh, 1 JSON/video."""
        return self.metadata / "source_info"

    @property
    def reviews_csv(self) -> Path:
        """Quyết định duyệt tay (chỉ ghi thêm, gắn với sha256 của file)."""
        return self.metadata / "reviews.csv"

    @property
    def moderation(self) -> Path:
        """Kết quả kiểm duyệt nội dung theo video (KHÔNG commit – transcript/OCR có thể chứa lời bài hát)."""
        return self.root / "moderation"

    @property
    def backups(self) -> Path:
        return self.metadata / "backups"

    @property
    def validation(self) -> Path:
        """Danh sách accepted / rejected / pending_review (CSV sinh từ metadata, không chứa video)."""
        return self.root / "validation"

    @property
    def reports(self) -> Path:
        return self.root / "reports"

    @property
    def validation_report(self) -> Path:
        return self.reports / "validation_report.json"

    @property
    def report_md(self) -> Path:
        return self.reports / "dataset_report.md"

    @property
    def report_json(self) -> Path:
        return self.reports / "dataset_report.json"

    @property
    def report_csv(self) -> Path:
        return self.reports / "dataset_report.csv"

    @property
    def processed(self) -> Path:
        return self.root / "processed"

    @property
    def annotations(self) -> Path:
        return self.root / "annotations"

    @property
    def splits(self) -> Path:
        return self.root / "splits"

    def all_dirs(self) -> list[Path]:
        return [
            self.raw_videos,
            self.metadata,
            self.processed / "frames",
            self.processed / "detections",
            self.processed / "temporal_segments",
            self.annotations,
            self.splits,
            self.validation,
            self.reports,
            self.source_info,
        ]


@dataclass(frozen=True)
class Settings:
    dataset_dir: Path = field(default_factory=lambda: _path("VI_DATASET_DIR", PROJECT_ROOT / "dataset"))
    log_dir: Path = field(default_factory=lambda: _path("VI_LOG_DIR", PROJECT_ROOT / "logs"))
    log_level: str = field(default_factory=lambda: os.environ.get("VI_LOG_LEVEL", "INFO"))
    # Tiêu chí lấy từ config/dataset_criteria.json; biến môi trường VI_* ghi đè được.
    # Luật cứng 150–420 s (ngoài khoảng -> rejected); 180–300 s chỉ là khoảng mục tiêu để thống kê.
    min_video_duration_sec: float = field(
        default_factory=lambda: _float("VI_MIN_VIDEO_DURATION_SEC", _criteria("duration", "min_seconds")))
    max_video_duration_sec: float = field(
        default_factory=lambda: _float("VI_MAX_VIDEO_DURATION_SEC", _criteria("duration", "max_seconds")))
    target_min_duration_sec: float = field(default_factory=lambda: _criteria("duration", "target_min_seconds"))
    target_max_duration_sec: float = field(default_factory=lambda: _criteria("duration", "target_max_seconds"))
    # 720p trở lên: cạnh dài >= 1280 px (MV khung điện ảnh 1280x536 vẫn là 720p). Thấp hơn -> rejected.
    min_long_side_px: int = field(
        default_factory=lambda: _int("VI_MIN_LONG_SIDE_PX", _criteria("resolution", "min_long_side_px")))
    # Chỉ MP4 được chấp nhận; các định dạng khác vẫn được quét để báo rejected: not_mp4.
    accepted_extensions: tuple[str, ...] = field(
        default_factory=lambda: tuple(_criteria("format", "accepted_extensions")))
    supported_extensions: tuple[str, ...] = field(
        default_factory=lambda: _list("VI_VIDEO_EXTENSIONS", tuple(_criteria("format", "scanned_extensions")))
    )
    # Tiền tố video_id: MV_001, MV_002 ... (gán một lần, không đổi).
    video_id_prefix: str = field(default_factory=lambda: os.environ.get("VI_VIDEO_ID_PREFIX", "MV_"))
    # Các mốc quy mô Dataset theo giai đoạn (5 kiểm tra pipeline, 10, 30 chính, 50 mở rộng).
    target_milestones: tuple[int, ...] = field(
        default_factory=lambda: _ints("VI_TARGET_MILESTONES", (5, 10, 30, 50))
    )
    # Lexicon kiểm duyệt nội dung (nơi DUY NHẤT chứa từ nhạy cảm), một CSV / ngôn ngữ.
    lexicon_dir: Path = field(default_factory=lambda: _path(
        "VI_LEXICON_DIR", PROJECT_ROOT / _criteria("content_safety", "lexicon_dir")))
    # Video gỡ khỏi dataset được chuyển vào đây (NGOÀI dataset/, bị .gitignore).
    archive_dir: Path = field(default_factory=lambda: _path("VI_ARCHIVE_DIR", PROJECT_ROOT / "archive"))
    dataset_name: str = field(default_factory=lambda: os.environ.get("VI_DATASET_NAME", "VideoIntelligence-MV"))
    dataset_version: str = field(default_factory=lambda: os.environ.get("VI_DATASET_VERSION", "0.3.0"))
    # Tỷ lệ chia train/validation/test theo video (deterministic theo hash video_id).
    split_ratios: tuple[float, float, float] = (0.7, 0.15, 0.15)

    @property
    def paths(self) -> DatasetPaths:
        return DatasetPaths(self.dataset_dir)

    @property
    def criteria(self) -> dict:
        return load_criteria()


def get_settings() -> Settings:
    return Settings()
