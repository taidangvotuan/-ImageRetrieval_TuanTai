"""Fixture dùng chung.

Video tổng hợp ở đây CHỈ phục vụ kiểm thử logic (đọc, metadata, validation). Chúng được sinh
trong thư mục tạm của pytest, không bao giờ ghi vào ``dataset/`` hay ``archive/`` và không được
dùng để báo cáo kết quả của hệ thống. Nội dung "AI / hậu trường / lyrics" trong test được thể
hiện qua metadata nguồn (tiêu đề, mô tả), vì hệ thống quyết định dựa trên metadata + duyệt tay.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import Settings  # noqa: E402
from src.common.io_utils import read_csv  # noqa: E402

FULL_REVIEW = dict(content_type="official_music_video", ai_generated_status="confirmed",
                   behind_the_scenes_status="confirmed", lyrics_status="confirmed",
                   license_status="confirmed", source_status="confirmed",
                   content_safety_status="safe")   # người duyệt đã xem toàn video


def make_video(path: Path, seconds: float = 3.0, fps: int = 10, size=(64, 48), seed: int = 0) -> Path:
    import cv2

    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
    assert writer.isOpened(), "OpenCV không tạo được VideoWriter mp4v"
    rng = np.random.default_rng(seed)
    for _ in range(int(round(seconds * fps))):
        writer.write(rng.integers(0, 255, (size[1], size[0], 3), dtype=np.uint8))
    writer.release()
    return path


def make_settings(tmp_path: Path, **overrides) -> Settings:
    """Settings cô lập trong thư mục tạm, truyền ngưỡng tường minh (không phụ thuộc .env của máy)."""
    params = dict(dataset_dir=tmp_path / "dataset", log_dir=tmp_path / "logs", archive_dir=tmp_path / "archive",
                  min_video_duration_sec=2, max_video_duration_sec=420, min_long_side_px=1)
    params.update(overrides)
    return Settings(**params)


def by_filename(settings: Settings) -> dict[str, dict]:
    return {r["filename"]: r for r in read_csv(settings.paths.videos_csv)}


@pytest.fixture
def settings(tmp_path) -> Settings:
    # Ngưỡng thời lượng / độ phân giải nhỏ để video test vài giây, 64x48 được coi là đạt kỹ thuật.
    return make_settings(tmp_path)


@pytest.fixture
def mv_settings(tmp_path) -> Settings:
    """Ngưỡng thời lượng THẬT của đề tài (150–420 s); video test dùng 1 fps nên vẫn rất nhỏ."""
    return make_settings(tmp_path, min_video_duration_sec=150, max_video_duration_sec=420)


@pytest.fixture
def video_factory(settings):
    def factory(name: str = "a.mp4", **kwargs) -> Path:
        return make_video(settings.paths.raw_videos / name, **kwargs)
    return factory
