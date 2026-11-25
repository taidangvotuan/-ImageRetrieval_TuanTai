"""Kiểm tra kỹ thuật của metadata và file video (không đánh giá nội dung).

Mỗi lỗi trả về ``TechIssue`` gắn với một mã trong ``schema.REJECTION_REASONS`` (lỗi làm video
bị ``rejected``) hoặc là ghi chú (``reason=None``: cần chạy lại scan, cảnh báo VFR…).
Lỗi cấp dataset (thiếu cột, file chưa đăng ký) không gắn video_id.

Trùng lặp: bản có video_id nhỏ nhất được giữ, các bản sau bị ``duplicate_*`` và được liệt kê
trong báo cáo để xử lý thủ công. **Không xóa hay di chuyển file nào.**
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from config.settings import Settings
from src.common.io_utils import read_csv_header
from src.dataset.schema import VIDEO_COLUMNS
from src.video_processing.video_probe import probe_video

REQUIRED_TECH_FIELDS = ("fps", "total_frames", "width", "height", "file_size", "sha256")
_YOUTUBE_ID = re.compile(r"(?:v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{11})")


@dataclass
class TechIssue:
    code: str                 # mã chi tiết (vd. duration_out_of_range, metadata_stale)
    message: str
    video_id: str | None = None
    filename: str | None = None
    reason: str | None = None  # mã lý do rejected; None = chỉ ghi chú, không loại
    duplicate_of: str | None = None


def positive(value) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def source_key(url: str | None) -> str | None:
    """Khóa nguồn để phát hiện trùng: YouTube id nếu có, ngược lại URL chuẩn hóa."""
    if not url:
        return None
    if m := _YOUTUBE_ID.search(url):
        return f"youtube:{m.group(1)}"
    return url.strip().lower().rstrip("/")


def check_duration(duration, settings: Settings) -> TechIssue | None:
    if not positive(duration):
        return TechIssue("duration_unknown", "không xác định được thời lượng", reason="duration_unknown")
    d = float(duration)
    lo, hi = settings.min_video_duration_sec, settings.max_video_duration_sec
    if not (lo <= d <= hi):
        return TechIssue("duration_out_of_range", f"{d:.0f} s ngoài khoảng {lo:g}–{hi:g} s",
                         reason="duration_out_of_range")
    return None


def check_resolution(width, height, settings: Settings) -> TechIssue | None:
    if not (positive(width) and positive(height)):
        return None  # đã báo ở missing_metadata
    long_side = max(int(float(width)), int(float(height)))
    if long_side < settings.min_long_side_px:
        return TechIssue("low_resolution", f"{int(float(width))}x{int(float(height))} dưới 720p "
                         f"(cạnh dài {long_side} < {settings.min_long_side_px} px)", reason="low_resolution")
    return None


def check_format(filename: str, settings: Settings) -> TechIssue | None:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in settings.accepted_extensions:
        return TechIssue("not_mp4", f"định dạng {suffix or '(không rõ)'} – chỉ chấp nhận "
                         f"{', '.join(settings.accepted_extensions)} (không tự chuyển đổi)", reason="not_mp4")
    return None


def check_row(row: dict, settings: Settings, deep: bool = True) -> list[TechIssue]:
    """Kiểm tra kỹ thuật một dòng videos.csv (file, đọc được, MP4, thời lượng, độ phân giải)."""
    paths = settings.paths
    issues: list[TechIssue] = []
    missing = [f for f in REQUIRED_TECH_FIELDS if not str(row.get(f) or "").strip()]
    missing += [f for f in ("fps", "total_frames", "width", "height", "file_size")
                if f not in missing and not positive(row.get(f))]
    if missing:
        issues.append(TechIssue("missing_metadata", f"thiếu/không hợp lệ: {', '.join(missing)}",
                                reason="missing_metadata"))
    for check in (check_duration(row.get("duration_seconds"), settings),
                  check_resolution(row.get("width"), row.get("height"), settings),
                  check_format(row.get("filename", ""), settings)):
        if check:
            issues.append(check)

    rel = row.get("filepath") or ""
    path = (paths.root / rel).resolve() if rel else None
    if not rel or not path.is_relative_to(paths.root.resolve()):
        issues.append(TechIssue("missing_file", f"filepath không hợp lệ hoặc nằm ngoài dataset: {rel!r}",
                                reason="missing_file"))
    elif not path.is_file():
        issues.append(TechIssue("missing_file", f"không tìm thấy file tại filepath={rel}", reason="missing_file"))
    else:
        if positive(row.get("file_size")) and int(float(row["file_size"])) != path.stat().st_size:
            issues.append(TechIssue("metadata_stale", "file đã thay đổi so với metadata – chạy lại scan_videos"))
        if deep:
            probe = probe_video(path)
            row["probe_ok"] = "yes" if probe.readable else "no"
            if not probe.readable:
                issues.append(TechIssue("unreadable", "; ".join(probe.errors), reason="unreadable"))
            for warning in probe.warnings:
                issues.append(TechIssue("probe_warning", warning))
        elif row.get("probe_ok") == "no":
            # Chế độ nhanh không giải mã lại nhưng KHÔNG bỏ qua kết quả đọc thử đã lưu.
            issues.append(TechIssue("unreadable", "lần đọc thử trước thất bại (probe_ok=no)", reason="unreadable"))
        elif row.get("probe_ok") != "yes":
            issues.append(TechIssue("probe_missing", "chưa đọc thử video – chạy validate_dataset (không --quick)"))
    for issue in issues:
        issue.video_id, issue.filename = row.get("video_id"), row.get("filename")
    return issues


def check_duplicates(rows: list[dict], sources: dict[str, dict], root: Path | None = None) -> list[TechIssue]:
    """Trùng nội dung (sha256), tên file (không phân biệt hoa thường) và nguồn (URL / YouTube id).

    Chỉ xét video còn file trên đĩa (``root``): bản gốc đã bị xóa thì bản sao không còn là trùng.
    """
    if root is not None:
        rows = [r for r in rows if r.get("filepath") and (root / r["filepath"]).is_file()]
    issues: list[TechIssue] = []
    keys = {
        "duplicate_content": lambda r: r.get("sha256") or None,
        "duplicate_filename": lambda r: (r.get("filename") or "").lower() or None,
        "duplicate_source": lambda r: source_key(r.get("source_url")
                                                 or sources.get(r.get("filename"), {}).get("source_url")),
    }
    labels = {"duplicate_content": "trùng nội dung (sha256)", "duplicate_filename": "trùng tên file",
              "duplicate_source": "trùng nguồn"}
    for code, key in keys.items():
        groups: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            if (k := key(r)) is not None:
                groups[k].append(r)
        for group in groups.values():
            if len(group) < 2:
                continue
            group = sorted(group, key=lambda r: r.get("video_id", ""))
            keep = group[0]["video_id"]
            for dup in group[1:]:
                issues.append(TechIssue(code, f"{labels[code]} với {keep} – cần xử lý thủ công (không tự xóa)",
                                        dup.get("video_id"), dup.get("filename"), reason=code, duplicate_of=keep))
    return issues


def check_dataset(settings: Settings, rows: list[dict]) -> list[TechIssue]:
    """Lỗi cấp dataset: thiếu cột, trùng video_id, file trên đĩa chưa đăng ký."""
    from src.dataset.scan_videos import list_video_files

    paths = settings.paths
    issues: list[TechIssue] = []
    if paths.videos_csv.exists():
        missing = [c for c in VIDEO_COLUMNS if c not in read_csv_header(paths.videos_csv)]
        if missing:
            issues.append(TechIssue("missing_metadata_columns",
                                    f"videos.csv thiếu cột {missing} – chạy python -m src.dataset.migrate_metadata"))
    seen: dict[str, int] = defaultdict(int)
    for r in rows:
        seen[r.get("video_id", "")] += 1
    for vid, count in seen.items():
        if vid and count > 1:
            issues.append(TechIssue("duplicate_id", f"video_id={vid} xuất hiện {count} lần", vid))
    registered = {r.get("filepath") for r in rows}
    files, others = list_video_files(settings)
    for f in files:
        if f.relative_to(paths.root).as_posix() not in registered:
            issues.append(TechIssue("unregistered_file", "video chưa có metadata – chạy scan_videos", None, f.name))
    for f in others:
        issues.append(TechIssue("unsupported_file", f"không phải file video ({f.suffix})", None, f.name))
    return issues
