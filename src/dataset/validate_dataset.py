"""Kiểm tra Dataset theo tiêu chí tập trung (``config/dataset_criteria.json``).

Mỗi video được kết luận ``accepted`` / ``rejected`` / ``pending_review`` (xem
``src/dataset/validation/dataset_validator.py``). Kết quả ghi vào:

* ``metadata/videos.csv`` – validation_status, rejection_reason, verification_notes, …
* ``validation/{accepted,rejected,pending_review}.csv`` – danh sách theo trạng thái
* ``reports/validation_report.json`` – chi tiết từng video, trùng lặp cần xử lý, lỗi cấp dataset
* ``splits/*.csv`` – chỉ gồm video accepted

Exit code 1 khi có lỗi cấp dataset (thiếu cột, trùng video_id, file chưa đăng ký); video bị
rejected / pending_review là kết quả bình thường, không làm lệnh thất bại.

    python -m src.dataset.validate_dataset           # giải mã thử frame đầu/giữa/cuối
    python -m src.dataset.validate_dataset --quick   # bỏ bước giải mã thử
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from config.settings import Settings, get_settings
from src.common.logging_utils import ensure_utf8_stdio, setup_logging
from src.dataset.validation.dataset_validator import validate as _validate

logger = logging.getLogger(__name__)


def validate(settings: Settings | None = None, quick: bool = False, update_status: bool = True) -> dict:
    """Giữ tên hàm cũ để tương thích; logic nằm trong ``validation.dataset_validator``."""
    return _validate(settings, deep=not quick, write=update_status)


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Kiểm tra Dataset: accepted / rejected / pending_review")
    parser.add_argument("--dataset-dir", type=Path)
    parser.add_argument("--quick", action="store_true", help="Không giải mã thử frame")
    parser.add_argument("--no-update", action="store_true", help="Chỉ kiểm tra, không ghi metadata/danh sách")
    args = parser.parse_args(argv)
    settings = Settings(dataset_dir=args.dataset_dir.resolve()) if args.dataset_dir else get_settings()
    setup_logging(settings.log_level, settings.log_dir)
    report = validate(settings, quick=args.quick, update_status=not args.no_update)
    for issue in report["dataset_issues"]:
        logger.error("[%s] %s: %s", issue["code"], issue["filename"] or issue["video_id"] or "-", issue["message"])
    for dup in report["duplicates"]:
        logger.warning("[%s] %s (%s) – trùng với %s: cần xử lý thủ công", dup["code"], dup["video_id"],
                       dup["filename"], dup["duplicate_of"])
    for video in report["videos"]:
        log = logger.info if video["validation_status"] == "accepted" else logger.warning
        log("%s %-15s %s %s", video["video_id"], video["validation_status"],
            ";".join(video["rejection_reason"]) or "-", video["filename"])
    logger.info("Kết quả: %d video – %d accepted, %d rejected, %d pending_review -> %s",
                report["total_videos"], report["accepted"], report["rejected"], report["pending_review"],
                settings.paths.validation_report)
    if report["total_videos"] == 0 and report["ok"]:
        logger.info("Dataset chưa có video (hợp lệ nhưng rỗng).")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
