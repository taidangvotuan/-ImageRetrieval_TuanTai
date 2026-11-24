"""Quét ``dataset/raw/videos``, đo metadata và cập nhật ``metadata/videos.csv``.

* Incremental: file không đổi (cùng kích thước + mtime) thì giữ metadata cũ,
  không băm lại -> chạy lại nhanh, tiếp tục được nếu bị ngắt (CSV được ghi
  atomic sau mỗi video).
* ``video_id`` (MV_001, ...) được gán một lần và không đổi khi chạy lại.
* Đánh dấu video trùng nội dung (sha256), không đọc được, file đã biến mất.
* Nguồn/giấy phép lấy từ ``metadata/sources.csv``; khai báo bằng
  ``--register-source`` thay vì sửa CSV bằng tay.

    python -m src.dataset.scan_videos
    python -m src.dataset.scan_videos --force            # đo lại toàn bộ
    python -m src.dataset.scan_videos --register-source a.mp4 --source user --license "CC BY 4.0"
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from config.settings import Settings, get_settings
from src.common.io_utils import read_csv, sha256_file, write_csv
from src.common.logging_utils import ensure_utf8_stdio, setup_logging
from src.dataset.create_dataset import create_dataset, write_dataset_info
from src.dataset.schema import ARCHIVE_COLUMNS, SOURCE_COLUMNS, SPLIT_COLUMNS, VIDEO_COLUMNS
from src.video_processing.video_probe import probe_video

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def list_video_files(settings: Settings) -> tuple[list[Path], list[Path]]:
    """Trả về (file video hỗ trợ, file khác) trong raw/videos (đệ quy)."""
    raw = settings.paths.raw_videos
    supported, others = [], []
    if not raw.exists():
        return supported, others
    for path in sorted(p for p in raw.rglob("*") if p.is_file()):
        if path.name.startswith(".") or path.suffix in (".part", ".tmp", ".ytdl"):
            continue
        (supported if path.suffix.lower() in settings.supported_extensions else others).append(path)
    return supported, others


def _next_id(rows: list[dict], prefix: str) -> int:
    pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$")
    numbers = [int(m.group(1)) for r in rows if (m := pattern.match(r.get("video_id", "")))]
    return max(numbers, default=0) + 1


def _measure(path: Path, rel: str, row: dict) -> dict:
    stat = path.stat()
    probe = probe_video(path)
    row.update({
        "filename": path.name,
        "filepath": rel,
        "file_size": stat.st_size,
        "file_mtime": int(stat.st_mtime),
        "sha256": sha256_file(path),
        "duration_seconds": probe.duration_seconds,
        "container_duration_seconds": probe.container_duration_seconds,
        "fps": probe.fps,
        "total_frames": probe.total_frames,
        "width": probe.width,
        "height": probe.height,
        "codec": probe.codec,
        "scanned_at": _now(),
        "processing_status": "pending" if probe.readable else "invalid",
        "notes": "; ".join(probe.errors + probe.warnings),
    })
    return row


def assign_splits(video_ids: list[str], ratios: tuple[float, float, float]) -> dict[str, str]:
    """Chia deterministic: xếp video theo sha256(video_id) rồi cắt theo tỷ lệ.

    Khác với gán từng video độc lập theo hash (với 10 video dễ ra test rỗng), cách
    này đảm bảo đúng tỷ lệ và validation/test có ít nhất 1 video khi có >= 3 video.
    Thứ tự theo hash nên không phụ thuộc thứ tự tên file.
    """
    ordered = sorted(video_ids, key=lambda v: hashlib.sha256(v.encode()).hexdigest())
    n = len(ordered)
    n_val = max(1, round(n * ratios[1])) if n >= 3 else 0
    n_test = max(1, round(n * ratios[2])) if n >= 3 else 0
    n_train = n - n_val - n_test
    result = {}
    for i, vid in enumerate(ordered):
        result[vid] = "train" if i < n_train else ("validation" if i < n_train + n_val else "test")
    return result


def write_splits(settings: Settings, rows: list[dict]) -> dict[str, int]:
    groups: dict[str, list[dict]] = {"train": [], "validation": [], "test": []}
    valid = [r for r in rows if r.get("processing_status") != "invalid"]
    mapping = assign_splits([r["video_id"] for r in valid], settings.split_ratios)
    for row in valid:
        split = mapping[row["video_id"]]
        groups[split].append({"video_id": row["video_id"], "filename": row["filename"], "split": split})
    for split, items in groups.items():
        write_csv(settings.paths.splits / f"{split}.csv", SPLIT_COLUMNS, items)
    return {k: len(v) for k, v in groups.items()}


def scan(settings: Settings | None = None, force: bool = False) -> list[dict]:
    settings = settings or get_settings()
    paths = settings.paths
    if not paths.videos_csv.exists():
        create_dataset(settings)

    existing = {r["filepath"]: r for r in read_csv(paths.videos_csv) if r.get("filepath")}
    sources = {r["filename"]: r for r in read_csv(paths.sources_csv) if r.get("filename")}
    files, others = list_video_files(settings)
    for other in others:
        logger.warning("Bỏ qua file không phải video hỗ trợ: %s", other.name)

    rows: list[dict] = []
    seen: set[str] = set()
    next_id = _next_id(list(existing.values()), settings.video_id_prefix)

    def flush():
        # Ghi lại sau mỗi video: bị ngắt giữa chừng vẫn giữ được phần đã quét.
        current = {r["filepath"] for r in rows}
        pending_old = [r for k, r in existing.items() if k not in current]
        write_csv(paths.videos_csv, VIDEO_COLUMNS, sorted(rows + pending_old, key=lambda r: r["video_id"]))

    for index, path in enumerate(files, 1):
        rel = path.relative_to(paths.root).as_posix()
        seen.add(rel)
        old = existing.get(rel)
        stat = path.stat()
        unchanged = (old and not force and old.get("sha256")
                     and str(old.get("file_size")) == str(stat.st_size)
                     and str(old.get("file_mtime")) == str(int(stat.st_mtime)))
        if unchanged:
            row = dict(old)
            # Lỗi cấp dataset (mất file / trùng lặp) được tính lại ở mỗi lần quét;
            # lỗi do chính file (không đọc được) thì giữ nguyên vì file không đổi.
            notes = row.get("notes", "")
            if row.get("processing_status") == "invalid" and (
                    notes.startswith("file không tồn tại") or notes.startswith("trùng lặp")):
                row["processing_status"], row["notes"] = "pending", ""
            logger.info("[%d/%d] %s: không đổi, giữ metadata", index, len(files), path.name)
        else:
            row = {"video_id": old["video_id"] if old else f"{settings.video_id_prefix}{next_id:03d}"}
            if not old:
                next_id += 1
            logger.info("[%d/%d] %s: đo metadata + sha256 ...", index, len(files), path.name)
            _measure(path, rel, row)
        src = sources.get(path.name, {})
        for key in ("title", "source", "license", "category", "series"):
            row[key] = src.get(key, "") or row.get(key, "")
        rows.append(row)
        flush()

    # File đã đăng ký nhưng không còn trên đĩa: giữ dòng, đánh dấu invalid (không xóa âm thầm).
    for rel, old in existing.items():
        if rel not in seen:
            row = dict(old)
            row["processing_status"] = "invalid"
            row["notes"] = "file không tồn tại tại filepath (bị xóa/di chuyển?)"
            rows.append(row)
            logger.warning("%s: %s", row.get("video_id"), row["notes"])

    # Trùng nội dung: giữ bản có video_id nhỏ nhất.
    by_hash: dict[str, str] = {}
    for row in sorted(rows, key=lambda r: r["video_id"]):
        digest = row.get("sha256")
        if not digest or row.get("processing_status") == "invalid":
            continue
        if digest in by_hash:
            row["processing_status"] = "invalid"
            row["notes"] = f"trùng lặp nội dung với {by_hash[digest]}"
            logger.warning("%s (%s): %s", row["video_id"], row["filename"], row["notes"])
        else:
            by_hash[digest] = row["video_id"]

    rows.sort(key=lambda r: r["video_id"])
    write_csv(paths.videos_csv, VIDEO_COLUMNS, rows)
    splits = write_splits(settings, rows)
    info = write_dataset_info(paths, settings, rows)
    logger.info("Đã quét %d video (%.1f phút). Split: %s", info["total_videos"],
                info["total_duration_seconds"] / 60, splits)
    if not rows:
        logger.info("Chưa có video trong %s – xem %s", paths.raw_videos, paths.root / "README.md")
    return rows


def register_source(settings: Settings, filename: str, **fields) -> dict:
    """Thêm/cập nhật một dòng sources.csv (thay cho việc sửa CSV bằng tay)."""
    paths = settings.paths
    if not paths.sources_csv.exists():
        create_dataset(settings)
    if not (paths.raw_videos / filename).is_file():
        raise FileNotFoundError(f"Không thấy {filename} trong {paths.raw_videos}")
    rows = {r["filename"]: r for r in read_csv(paths.sources_csv)}
    row = rows.get(filename, {"filename": filename})
    row.update({k: v for k, v in fields.items() if v is not None})
    row.setdefault("retrieved_at", _now())
    rows[filename] = row
    write_csv(paths.sources_csv, SOURCE_COLUMNS, sorted(rows.values(), key=lambda r: r["filename"]))
    return row


def remove_video(settings: Settings, video_id: str, delete_file: bool = False,
                 reason: str = "") -> dict:
    """Gỡ một video khỏi dataset: xóa dòng videos.csv, sources.csv, download_archive.

    Mặc định file được chuyển vào ``settings.archive_dir/videos/`` (NGOÀI dataset/, nên
    scan không đăng ký lại) và một dòng metadata + nguồn + giấy phép được ghi vào
    ``archive_manifest.csv`` để truy vết. ``delete_file=True`` thì xóa hẳn file.
    """
    paths = settings.paths
    rows = read_csv(paths.videos_csv)
    target = next((r for r in rows if r.get("video_id") == video_id), None)
    if target is None:
        raise KeyError(f"Không có {video_id} trong videos.csv")
    sources = read_csv(paths.sources_csv)
    source = next((s for s in sources if s.get("filename") == target["filename"]), {})
    path = paths.root / target["filepath"]
    archived_path = ""
    if path.is_file():
        if delete_file:
            path.unlink()
        else:
            dest_dir = settings.archive_dir / "videos"
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / path.name
            if dest.exists():
                raise FileExistsError(f"{dest} đã tồn tại – không ghi đè")
            shutil.move(str(path), dest)
            archived_path = dest.relative_to(settings.archive_dir).as_posix()
    if not delete_file:
        manifest = settings.archive_dir / "archive_manifest.csv"
        record = {k: target.get(k) or source.get(k, "") for k in ARCHIVE_COLUMNS}
        record.update(archived_path=archived_path, removed_at=_now(), reason=reason)
        write_csv(manifest, ARCHIVE_COLUMNS, read_csv(manifest) + [record])
    write_csv(paths.videos_csv, VIDEO_COLUMNS, [r for r in rows if r is not target])
    write_csv(paths.sources_csv, SOURCE_COLUMNS, [s for s in sources if s.get("filename") != target["filename"]])
    archive = paths.metadata / "download_archive.txt"
    if archive.exists() and (m := re.match(r"yt_(.+)\.\w+$", target["filename"])):
        lines = [ln for ln in archive.read_text(encoding="utf-8").splitlines() if ln.strip() != f"youtube {m.group(1)}"]
        archive.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return target


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Quét video và cập nhật metadata")
    parser.add_argument("--dataset-dir", type=Path)
    parser.add_argument("--force", action="store_true", help="Đo lại metadata kể cả file không đổi")
    parser.add_argument("--register-source", metavar="FILENAME", help="Khai báo nguồn/giấy phép cho một file")
    parser.add_argument("--source", default=None, help="user / youtube / wikimedia / archive / other")
    parser.add_argument("--license", default=None)
    parser.add_argument("--source-url", default=None)
    parser.add_argument("--title", default=None)
    parser.add_argument("--author", default=None)
    parser.add_argument("--license-url", default=None)
    parser.add_argument("--notes", default=None)
    parser.add_argument("--category", default=None, help="music_video")
    parser.add_argument("--series", default=None, help="tùy chọn: album / dự án / nghệ sĩ")
    parser.add_argument("--remove", metavar="VIDEO_ID", action="append", default=[],
                        help="Gỡ video khỏi dataset, file chuyển vào archive/videos/ (lặp lại được)")
    parser.add_argument("--remove-all", action="store_true", help="Gỡ toàn bộ video đang có trong videos.csv")
    parser.add_argument("--reason", default="", help="Lý do gỡ (ghi vào archive_manifest.csv)")
    parser.add_argument("--delete-file", action="store_true", help="Dùng với --remove: xóa hẳn file video")
    args = parser.parse_args(argv)

    settings = Settings(dataset_dir=args.dataset_dir.resolve()) if args.dataset_dir else get_settings()
    setup_logging(settings.log_level, settings.log_dir)
    targets = list(args.remove)
    if args.remove_all:
        targets += [r["video_id"] for r in read_csv(settings.paths.videos_csv) if r.get("video_id")]
    for video_id in dict.fromkeys(targets):
        row = remove_video(settings, video_id, args.delete_file, args.reason)
        logger.info("Đã gỡ %s (%s)%s", row["video_id"], row["filename"],
                    " và xóa file" if args.delete_file else f" – file chuyển vào {settings.archive_dir / 'videos'}")
    if args.register_source:
        if not args.license:
            parser.error("--register-source cần --license")
        row = register_source(settings, args.register_source, source=args.source or "user",
                              license=args.license, source_url=args.source_url, title=args.title,
                              author=args.author, license_url=args.license_url, notes=args.notes,
                              category=args.category, series=args.series, license_verified="yes")
        logger.info("Đã khai báo nguồn: %s", row)
    scan(settings, force=args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
