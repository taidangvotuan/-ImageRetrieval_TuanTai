"""Fixture dùng chung.

Video tổng hợp ở đây CHỈ phục vụ kiểm thử logic (đọc, metadata, validation).
Chúng được sinh trong thư mục tạm của pytest, không bao giờ ghi vào ``dataset/``
và không được dùng để báo cáo kết quả của hệ thống.
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


def make_video(path: Path, seconds: float = 3.0, fps: int = 10, size=(64, 48), seed: int = 0) -> Path:
    import cv2

    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
    assert writer.isOpened(), "OpenCV không tạo được VideoWriter mp4v"
    rng = np.random.default_rng(seed)
    for _ in range(int(seconds * fps)):
        writer.write(rng.integers(0, 255, (size[1], size[0], 3), dtype=np.uint8))
    writer.release()
    return path


@pytest.fixture
def settings(tmp_path) -> Settings:
    # Ngưỡng thời lượng / độ phân giải nhỏ để video test vài giây, 64x48 được coi là hợp lệ.
    # archive_dir trỏ vào thư mục tạm: test không bao giờ đụng tới archive/ thật của repo.
    return Settings(dataset_dir=tmp_path / "dataset", log_dir=tmp_path / "logs", archive_dir=tmp_path / "archive",
                    min_video_duration_sec=2, max_video_duration_sec=330, min_long_side_px=1)


@pytest.fixture
def video_factory(settings):
    def factory(name: str = "a.mp4", **kwargs) -> Path:
        return make_video(settings.paths.raw_videos / name, **kwargs)
    return factory
