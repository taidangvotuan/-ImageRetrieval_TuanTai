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
from src.dataset.schema import (COCO_CLASSES, GROUND_TRUTH_COLUMNS, LABEL_COLUMNS, REVIEW_COLUMNS,
                                SCHEMA_VERSION, SOURCE_COLUMNS, SPLIT_COLUMNS, TARGET_CLASSES,
                                VALIDATION_STATUSES, VIDEO_COLUMNS)

logger = logging.getLogger(__name__)

README_TEMPLATE = """# Dataset – Video Intelligence (Music Video chính thức)

Thư mục này được tạo bởi `python -m src.dataset.create_dataset`. Không sửa CSV bằng tay:
metadata được sinh lại bằng các công cụ trong `src/dataset/`. Tiêu chí nằm trong **một file
duy nhất** `config/dataset_criteria.json` – xem `docs/DATASET_CRITERIA.md`.

## Tiêu chí

| Tiêu chí | Yêu cầu |
|---|---|
| Loại | Music Video **chính thức**, hoàn chỉnh, quay thực tế (không phải AI-generated) |
| Định dạng | Chỉ MP4 – định dạng khác bị `rejected: not_mp4`, không tự chuyển đổi |
| Thời lượng | Luật cứng {min_sec:g}–{max_sec:g} s; khoảng mục tiêu {target_min:g}–{target_max:g} s (3–5 phút, chỉ để thống kê) |
| Độ phân giải | ≥ 720p (cạnh dài ≥ {min_long_side} px) – thấp hơn bị `rejected: low_resolution` |
| Loại trừ | AI-generated, hậu trường, lyric video, teaser/trailer, reaction/review, tổng hợp/medley, sân khấu/live, audio/visualizer, fan made/reupload, bản cover, trùng lặp, file lỗi |
| Nguồn | Hợp pháp, có quyền sử dụng – **phải được người duyệt xác minh** |
| An toàn nội dung | Không tục tĩu/xúc phạm, bạo lực/máu me, kích động bạo lực, khiêu dâm, giới hạn tuổi 18+ – xem `docs/CONTENT_MODERATION.md` |
| Quy mô | Mốc {milestones} MV accepted (30 chính thức, 50 mở rộng) |

Không dùng video giả, không cắt/lặp video để đạt thời lượng, không tạo metadata giả.

## Trạng thái xác minh

| validation_status | Ý nghĩa |
|---|---|
| `accepted` | Không vi phạm tiêu chí nào VÀ người duyệt đã xác nhận: MV chính thức, không AI, không hậu trường, không lyrics, giấy phép, nguồn gốc, an toàn nội dung (đã xem toàn video). **Chỉ trạng thái này được đưa vào pipeline.** |
| `rejected` | Vi phạm ít nhất một tiêu chí – lý do trong `rejection_reason` |
| `pending_review` | Chưa đủ căn cứ – mục còn thiếu trong `verification_notes` |

Luật tự động chỉ có thể **loại** (từ khóa rõ nghĩa trong tiêu đề, lỗi kỹ thuật). Không có video
nào tự trở thành `accepted`: AI-generated, nguồn gốc và giấy phép phải do người xem và xác nhận.

## Quy trình đưa MV vào

```powershell
# 1) Tìm MV CC BY (chỉ đọc metadata, KHÔNG tải) -> metadata/download_candidates.csv
python -m src.dataset.download_videos --preset music_video --max-results 30 --dry-run
# 2) Chọn URL có screen_result=pass hoặc review, chép vào approved_urls.txt rồi tải
python -m src.dataset.download_videos --url-file approved_urls.txt
#    (hoặc MV tự cung cấp: chép vào raw/videos/ rồi khai báo nguồn)
python -m src.dataset.scan_videos --register-source my_mv.mp4 --source user --license "..." `
    --title "..." --author "..." --source-url "..." --description "..."
# 3) Quét + kiểm tra -> accepted / rejected / pending_review
python -m src.dataset.scan_videos
python -m src.dataset.validate_dataset
# 4) Duyệt tay các video pending_review (xem video, kiểm tra kênh, giấy phép)
python -m src.dataset.review_video --export --reviewer "Tên người duyệt"
#    điền validation/review_sheet.csv rồi:
python -m src.dataset.review_video --import validation/review_sheet.csv --reviewer "Tên người duyệt"
# 5) Báo cáo
python -m src.dataset.dataset_report
```

**Gỡ video:** `python -m src.dataset.scan_videos --remove MV_00X --reason "..."` – file chuyển vào
`archive/videos/` (ngoài dataset/), nguồn và giấy phép giữ trong `archive/archive_manifest.csv`.
Video trùng lặp **không bao giờ bị tự động xóa**: bản sau bị `rejected: duplicate_*` và được liệt
kê trong báo cáo để xử lý thủ công.

## Cấu trúc

| Thư mục / file | Nội dung |
|---|---|
| `raw/videos/` | MV gốc, giữ tên file gốc, **không chỉnh sửa**. Bị `.gitignore`. Không di chuyển theo trạng thái |
| `metadata/videos.csv` | Một dòng/MV: kỹ thuật + nguồn + kết quả xác minh (schema v{schema_version}) |
| `metadata/sources.csv` | Nguồn khai báo (URL, tác giả, kênh, giấy phép) – khai báo chưa phải xác minh |
| `metadata/source_info/*.json` | Metadata gốc từ nguồn (tiêu đề, mô tả, tags, kênh) – căn cứ xác minh |
| `metadata/reviews.csv` | Quyết định duyệt tay (chỉ ghi thêm, gắn sha256, có người duyệt + căn cứ) |
| `metadata/dataset_info.json` | Tổng hợp: số video theo trạng thái, thời lượng accepted, schema_version |
| `metadata/download_candidates.csv` | Kết quả `--dry-run` gần nhất (không commit) |
| `metadata/backups/` | Bản sao lưu trước mỗi lần migration (không commit) |
| `validation/accepted.csv`, `rejected.csv`, `pending_review.csv` | Danh sách theo trạng thái (sinh từ metadata) |
| `validation/review_sheet.csv` | Phiếu duyệt xuất bởi `review_video --export` |
| `reports/dataset_report.{{md,json,csv}}` | Báo cáo Dataset |
| `reports/validation_report.json` | Chi tiết lần kiểm tra gần nhất, trùng lặp cần xử lý |
| `moderation/<video_id>/*.json` | Kết quả kiểm duyệt nội dung theo bước (không commit – có thể chứa lời bài hát) |
| `processed/frames/`, `detections/`, `temporal_segments/` | Kết quả xử lý – CHỈ cho video accepted (GĐ3–5) |
| `annotations/labels.csv` | 80 lớp COCO; `is_target=yes` là 10 lớp mục tiêu |
| `annotations/ground_truth.csv` | Nhãn gán tay để đánh giá – xem `annotations/annotation_guide.md` |
| `splits/*.csv` | Chia theo video, **chỉ video accepted** |

`processing_status` (độc lập với xác minh): `not_started` → `processing` → `detected` → `aggregated` / `failed`.
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
    """Thống kê tóm tắt. Thời lượng / số MV "hợp lệ" CHỈ tính video accepted."""
    from src.common.io_utils import read_json

    previous = read_json(paths.dataset_info, {}) or {}
    accepted = [v for v in videos if v.get("validation_status") == "accepted"]

    def total(key, rows):
        return sum(float(r.get(key) or 0) for r in rows)

    info = {
        "name": settings.dataset_name,
        "version": settings.dataset_version,
        "schema_version": SCHEMA_VERSION,
        "created_at": previous.get("created_at") or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_videos": len(videos),
        "accepted_videos": len(accepted),
        "validation_counts": {s: sum(1 for v in videos if v.get("validation_status") == s)
                              for s in VALIDATION_STATUSES},
        "accepted_duration_seconds": round(total("duration_seconds", accepted), 3),
        "total_duration_seconds": round(total("duration_seconds", videos), 3),
        "total_size_bytes": int(total("file_size", videos)),
        "formats": sorted({Path(v["filename"]).suffix.lower() for v in videos if v.get("filename")}),
        "accepted_formats": list(settings.accepted_extensions),
        "min_video_duration_seconds": settings.min_video_duration_sec,
        "max_video_duration_seconds": settings.max_video_duration_sec,
        "target_duration_seconds": [settings.target_min_duration_sec, settings.target_max_duration_sec],
        "min_long_side_px": settings.min_long_side_px,
        "target_milestones": list(settings.target_milestones),
        "processing_counts": {s: sum(1 for v in videos if v.get("processing_status") == s)
                              for s in sorted({v.get("processing_status", "") for v in videos})},
    }
    for key in ("migrated_at", "migrated_from_backup"):
        if key in previous:
            info[key] = previous[key]
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

    from src.dataset.migrate_metadata import ensure_schema

    migration = ensure_schema(settings)  # metadata cũ -> nâng cấp (có sao lưu) trước khi kiểm tra header
    result = {
        "videos.csv": _ensure_csv(paths.videos_csv, VIDEO_COLUMNS),
        "sources.csv": _ensure_csv(paths.sources_csv, SOURCE_COLUMNS),
        "ground_truth.csv": _ensure_csv(paths.annotations / "ground_truth.csv", GROUND_TRUTH_COLUMNS),
        "reviews.csv": _ensure_csv(paths.reviews_csv, REVIEW_COLUMNS),
    }
    if migration.get("migrated"):
        result["migration"] = f"v{migration['from_version']} -> v{migration['to_version']}, sao lưu {migration['backup']}"
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
            target_min=settings.target_min_duration_sec, target_max=settings.target_max_duration_sec,
            min_long_side=settings.min_long_side_px,
            milestones=" → ".join(str(m) for m in settings.target_milestones),
            schema_version=SCHEMA_VERSION,
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
