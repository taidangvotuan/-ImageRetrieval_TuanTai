"""Nâng cấp metadata lên schema hiện hành (``schema.SCHEMA_VERSION``) – an toàn, có sao lưu.

* Sao lưu nguyên vẹn mọi file metadata/báo cáo cũ vào ``metadata/backups/<thời điểm>_v<cũ>/``
  TRƯỚC khi ghi bất cứ thứ gì.
* Thêm cột mới với giá trị an toàn (``unknown`` / ``pending_review``). Không có giá trị nào được
  nâng thành ``confirmed``: kể cả ``license_verified=yes`` hay ``category=music_video`` của schema cũ
  (do công cụ tự gán, không phải kết quả xác minh).
* ``processing_status`` cũ (pending / validated / invalid – lẫn với trạng thái hợp lệ) ->
  ``not_started``; trạng thái hợp lệ được validator tính lại.
* Báo cáo dẫn xuất ở vị trí cũ (``metadata/validation_report.json``, ``metadata/dataset_report.md``)
  được chuyển vào bản sao lưu; báo cáo mới nằm trong ``dataset/reports/``.
* Không xóa dòng metadata nào. Chạy lại nhiều lần không thay đổi gì thêm (idempotent).

    python -m src.dataset.migrate_metadata            # nâng cấp nếu cần
    python -m src.dataset.migrate_metadata --dry-run  # chỉ báo cáo
"""
from __future__ import annotations

import argparse
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from config.settings import Settings, get_settings
from src.common.io_utils import atomic_json, read_csv, read_csv_header, read_json, write_csv
from src.common.logging_utils import ensure_utf8_stdio, setup_logging
from src.dataset.schema import (LEGACY_PROCESSING_STATUS, SCHEMA_VERSION, SOURCE_COLUMNS, VIDEO_COLUMNS,
                                normalize_video_row)

logger = logging.getLogger(__name__)

LEGACY_DERIVED = ("validation_report.json", "dataset_report.md")


def current_version(settings: Settings) -> int:
    info = read_json(settings.paths.dataset_info, {}) or {}
    return int(info.get("schema_version", 1))


def needs_migration(settings: Settings) -> bool:
    paths = settings.paths
    if not paths.videos_csv.exists():
        return False
    header = read_csv_header(paths.videos_csv)
    return (current_version(settings) < SCHEMA_VERSION
            or any(c not in header for c in VIDEO_COLUMNS)
            or any((paths.metadata / name).exists() for name in LEGACY_DERIVED))


def _backup(settings: Settings, version: int) -> Path:
    paths = settings.paths
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = paths.backups / f"{stamp}_v{version}"
    n = 1
    while target.exists():
        target = paths.backups / f"{stamp}_v{version}_{n}"
        n += 1
    target.mkdir(parents=True)
    for file in sorted(paths.metadata.glob("*")):
        if file.is_file():
            shutil.copy2(file, target / file.name)
    return target


def migrate(settings: Settings | None = None, dry_run: bool = False) -> dict:
    settings = settings or get_settings()
    paths = settings.paths
    version = current_version(settings)
    if not needs_migration(settings):
        return {"migrated": False, "from_version": version, "to_version": SCHEMA_VERSION}
    rows = read_csv(paths.videos_csv)
    summary = {"migrated": not dry_run, "from_version": version, "to_version": SCHEMA_VERSION,
               "videos": len(rows), "backup": None,
               "added_columns": [c for c in VIDEO_COLUMNS if c not in read_csv_header(paths.videos_csv)]}
    if dry_run:
        return summary

    backup = _backup(settings, version)
    summary["backup"] = str(backup)
    migrated = []
    for row in rows:
        old_status = row.get("processing_status", "")
        if old_status in LEGACY_PROCESSING_STATUS:
            row["processing_status"] = LEGACY_PROCESSING_STATUS[old_status]
        new = normalize_video_row(row)
        if version < 2:
            # Trạng thái hợp lệ của schema cũ không được tin – validator tính lại từ đầu.
            new["validation_status"] = "pending_review"
        migrated.append(new)
    write_csv(paths.videos_csv, VIDEO_COLUMNS, migrated)
    if paths.sources_csv.exists():
        sources = read_csv(paths.sources_csv)
        write_csv(paths.sources_csv, SOURCE_COLUMNS + [c for c in read_csv_header(paths.sources_csv)
                                                       if c not in SOURCE_COLUMNS], sources)
    for name in LEGACY_DERIVED:
        old = paths.metadata / name
        if old.exists():
            old.unlink()  # đã có bản sao trong backup; bản mới sinh trong dataset/reports/
    info = read_json(paths.dataset_info, {}) or {}
    info["schema_version"] = SCHEMA_VERSION
    info["migrated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    info["migrated_from_backup"] = backup.relative_to(paths.root).as_posix()
    atomic_json(paths.dataset_info, info)
    logger.info("Đã nâng metadata v%d -> v%d (%d video). Sao lưu: %s", version, SCHEMA_VERSION, len(rows), backup)
    return summary


def ensure_schema(settings: Settings) -> dict:
    """Gọi bởi scan / validate / report: tự nâng cấp (có sao lưu) nếu metadata còn schema cũ."""
    if needs_migration(settings):
        return migrate(settings)
    return {"migrated": False}


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Nâng cấp metadata lên schema hiện hành (có sao lưu)")
    parser.add_argument("--dataset-dir", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="Chỉ báo cáo, không ghi")
    args = parser.parse_args(argv)
    settings = Settings(dataset_dir=args.dataset_dir.resolve()) if args.dataset_dir else get_settings()
    setup_logging(settings.log_level, settings.log_dir)
    summary = migrate(settings, dry_run=args.dry_run)
    logger.info("Kết quả: %s", summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
