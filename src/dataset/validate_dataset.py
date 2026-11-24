"""Kiểm tra tính hợp lệ của Dataset.

Lỗi (error) – video bị đánh dấu ``invalid``:
  missing_metadata, missing_file (sai đường dẫn), metadata_stale, unreadable,
  corrupt, duplicate_content, duplicate_id, unregistered_file, unsupported_format
Cảnh báo (warning) – vẫn dùng được, ``--strict`` coi như lỗi:
  too_short (< VI_MIN_VIDEO_DURATION_SEC), missing_license, probe_warning,
  out_of_domain (category không thuộc VI_ALLOWED_CATEGORIES – mặc định chỉ video ca nhạc)

Kết quả ghi vào ``metadata/validation_report.json``; exit code 1 nếu có lỗi.

    python -m src.dataset.validate_dataset           # giải mã thử frame đầu/giữa/cuối
    python -m src.dataset.validate_dataset --quick   # chỉ kiểm tra metadata + tồn tại file
    python -m src.dataset.validate_dataset --strict
"""
from __future__ import annotations

import argparse
import logging
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from config.settings import Settings, get_settings
from src.common.io_utils import atomic_json, read_csv, read_csv_header, write_csv
from src.common.logging_utils import setup_logging
from src.dataset.create_dataset import write_dataset_info
from src.dataset.scan_videos import list_video_files
from src.dataset.schema import VIDEO_COLUMNS
from src.video_processing.video_probe import probe_video

logger = logging.getLogger(__name__)

REQUIRED_FIELDS = ("video_id", "filename", "filepath", "duration_seconds", "fps", "total_frames",
                   "width", "height", "file_size", "sha256")
# Trạng thái của giai đoạn sau: validate không hạ cấp nếu video vẫn hợp lệ.
ADVANCED_STATUSES = {"processing", "detected", "aggregated"}


def _positive(value) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def validate(settings: Settings | None = None, quick: bool = False, strict: bool = False,
             update_status: bool = True) -> dict:
    settings = settings or get_settings()
    paths = settings.paths
    issues: list[dict] = []

    def add(level, code, message, video_id=None, filename=None):
        issues.append({"level": level, "code": code, "video_id": video_id, "filename": filename, "message": message})

    if not paths.videos_csv.exists():
        add("error", "missing_metadata_file", f"Không có {paths.videos_csv}; chạy create_dataset/scan_videos")
        rows = []
    else:
        header = read_csv_header(paths.videos_csv)
        missing_cols = [c for c in VIDEO_COLUMNS if c not in header]
        if missing_cols:
            add("error", "missing_metadata_columns", f"videos.csv thiếu cột: {missing_cols}")
        rows = read_csv(paths.videos_csv)

    # --- Trùng video_id / filepath / nội dung
    for column, code in (("video_id", "duplicate_id"), ("filepath", "duplicate_id")):
        for value, count in Counter(r.get(column) for r in rows if r.get(column)).items():
            if count > 1:
                for r in rows:
                    if r.get(column) == value:
                        add("error", code, f"{column}={value} xuất hiện {count} lần", r.get("video_id"), r.get("filename"))
    by_hash = defaultdict(list)
    for r in rows:
        if r.get("sha256"):
            by_hash[r["sha256"]].append(r)
    for group in by_hash.values():
        if len(group) > 1:
            group = sorted(group, key=lambda r: r.get("video_id", ""))
            for dup in group[1:]:
                add("error", "duplicate_content", f"trùng nội dung (sha256) với {group[0]['video_id']}",
                    dup.get("video_id"), dup.get("filename"))

    # --- Từng video
    for r in rows:
        vid, name = r.get("video_id"), r.get("filename")
        missing = [f for f in REQUIRED_FIELDS if not str(r.get(f, "")).strip()]
        missing += [f for f in ("duration_seconds", "fps", "total_frames", "width", "height", "file_size")
                    if f not in missing and not _positive(r.get(f))]
        if missing:
            add("error", "missing_metadata", f"thiếu/không hợp lệ: {', '.join(missing)}", vid, name)
        if not r.get("filepath"):
            continue
        path = (paths.root / r["filepath"]).resolve()
        if not path.is_relative_to(paths.root.resolve()):
            add("error", "missing_file", f"filepath nằm ngoài dataset: {r['filepath']}", vid, name)
            continue
        if not path.is_file():
            add("error", "missing_file", f"không tìm thấy file tại filepath={r['filepath']}", vid, name)
            continue
        if path.suffix.lower() not in settings.supported_extensions:
            add("error", "unsupported_format", f"định dạng {path.suffix} không hỗ trợ", vid, name)
        if _positive(r.get("file_size")) and int(float(r["file_size"])) != path.stat().st_size:
            add("error", "metadata_stale", "file_size khác file thực tế – chạy lại scan_videos", vid, name)
        if not quick:
            probe = probe_video(path)
            if not probe.readable:
                code = "unreadable" if any("mở" in e or "rỗng" in e for e in probe.errors) else "corrupt"
                add("error", code, "; ".join(probe.errors), vid, name)
            for warning in probe.warnings:
                add("warning", "probe_warning", warning, vid, name)
        if _positive(r.get("duration_seconds")) and float(r["duration_seconds"]) < settings.min_video_duration_sec:
            add("warning", "too_short",
                f"{float(r['duration_seconds']) / 60:.1f} phút < {settings.min_video_duration_sec / 60:g} phút", vid, name)
        if not r.get("license"):
            add("warning", "missing_license", "chưa khai báo giấy phép (scan_videos --register-source)", vid, name)
        if settings.allowed_categories:
            category = (r.get("category") or "").strip().lower()
            if category not in settings.allowed_categories:
                add("warning", "out_of_domain",
                    f"category='{category or '(trống)'}' ngoài domain {list(settings.allowed_categories)} "
                    f"(khai báo bằng scan_videos --register-source ... --category)", vid, name)

    # --- File có trên đĩa nhưng chưa có metadata
    registered = {r.get("filepath") for r in rows}
    files, others = list_video_files(settings)
    for f in files:
        rel = f.relative_to(paths.root).as_posix()
        if rel not in registered:
            add("error", "unregistered_file", "video chưa có metadata – chạy scan_videos", None, f.name)
    for f in others:
        add("warning", "unsupported_format", f"file không phải video hỗ trợ ({f.suffix})", None, f.name)

    if strict:
        for issue in issues:
            if issue["level"] == "warning":
                issue["level"] = "error"

    errors_by_video = defaultdict(list)
    for issue in issues:
        if issue["video_id"] and issue["level"] == "error":
            errors_by_video[issue["video_id"]].append(issue["code"])

    if update_status and rows and paths.videos_csv.exists():
        for r in rows:
            if r.get("video_id") in errors_by_video:
                r["processing_status"] = "invalid"
            elif r.get("processing_status") not in ADVANCED_STATUSES:
                r["processing_status"] = "validated"
        write_csv(paths.videos_csv, VIDEO_COLUMNS, rows)
        write_dataset_info(paths, settings, rows)

    n_err = sum(1 for i in issues if i["level"] == "error")
    n_warn = sum(1 for i in issues if i["level"] == "warning")
    report = {
        "validated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "quick" if quick else "deep",
        "strict": strict,
        "total_videos": len(rows),
        "valid_videos": sum(1 for r in rows if r.get("video_id") not in errors_by_video),
        "errors": n_err,
        "warnings": n_warn,
        "ok": n_err == 0,
        "issues": issues,
    }
    atomic_json(paths.validation_report, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Kiểm tra tính hợp lệ của Dataset")
    parser.add_argument("--dataset-dir", type=Path)
    parser.add_argument("--quick", action="store_true", help="Không giải mã thử frame")
    parser.add_argument("--strict", action="store_true", help="Coi cảnh báo là lỗi")
    parser.add_argument("--no-update", action="store_true", help="Không cập nhật processing_status")
    args = parser.parse_args(argv)
    settings = Settings(dataset_dir=args.dataset_dir.resolve()) if args.dataset_dir else get_settings()
    setup_logging(settings.log_level, settings.log_dir)
    report = validate(settings, quick=args.quick, strict=args.strict, update_status=not args.no_update)
    for issue in report["issues"]:
        log = logger.error if issue["level"] == "error" else logger.warning
        log("[%s] %s %s: %s", issue["code"], issue["video_id"] or "-", issue["filename"] or "", issue["message"])
    logger.info("Kết quả: %d video, %d hợp lệ, %d lỗi, %d cảnh báo -> %s",
                report["total_videos"], report["valid_videos"], report["errors"], report["warnings"],
                settings.paths.validation_report)
    if report["total_videos"] == 0 and report["ok"]:
        logger.info("Dataset chưa có video (hợp lệ nhưng rỗng).")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
