"""Khởi tạo cấu trúc Dataset (idempotent – chạy lại không ghi đè dữ liệu có sẵn).

Tạo: cây thư mục, README hướng dẫn, header CSV cho metadata/annotation/split,
``labels.csv`` (80 lớp COCO mà YOLO hỗ trợ, đánh dấu 10 lớp mục tiêu),
``annotations/annotation_guide.md`` và ``dataset_info.json``.
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
from src.common.logging_utils import ensure_utf8_stdio, setup_logging
from src.dataset.schema import (COCO_CLASSES, GROUND_TRUTH_COLUMNS, LABEL_COLUMNS, SOURCE_COLUMNS,
                                SPLIT_COLUMNS, TARGET_CLASSES, VIDEO_COLUMNS)

logger = logging.getLogger(__name__)

README_TEMPLATE = """# Dataset – Video Intelligence (domain: Music Video)

Thư mục này được tạo bởi `python -m src.dataset.create_dataset`. Không sửa CSV bằng tay:
mọi file metadata được sinh lại bằng các công cụ trong `src/dataset/`.

## Tiêu chí

| Tiêu chí | Yêu cầu |
|---|---|
| Loại dữ liệu | Chỉ Music Video (`category` thuộc: {categories}) |
| Định dạng | MP4 (hỗ trợ đọc: {extensions}) |
| Thời lượng / video | 3–5 phút; công cụ chấp nhận {min_sec:g}–{max_sec:g} s, ngoài khoảng là `too_short` / `too_long` |
| Độ phân giải | 720p hoặc 1080p (cạnh dài ≥ {min_long_side} px), thấp hơn là `low_resolution` |
| Quy mô | Mốc {milestones} MV (5 kiểm tra pipeline, 10 nhiều video, 30 Dataset chính, 50 mở rộng) |
| Lấy mẫu frame | Mặc định 1 frame/giây, thử nghiệm thêm 2 frame/giây |
| Nguồn | Giấy phép rõ ràng: CC BY do chính chủ đăng, nguồn mở, hoặc video tự cung cấp có khai báo |

Không dùng video giả, không lặp video ngắn, không tạo metadata giả. Số lượng MV trong báo cáo
luôn là số thật đếm từ `metadata/videos.csv`.

## Cách đưa MV vào

**Cách 1: tìm MV YouTube giấy phép CC BY, duyệt rồi mới tải.**

```powershell
# 1) Chỉ tìm, không tải -> ghi metadata/download_candidates.csv
python -m src.dataset.download_videos --preset music_video --max-results 30 --dry-run
# 2) Mở download_candidates.csv, kiểm tra từng dòng accepted=yes:
#    kênh có phải của nghệ sĩ / hãng không? có phải MV thật (không phải lyric, reup) không?
#    Chép URL được duyệt vào một file text, mỗi dòng một URL.
# 3) Tải các URL đã duyệt
python -m src.dataset.download_videos --url-file approved_urls.txt
```

Giấy phép CC BY chỉ có giá trị khi người đăng sở hữu nội dung. Công cụ không tự xác minh được
điều này, nên bước 2 là bắt buộc. Bài hát còn có quyền tác giả âm nhạc riêng: chỉ dùng cho phân
tích học thuật, **không phân phối lại file video**.

**Cách 2: video tự cung cấp hoặc được cấp phép.** Chép file vào `dataset/raw/videos/`, rồi khai báo:

```powershell
python -m src.dataset.scan_videos --register-source my_mv.mp4 --source user `
    --license "..." --category music_video --title "..." --author "..." --source-url "..."
```

**Gỡ video:** `python -m src.dataset.scan_videos --remove MV_00X --reason "..."`. File được chuyển
vào `archive/videos/` (ngoài `dataset/`), nguồn và giấy phép được ghi lại trong
`archive/archive_manifest.csv`. Thêm `--delete-file` để xóa hẳn.

Sau mỗi lần thêm hoặc gỡ, chạy:

```powershell
python -m src.dataset.scan_videos        # đo metadata, sha256, gán video_id MV_xxx, chia split
python -m src.dataset.validate_dataset   # lỗi / trùng / thời lượng / độ phân giải / giấy phép
python -m src.dataset.dataset_report     # thống kê + tiến độ theo mốc
```

## Cấu trúc

| Thư mục / file | Nội dung |
|---|---|
| `raw/videos/` | MV gốc, giữ tên file gốc (vd. `yt_<id>.mp4`), **không chỉnh sửa**. Bị `.gitignore` |
| `metadata/videos.csv` | Một dòng/MV: video_id, filename, filepath, title, duration_seconds, fps, total_frames, width, height, file_size, source, license, processing_status, sha256… |
| `metadata/sources.csv` | Nguồn gốc và giấy phép từng file (URL, tác giả, kênh, license) |
| `metadata/dataset_info.json` | Tổng số MV, tổng thời lượng, dung lượng, trạng thái |
| `metadata/validation_report.json` | Kết quả lần validate gần nhất |
| `metadata/download_candidates.csv` | Danh sách ứng viên của lần `--dry-run` gần nhất, chờ duyệt |
| `processed/frames/` | Chỉ frame minh họa cho từng khoảng xuất hiện (không lưu toàn bộ frame) |
| `processed/detections/` | Detection YOLO (Parquet) – Giai đoạn 4 |
| `processed/temporal_segments/` | Khoảng thời gian xuất hiện (Parquet) – Giai đoạn 5 |
| `annotations/labels.csv` | 80 lớp COCO mà YOLO pretrained hỗ trợ; `is_target=yes` là 10 lớp mục tiêu |
| `annotations/ground_truth.csv` | Khoảng xuất hiện **gán nhãn tay** để đánh giá – xem `annotations/annotation_guide.md` |
| `splits/*.csv` | Chia theo video. Đề tài không train mô hình: `validation` để chỉnh `gap_tolerance`/`confidence`, `test` để gán nhãn và đánh giá |

`processing_status`: `pending` → `validated` / `invalid` → `processing` → `detected` → `aggregated`.
"""

ANNOTATION_GUIDE = """# Hướng dẫn gán nhãn ground truth

Mục đích: đánh giá precision / recall / F1 và sai số thời gian của các khoảng xuất hiện mà hệ
thống tìm được. **Không bao giờ** sinh nhãn từ kết quả YOLO rồi dùng chính nhãn đó để đánh giá YOLO.

## Phạm vi

- Gán nhãn các MV thuộc `splits/test.csv` (bắt đầu với 5 MV).
- Chỉ gán các lớp đã chọn trước khi xem kết quả hệ thống (gợi ý: `person`, `car`, `chair`,
  `bottle`, `motorcycle`). Ghi danh sách lớp vào cột `notes` của dòng đầu tiên mỗi MV.

## Quy tắc

1. Xem MV bằng trình phát cho phép tua từng giây (VLC: phím `E` để tiến từng frame).
2. Một **lần xuất hiện** là khoảng liên tục mà đối tượng nhìn thấy được, dù chỉ một phần. Nếu
   đối tượng biến mất dưới 1 giây (chớp cắt cảnh) thì vẫn coi là liên tục.
3. Ghi `start_seconds` / `end_seconds` với độ chính xác 0,5 s.
4. Đối tượng quá nhỏ (dưới khoảng 2% chiều cao khung hình) hoặc bị mờ không nhận ra: không gán.
   Ghi chú lại nếu phân vân.
5. Mỗi người gán ghi tên vào cột `annotator`. Nếu có 2 người, người thứ hai kiểm tra lại ít nhất
   1 MV để ước lượng mức đồng thuận.

## Định dạng `ground_truth.csv`

| video_id | object_name | start_seconds | end_seconds | annotator | notes |
|---|---|---|---|---|---|

`object_name` phải đúng tên lớp trong `labels.csv`.

## Chỉ số sẽ tính (Giai đoạn 8)

- Mức giây: precision / recall / F1 trên lưới 1 giây.
- Mức khoảng: ghép khoảng dự đoán với nhãn theo tIoU ≥ 0,5, tính precision / recall / F1 và
  sai số |Δstart|, |Δend|. Sai số này luôn bị chặn dưới bởi chu kỳ lấy mẫu (1 s hoặc 0,5 s).
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
        "max_video_duration_seconds": settings.max_video_duration_sec,
        "target_milestones": list(settings.target_milestones),
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
              [{"class_id": i, "object_name": en, "name_vi": vi, "supported_by": "yolo-coco80",
                "is_target": "yes" if en in TARGET_CLASSES else "no"}
               for i, (en, vi) in enumerate(COCO_CLASSES)])
    result["labels.csv"] = "written"

    readme = paths.root / "README.md"
    if not readme.exists():
        atomic_write_text(readme, README_TEMPLATE.format(
            min_sec=settings.min_video_duration_sec, max_sec=settings.max_video_duration_sec,
            min_long_side=settings.min_long_side_px,
            milestones=" → ".join(str(m) for m in settings.target_milestones),
            categories=", ".join(f"`{c}`" for c in settings.allowed_categories) or "không giới hạn",
            extensions=", ".join(settings.supported_extensions)))
        result["README.md"] = "created"
    else:
        result["README.md"] = "exists"

    guide = paths.annotations / "annotation_guide.md"
    if not guide.exists():
        atomic_write_text(guide, ANNOTATION_GUIDE)
        result["annotation_guide.md"] = "created"
    else:
        result["annotation_guide.md"] = "exists"

    from src.common.io_utils import read_csv
    write_dataset_info(paths, settings, read_csv(paths.videos_csv))
    result["dataset_info.json"] = "written"
    return result


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdio()
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
