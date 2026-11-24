"""Khởi tạo cấu trúc Dataset (idempotent – chạy lại không ghi đè dữ liệu có sẵn).

Tạo: cây thư mục, README hướng dẫn, header CSV cho metadata/annotation/split,
``labels.csv`` (80 lớp COCO mà YOLO hỗ trợ) và ``dataset_info.json``.
KHÔNG tạo video giả hay dòng metadata giả.

    python -m src.dataset.create_dataset
    python -m src.dataset.create_dataset --dataset-dir path/khac
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path

from config.settings import DatasetPaths, Settings, get_settings
from src.common.io_utils import atomic_json, atomic_write_text, read_csv_header, write_csv
from src.common.logging_utils import setup_logging
from src.dataset.schema import (COCO_CLASSES, GROUND_TRUTH_COLUMNS, LABEL_COLUMNS, SOURCE_COLUMNS,
                                SPLIT_COLUMNS, VIDEO_COLUMNS)

logger = logging.getLogger(__name__)

README_TEMPLATE = """# Dataset – Video Intelligence (domain: video ca nhạc)

Thư mục này được tạo bởi `python -m src.dataset.create_dataset`. Không chỉnh sửa
CSV bằng tay – mọi file metadata được sinh lại bằng các công cụ trong `src/dataset/`.

## Yêu cầu video

| Tiêu chí | Giá trị |
|---|---|
| Domain | Chỉ video ca nhạc – `category` thuộc: {categories} |
| Ưu tiên | Show **Anh Trai Vượt Ngàn Chông Gai** (`music_show`); MV / concert khác vẫn được |
| Thời lượng tối thiểu / video | {min_minutes:g} phút (video ngắn hơn bị cảnh báo `too_short`) |
| Mục tiêu | 10–30 phút/video, 10–20 video, tổng 2–5 giờ |
| Định dạng hỗ trợ | {extensions} |
| Nguồn | Giấy phép rõ ràng (CC BY do chính chủ đăng, hoặc video tự cung cấp có khai báo) |

Không dùng video giả, không lặp một video ngắn để "làm to" dataset.
Video ngoài domain bị cảnh báo `out_of_domain` (lỗi khi `validate_dataset --strict`).

## Cách đưa video vào

**Cách 1 – Tải video YouTube giấy phép CC BY** (tự kiểm tra giấy phép, thời lượng, kênh):

```powershell
# Anh Trai Vượt Ngàn Chông Gai – chỉ nhận video từ kênh chính chủ YeaH1
python -m src.dataset.download_videos --preset atvncg --max-results 20 --dry-run
python -m src.dataset.download_videos --preset atvncg --url https://www.youtube.com/watch?v=XXXXXXXXXXX
# Concert / MV khác
python -m src.dataset.download_videos --preset concert --dry-run
python -m src.dataset.download_videos --category concert --url https://www.youtube.com/watch?v=XXXXXXXXXXX
```

**Cách 2 – Video tự cung cấp:** chép file vào `dataset/raw/videos/`, rồi khai báo nguồn,
giấy phép và thể loại (không cần sửa CSV):

```powershell
python -m src.dataset.scan_videos --register-source my_video.mp4 --source user --license "..." --category music_video --title "..."
```

**Gỡ video khỏi dataset:** `python -m src.dataset.scan_videos --remove video_00X`
(file được chuyển vào `raw/removed/`; thêm `--delete-file` để xóa hẳn).

Sau đó luôn chạy:

```powershell
python -m src.dataset.scan_videos        # quét, đo metadata, gán video_id
python -m src.dataset.validate_dataset   # kiểm tra lỗi / trùng / thời lượng / giấy phép
python -m src.dataset.dataset_report     # thống kê tổng quan
```

## Cấu trúc

| Thư mục / file | Nội dung |
|---|---|
| `raw/videos/` | Video gốc, **không chỉnh sửa**. Bị `.gitignore`. |
| `metadata/videos.csv` | Một dòng/video: video_id, đường dẫn, thời lượng, FPS, frame, độ phân giải, dung lượng, nguồn, giấy phép, trạng thái xử lý, sha256 |
| `metadata/sources.csv` | Nguồn gốc & giấy phép từng file (URL, tác giả, license) |
| `metadata/dataset_info.json` | Tên, phiên bản, tổng số video, tổng thời lượng, dung lượng, định dạng, ngày tạo |
| `metadata/validation_report.json` | Kết quả lần validate gần nhất |
| `processed/frames/` | Chỉ frame minh họa (không lưu toàn bộ frame) |
| `processed/detections/` | Detection events YOLO (Parquet) – Giai đoạn 4 |
| `processed/temporal_segments/` | Các khoảng thời gian xuất hiện (Parquet) – Giai đoạn 5 |
| `processed/embeddings/` | Dự phòng cho mở rộng |
| `annotations/labels.csv` | 80 lớp COCO mà YOLO pretrained hỗ trợ (kèm tên tiếng Việt) |
| `annotations/ground_truth.csv` | Khoảng thời gian gán nhãn tay để đánh giá |
| `splits/*.csv` | Chia train/validation/test theo video (deterministic) |

`processing_status`: `pending` → `validated` / `invalid` → `processing` → `detected` → `aggregated`.
"""


def _ensure_csv(path: Path, columns: list[str]) -> str:
    """Tạo CSV chỉ có header nếu chưa tồn tại; nếu đã có thì kiểm tra header."""
    if not path.exists():
        write_csv(path, columns, [])
        return "created"
    header = read_csv_header(path)
    missing = [c for c in columns if c not in header]
    if missing:
        logger.warning("%s thiếu cột %s – chạy scan_videos để chuẩn hóa", path.name, missing)
        return "header-mismatch"
    return "exists"


def write_dataset_info(paths: DatasetPaths, settings: Settings, videos: list[dict]) -> dict:
    from src.common.io_utils import read_json

    previous = read_json(paths.dataset_info, {}) or {}
    valid = [v for v in videos if v.get("processing_status") != "invalid"]

    def total(key, rows):
        return sum(float(r.get(key) or 0) for r in rows)

    info = {
        "name": settings.dataset_name,
        "version": settings.dataset_version,
        "created_at": previous.get("created_at") or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_videos": len(videos),
        "valid_videos": len(valid),
        "total_duration_seconds": round(total("duration_seconds", videos), 3),
        "valid_duration_seconds": round(total("duration_seconds", valid), 3),
        "total_size_bytes": int(total("file_size", videos)),
        "formats": sorted({Path(v["filename"]).suffix.lower() for v in videos if v.get("filename")}),
        "supported_formats": list(settings.supported_extensions),
        "min_video_duration_seconds": settings.min_video_duration_sec,
        "status_counts": {s: sum(1 for v in videos if v.get("processing_status") == s)
                          for s in sorted({v.get("processing_status", "") for v in videos})},
    }
    atomic_json(paths.dataset_info, info)
    return info


def create_dataset(settings: Settings | None = None) -> dict[str, str]:
    settings = settings or get_settings()
    paths = settings.paths
    for directory in paths.all_dirs():
        directory.mkdir(parents=True, exist_ok=True)
        keep = directory / ".gitkeep"
        if not any(directory.iterdir()):
            keep.touch()

    result = {
        "videos.csv": _ensure_csv(paths.videos_csv, VIDEO_COLUMNS),
        "sources.csv": _ensure_csv(paths.sources_csv, SOURCE_COLUMNS),
        "ground_truth.csv": _ensure_csv(paths.annotations / "ground_truth.csv", GROUND_TRUTH_COLUMNS),
    }
    for split in ("train", "validation", "test"):
        result[f"{split}.csv"] = _ensure_csv(paths.splits / f"{split}.csv", SPLIT_COLUMNS)

    # labels.csv là dữ liệu thật (danh sách lớp của mô hình) -> luôn sinh lại cho đồng bộ.
    write_csv(paths.annotations / "labels.csv", LABEL_COLUMNS,
              [{"class_id": i, "object_name": en, "name_vi": vi, "supported_by": "yolo-coco80"}
               for i, (en, vi) in enumerate(COCO_CLASSES)])
    result["labels.csv"] = "written"

    readme = paths.root / "README.md"
    if not readme.exists():
        atomic_write_text(readme, README_TEMPLATE.format(
            min_minutes=settings.min_video_duration_sec / 60,
            categories=", ".join(f"`{c}`" for c in settings.allowed_categories) or "không giới hạn",
            extensions=", ".join(settings.supported_extensions)))
        result["README.md"] = "created"
    else:
        result["README.md"] = "exists"

    from src.common.io_utils import read_csv
    write_dataset_info(paths, settings, read_csv(paths.videos_csv))
    result["dataset_info.json"] = "written"
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Khởi tạo cấu trúc Dataset Video Intelligence")
    parser.add_argument("--dataset-dir", type=Path, help="Ghi đè VI_DATASET_DIR")
    args = parser.parse_args(argv)
    settings = get_settings()
    if args.dataset_dir:
        settings = Settings(dataset_dir=args.dataset_dir.resolve())
    setup_logging(settings.log_level, settings.log_dir)
    result = create_dataset(settings)
    logger.info("Dataset tại %s", settings.dataset_dir)
    for name, status in result.items():
        logger.info("  %-20s %s", name, status)
    n_videos = sum(1 for p in settings.paths.raw_videos.iterdir()
                   if p.suffix.lower() in settings.supported_extensions)
    if n_videos == 0:
        logger.info("Chưa có video. Xem hướng dẫn đưa video vào: %s", settings.paths.root / "README.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
