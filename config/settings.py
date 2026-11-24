"""Cấu hình tập trung cho Video Intelligence.

Mọi đường dẫn được tính tương đối so với thư mục gốc repo (không hard-code
đường dẫn tuyệt đối của máy). Có thể ghi đè bằng biến môi trường ``VI_*``
hoặc file ``.env`` ở thư mục gốc (xem ``.env.example``).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

try:  # python-dotenv là tùy chọn; thiếu thì chỉ đọc biến môi trường hệ thống.
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", override=False)
except ImportError:  # pragma: no cover
    pass


def _path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    if not value:
        return default
    path = Path(value)
    return path if path.is_absolute() else (PROJECT_ROOT / path)


def _float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


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
    def validation_report(self) -> Path:
        return self.metadata / "validation_report.json"

    @property
    def report_md(self) -> Path:
        return self.metadata / "dataset_report.md"

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
            self.processed / "embeddings",
            self.annotations,
            self.splits,
        ]


@dataclass(frozen=True)
class Settings:
    dataset_dir: Path = field(default_factory=lambda: _path("VI_DATASET_DIR", PROJECT_ROOT / "dataset"))
    log_dir: Path = field(default_factory=lambda: _path("VI_LOG_DIR", PROJECT_ROOT / "logs"))
    log_level: str = field(default_factory=lambda: os.environ.get("VI_LOG_LEVEL", "INFO"))
    # Video ngắn hơn ngưỡng này bị cảnh báo "too_short" (đề tài: tối thiểu 10 phút).
    min_video_duration_sec: float = field(default_factory=lambda: _float("VI_MIN_VIDEO_DURATION_SEC", 600))
    supported_extensions: tuple[str, ...] = field(
        default_factory=lambda: _list("VI_VIDEO_EXTENSIONS", (".mp4", ".avi", ".mkv", ".mov"))
    )
    # Domain của dataset: chỉ video ca nhạc. Video có category ngoài danh sách bị cảnh báo
    # "out_of_domain" (lỗi khi --strict). Để rỗng (VI_ALLOWED_CATEGORIES=) để bỏ giới hạn.
    allowed_categories: tuple[str, ...] = field(
        default_factory=lambda: _list("VI_ALLOWED_CATEGORIES", ("music_show", "music_video", "concert"))
    )
    dataset_name: str = field(default_factory=lambda: os.environ.get("VI_DATASET_NAME", "VideoIntelligence-Music"))
    dataset_version: str = field(default_factory=lambda: os.environ.get("VI_DATASET_VERSION", "0.1.0"))
    # Tỷ lệ chia train/validation/test theo video (deterministic theo hash video_id).
    split_ratios: tuple[float, float, float] = (0.7, 0.15, 0.15)

    @property
    def paths(self) -> DatasetPaths:
        return DatasetPaths(self.dataset_dir)


def get_settings() -> Settings:
    return Settings()
