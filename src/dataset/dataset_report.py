"""Thống kê tổng quan Dataset -> console + ``metadata/dataset_report.md``.

    python -m src.dataset.dataset_report
"""
from __future__ import annotations

import argparse
import logging
from collections import Counter
from pathlib import Path

from config.settings import Settings, get_settings
from src.common.io_utils import atomic_write_text, read_csv, read_json
from src.common.logging_utils import setup_logging
from src.dataset.create_dataset import write_dataset_info

logger = logging.getLogger(__name__)

TARGET_VIDEOS = (10, 20)
TARGET_HOURS = (2, 5)


def _hms(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def _size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _f(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def build_report(settings: Settings | None = None) -> tuple[dict, str]:
    settings = settings or get_settings()
    paths = settings.paths
    videos = read_csv(paths.videos_csv)
    info = write_dataset_info(paths, settings, videos) if paths.metadata.exists() else {}
    validation = read_json(paths.validation_report, None)
    valid = [v for v in videos if v.get("processing_status") != "invalid"]
    durations = [_f(v.get("duration_seconds")) for v in valid]
    hours = sum(durations) / 3600
    short = [v for v in valid if _f(v.get("duration_seconds")) < settings.min_video_duration_sec]
    splits = {s: len(read_csv(paths.splits / f"{s}.csv")) for s in ("train", "validation", "test")}
    stats = {
        "total_videos": len(videos),
        "valid_videos": len(valid),
        "valid_hours": round(hours, 3),
        "total_size": info.get("total_size_bytes", 0),
        "status": dict(Counter(v.get("processing_status") or "?" for v in videos)),
        "formats": dict(Counter(Path(v["filename"]).suffix.lower() for v in videos if v.get("filename"))),
        "resolutions": dict(Counter(f"{v.get('width')}x{v.get('height')}" for v in valid)),
        "fps": dict(Counter(f"{_f(v.get('fps')):.2f}" for v in valid)),
        "licenses": dict(Counter(v.get("license") or "(chưa khai báo)" for v in videos)),
        "categories": dict(Counter(v.get("category") or "(chưa khai báo)" for v in videos)),
        "series": dict(Counter(v.get("series") for v in videos if v.get("series"))),
        "out_of_domain": sum(1 for v in videos if settings.allowed_categories
                             and (v.get("category") or "").lower() not in settings.allowed_categories),
        "too_short": len(short),
        "splits": splits,
        "min_minutes": round(min(durations) / 60, 2) if durations else None,
        "max_minutes": round(max(durations) / 60, 2) if durations else None,
        "avg_minutes": round(sum(durations) / len(durations) / 60, 2) if durations else None,
        "target_met": TARGET_VIDEOS[0] <= len(valid) and TARGET_HOURS[0] <= hours,
    }

    lines = [
        f"# Báo cáo Dataset – {info.get('name', settings.dataset_name)} v{info.get('version', settings.dataset_version)}",
        "",
        f"Cập nhật: {info.get('updated_at', '-')}",
        "",
        "## Tổng quan",
        "",
        "| Chỉ số | Giá trị | Mục tiêu |",
        "|---|---|---|",
        f"| Số video (tổng / hợp lệ) | {stats['total_videos']} / {stats['valid_videos']} | {TARGET_VIDEOS[0]}–{TARGET_VIDEOS[1]} |",
        f"| Tổng thời lượng hợp lệ | {_hms(sum(durations))} ({hours:.2f} giờ) | {TARGET_HOURS[0]}–{TARGET_HOURS[1]} giờ |",
        f"| Thời lượng min / TB / max | {stats['min_minutes']} / {stats['avg_minutes']} / {stats['max_minutes']} phút | ≥ {settings.min_video_duration_sec / 60:g} phút |",
        f"| Video quá ngắn | {stats['too_short']} | 0 |",
        f"| Tổng dung lượng | {_size(stats['total_size'])} | |",
        f"| Định dạng | {stats['formats'] or '-'} | |",
        f"| Độ phân giải | {stats['resolutions'] or '-'} | |",
        f"| FPS | {stats['fps'] or '-'} | |",
        f"| Trạng thái xử lý | {stats['status'] or '-'} | |",
        f"| Giấy phép | {stats['licenses'] or '-'} | đều phải được khai báo |",
        f"| Thể loại (category) | {stats['categories'] or '-'} | {', '.join(settings.allowed_categories) or 'không giới hạn'} |",
        f"| Chương trình (series) | {stats['series'] or '-'} | ưu tiên Anh Trai Vượt Ngàn Chông Gai |",
        f"| Video ngoài domain | {stats['out_of_domain']} | 0 |",
        f"| Split train / val / test | {splits['train']} / {splits['validation']} / {splits['test']} | |",
        f"| Đạt mục tiêu tối thiểu | {'CÓ' if stats['target_met'] else 'CHƯA'} | |",
        "",
    ]
    if validation:
        lines += [f"Validation gần nhất ({validation.get('validated_at')}, {validation.get('mode')}): "
                  f"{validation.get('errors')} lỗi, {validation.get('warnings')} cảnh báo.", ""]
    else:
        lines += ["Chưa chạy validation (`python -m src.dataset.validate_dataset`).", ""]
    if videos:
        lines += ["## Danh sách video", "",
                  "| video_id | file | thời lượng | FPS | độ phân giải | dung lượng | thể loại | chương trình | nguồn | giấy phép | trạng thái |",
                  "|---|---|---|---|---|---|---|---|---|---|---|"]
        for v in videos:
            lines.append(
                f"| {v.get('video_id')} | {v.get('filename')} | {_hms(_f(v.get('duration_seconds')))} | "
                f"{_f(v.get('fps')):.2f} | {v.get('width')}x{v.get('height')} | {_size(_f(v.get('file_size')))} | "
                f"{v.get('category') or '-'} | {v.get('series') or '-'} | "
                f"{v.get('source') or '-'} | {v.get('license') or '-'} | {v.get('processing_status')} |")
    else:
        lines += ["**Dataset chưa có video.** Xem `dataset/README.md` để đưa video vào.", ""]
    markdown = "\n".join(lines) + "\n"
    if paths.metadata.exists():
        atomic_write_text(paths.report_md, markdown)
    return stats, markdown


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Thống kê tổng quan Dataset")
    parser.add_argument("--dataset-dir", type=Path)
    args = parser.parse_args(argv)
    settings = Settings(dataset_dir=args.dataset_dir.resolve()) if args.dataset_dir else get_settings()
    setup_logging(settings.log_level, settings.log_dir)
    if not settings.paths.videos_csv.exists():
        logger.error("Chưa khởi tạo dataset tại %s – chạy: python -m src.dataset.create_dataset", settings.dataset_dir)
        return 1
    _, markdown = build_report(settings)
    print(markdown)
    logger.info("Đã ghi %s", settings.paths.report_md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
