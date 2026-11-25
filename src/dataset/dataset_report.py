"""Báo cáo Dataset theo tiêu chí tập trung -> ``reports/dataset_report.{json,csv,md}``.

Mọi con số "hợp lệ" (số MV, tổng thời lượng, mốc 5/10/30/50, ước tính frame) CHỈ tính video
``accepted``. Số liệu được đọc từ metadata sau khi chạy validator – báo cáo không tự kết luận lại.

    python -m src.dataset.dataset_report
"""
from __future__ import annotations

import argparse
import logging
from collections import Counter
from pathlib import Path

from config.settings import Settings, get_settings
from src.common.io_utils import atomic_json, atomic_write_text, read_csv, read_json, write_csv
from src.common.logging_utils import ensure_utf8_stdio, setup_logging
from src.dataset.schema import REJECTION_REASONS, VALIDATION_LIST_COLUMNS, VALIDATION_STATUSES

logger = logging.getLogger(__name__)

# Giả định chỉ để ƯỚC TÍNH số frame theo mốc (không phải số liệu đo): MV trung bình 4 phút.
ASSUMED_AVG_MV_SECONDS = 240
# Nhóm lý do theo yêu cầu báo cáo.
REASON_GROUPS = {
    "ai_generated": ("ai_generated",),
    "behind_the_scenes": ("behind_the_scenes",),
    "lyrics_video": ("lyrics_video",),
    "duration": ("duration_out_of_range", "duration_unknown"),
    "duplicate": ("duplicate_content", "duplicate_filename", "duplicate_source"),
    "profanity_or_abusive_language": ("profanity_or_abusive_language",),
    "violent_or_gory_content": ("violent_or_gory_content",),
    "violence_incitement": ("violence_incitement",),
    "explicit_or_obscene_content": ("explicit_or_obscene_content",),
    "age_restricted": ("age_restricted",),
}
SAFETY_STATUS_FIELDS = ("content_safety_status", "text_moderation_status", "audio_moderation_status",
                        "visual_moderation_status", "language_detection_status")


def _hms(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def _f(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _reasons(row: dict) -> list[str]:
    return [r for r in (row.get("rejection_reason") or "").split(";") if r]


def build_report(settings: Settings | None = None, revalidate: bool = True) -> tuple[dict, str]:
    """Chạy validator nhanh (mặc định) để số liệu khớp trạng thái hiện tại, rồi tổng hợp."""
    settings = settings or get_settings()
    paths = settings.paths
    if revalidate and paths.videos_csv.exists():
        from src.dataset.validation.dataset_validator import validate

        validate(settings, deep=False)
    videos = read_csv(paths.videos_csv)
    validation = read_json(paths.validation_report, {}) or {}
    by_status = {s: [v for v in videos if v.get("validation_status") == s] for s in VALIDATION_STATUSES}
    accepted = by_status["accepted"]
    rejected = by_status["rejected"]
    reason_counts = Counter(r for v in rejected for r in _reasons(v))
    acc_durations = [_f(v.get("duration_seconds")) for v in accepted]
    in_target = [d for d in acc_durations
                 if settings.target_min_duration_sec <= d <= settings.target_max_duration_sec]
    milestones = [{"target": m, "reached": len(accepted) >= m,
                   "est_frames_1fps": m * ASSUMED_AVG_MV_SECONDS, "est_frames_2fps": m * ASSUMED_AVG_MV_SECONDS * 2}
                  for m in settings.target_milestones]
    stats = {
        "total_checked": len(videos),
        "accepted": len(accepted),
        "rejected": len(rejected),
        "pending_review": len(by_status["pending_review"]),
        "rejected_by_group": {g: sum(1 for v in rejected if set(_reasons(v)) & set(codes))
                              for g, codes in REASON_GROUPS.items()},
        "rejection_reason_counts": dict(reason_counts),
        "accepted_duration_seconds": round(sum(acc_durations), 3),
        "accepted_in_target_range": len(in_target),
        "target_range_seconds": [settings.target_min_duration_sec, settings.target_max_duration_sec],
        "duration_rule_seconds": [settings.min_video_duration_sec, settings.max_video_duration_sec],
        "license_status": dict(Counter(v.get("license_status") or "unknown" for v in videos)),
        "source_status": dict(Counter(v.get("source_status") or "unknown" for v in videos)),
        "content_safety": {f: dict(Counter(v.get(f) or "unknown" for v in videos)) for f in SAFETY_STATUS_FIELDS},
        "review_required": sum(1 for v in videos if v.get("review_required") != "false"),
        "duplicates_to_resolve": validation.get("duplicates", []),
        "dataset_issues": validation.get("dataset_issues", []),
        "milestones": milestones,
        "current_milestone": max((m["target"] for m in milestones if m["reached"]), default=None),
        "validated_at": validation.get("validated_at"),
    }

    lines = [
        f"# Báo cáo Dataset – {settings.dataset_name} v{settings.dataset_version}", "",
        f"Kiểm tra gần nhất: {stats['validated_at'] or 'chưa chạy'}. "
        "Mọi số liệu “hợp lệ” chỉ tính video **accepted**.", "",
        "## Tổng quan", "",
        "| Chỉ số | Giá trị |", "|---|---|",
        f"| Tổng số video được kiểm tra | {stats['total_checked']} |",
        f"| **Accepted** (hợp lệ) | **{stats['accepted']}** |",
        f"| Rejected (bị loại) | {stats['rejected']} |",
        f"| Pending review (chờ xác minh) | {stats['pending_review']} |",
        f"| Bị loại do AI-generated | {stats['rejected_by_group']['ai_generated']} |",
        f"| Bị loại do hậu trường | {stats['rejected_by_group']['behind_the_scenes']} |",
        f"| Bị loại do lyric video | {stats['rejected_by_group']['lyrics_video']} |",
        f"| Sai thời lượng (ngoài {settings.min_video_duration_sec:g}–{settings.max_video_duration_sec:g} s) | {stats['rejected_by_group']['duration']} |",
        f"| Trùng lặp | {stats['rejected_by_group']['duplicate']} |",
        f"| Tổng thời lượng Dataset hợp lệ | {_hms(stats['accepted_duration_seconds'])} |",
        f"| MV accepted trong khoảng mục tiêu {settings.target_min_duration_sec:g}–{settings.target_max_duration_sec:g} s | {stats['accepted_in_target_range']} |",
        f"| Giấy phép (license_status) | {stats['license_status'] or '-'} |",
        f"| Nguồn gốc (source_status) | {stats['source_status'] or '-'} |",
        "", "## Kiểm duyệt an toàn nội dung", "",
        "Tự động chỉ gắn cờ hoặc loại khi bằng chứng rõ ràng; `safe` cho toàn video chỉ do người duyệt ghi. "
        "Bước chưa triển khai hiện `unknown` – xem `docs/CONTENT_MODERATION.md`.", "",
        "| Chỉ số | Giá trị |", "|---|---|",
        f"| Bị loại do ngôn từ tục tĩu / xúc phạm | {stats['rejected_by_group']['profanity_or_abusive_language']} |",
        f"| Bị loại do bạo lực / máu me | {stats['rejected_by_group']['violent_or_gory_content']} |",
        f"| Bị loại do kích động bạo lực | {stats['rejected_by_group']['violence_incitement']} |",
        f"| Bị loại do khiêu dâm | {stats['rejected_by_group']['explicit_or_obscene_content']} |",
        f"| Bị loại do giới hạn tuổi 18+ | {stats['rejected_by_group']['age_restricted']} |",
        f"| Cần người duyệt xem | {stats['review_required']} |",
        *[f"| `{f}` | {stats['content_safety'][f] or '-'} |" for f in SAFETY_STATUS_FIELDS],
        "", "## Tiến độ theo mốc (chỉ video accepted)", "",
        f"Mốc hiện tại: **{stats['current_milestone'] or 'chưa đạt mốc nào'}**.", "",
        "| Mốc | Trạng thái | Frame ước tính @1 fps | @2 fps |", "|---|---|---|---|",
    ]
    for m in milestones:
        lines.append(f"| {m['target']} MV | {'✅ đạt' if m['reached'] else '⏳ chưa'} | "
                     f"~{m['est_frames_1fps']:,} | ~{m['est_frames_2fps']:,} |")
    lines += ["", f"> Số frame là **ước tính** (giả định MV trung bình {ASSUMED_AVG_MV_SECONDS // 60} phút), "
                  "không phải số đo.", ""]
    if reason_counts:
        lines += ["## Lý do bị loại", "", "| Mã | Ý nghĩa | Số video |", "|---|---|---|"]
        for code, n in reason_counts.most_common():
            lines.append(f"| `{code}` | {REJECTION_REASONS.get(code, code)} | {n} |")
        lines.append("")
    if stats["duplicates_to_resolve"]:
        lines += ["## Trùng lặp cần xử lý thủ công", "",
                  "Không file nào bị tự động xóa. Chọn bản giữ lại rồi gỡ bản còn lại bằng "
                  "`scan_videos --remove VIDEO_ID --reason \"trùng lặp\"`.", "",
                  "| video_id | file | trùng với | loại |", "|---|---|---|---|"]
        for d in stats["duplicates_to_resolve"]:
            lines.append(f"| {d['video_id']} | {d['filename']} | {d['duplicate_of']} | `{d['code']}` |")
        lines.append("")
    if videos:
        lines += ["## Danh sách video", "",
                  "| video_id | tiêu đề | thời lượng | độ phân giải | trạng thái | lý do / còn thiếu | giấy phép | nguồn |",
                  "|---|---|---|---|---|---|---|---|"]
        for v in videos:
            title = (v.get("title") or v.get("filename") or "-").replace("|", "/")
            detail = v.get("rejection_reason") or (v.get("verification_notes") or "").split(" | ")[0]
            lines.append(f"| {v.get('video_id')} | {title} | {_hms(_f(v.get('duration_seconds')))} | "
                         f"{v.get('width')}x{v.get('height')} | {v.get('validation_status')} | "
                         f"{detail.replace('|', '/') or '-'} | {v.get('license') or '-'} ({v.get('license_status')}) | "
                         f"{v.get('source_url') or v.get('source') or '-'} ({v.get('source_status')}) |")
    else:
        lines += ["**Dataset chưa có MV nào.** Xem `dataset/README.md` để đưa MV vào.", ""]
    markdown = "\n".join(lines) + "\n"

    if paths.metadata.exists():
        atomic_json(paths.report_json, {**stats, "videos": [{k: v.get(k, "") for k in VALIDATION_LIST_COLUMNS}
                                                            for v in videos]})
        write_csv(paths.report_csv, VALIDATION_LIST_COLUMNS, videos)
        atomic_write_text(paths.report_md, markdown)
    return stats, markdown


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Báo cáo Dataset (accepted / rejected / pending_review)")
    parser.add_argument("--dataset-dir", type=Path)
    args = parser.parse_args(argv)
    settings = Settings(dataset_dir=args.dataset_dir.resolve()) if args.dataset_dir else get_settings()
    setup_logging(settings.log_level, settings.log_dir)
    if not settings.paths.videos_csv.exists():
        logger.error("Chưa khởi tạo dataset tại %s – chạy: python -m src.dataset.create_dataset", settings.dataset_dir)
        return 1
    _, markdown = build_report(settings)
    print(markdown)
    logger.info("Đã ghi %s, %s, %s", settings.paths.report_md, settings.paths.report_json, settings.paths.report_csv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
