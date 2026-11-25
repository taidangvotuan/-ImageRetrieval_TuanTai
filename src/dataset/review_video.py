"""Duyệt tay video – cách DUY NHẤT để một tiêu chí thành ``confirmed`` và video thành ``accepted``.

Người duyệt (``--reviewer``, bắt buộc) xem video và nguồn rồi ghi kết luận cùng căn cứ. Mỗi quyết
định được ghi THÊM vào ``metadata/reviews.csv`` (không sửa dòng cũ), gắn với sha256 hiện tại của file.

Cách 1 – phiếu duyệt hàng loạt::

    python -m src.dataset.review_video --export --reviewer "Nguyễn Văn A"
    # mở dataset/validation/review_sheet.csv, điền các cột quyết định + evidence, lưu lại
    python -m src.dataset.review_video --import dataset/validation/review_sheet.csv --reviewer "Nguyễn Văn A"

Cách 2 – một video::

    python -m src.dataset.review_video MV_003 --reviewer "Nguyễn Văn A" --content-type official_music_video \\
        --ai-generated confirmed --behind-the-scenes confirmed --lyrics confirmed \\
        --license confirmed --source confirmed --content-safety safe \
        --evidence "Kênh chính chủ của nghệ sĩ, MV quay thực tế, đã xem toàn video"

Giá trị tiêu chí: ``confirmed`` (đạt) / ``rejected`` (vi phạm) / ``unknown`` / ``not_applicable``.
An toàn nội dung: ``--content-safety safe`` CHỈ sau khi đã xem TOÀN BỘ video (kể cả các timestamp
bị gắn cờ trong review_notes); ``flagged`` cần ``--safety-categories`` (nhóm vi phạm).
Sau khi ghi, validator chạy lại để cập nhật ``validation_status``.
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from config.settings import Settings, get_settings
from src.common.io_utils import read_csv, write_csv
from src.common.logging_utils import ensure_utf8_stdio, setup_logging
from src.dataset.schema import CONTENT_TYPES, SAFETY_CATEGORIES, SAFETY_VALUES, VERIFICATION_VALUES
from src.dataset.validation.dataset_validator import validate
from src.dataset.validation.review_store import DECISION_FIELDS, ReviewError, append_reviews

logger = logging.getLogger(__name__)

SHEET_INFO_COLUMNS = ["video_id", "filename", "sha256", "title", "duration_seconds", "source_url",
                      "license", "current_status", "auto_findings", "moderation_flags"]
SHEET_COLUMNS = SHEET_INFO_COLUMNS + list(DECISION_FIELDS) + ["evidence", "notes"]


def export_sheet(settings: Settings, path: Path | None = None, include_all: bool = False) -> Path:
    """Phiếu duyệt cho video pending_review (hoặc mọi video với include_all). Cột quyết định để trống."""
    validate(settings, deep=False)
    path = path or settings.paths.validation / "review_sheet.csv"
    rows = []
    for v in read_csv(settings.paths.videos_csv):
        if include_all or v.get("validation_status") == "pending_review":
            rows.append({**{k: v.get(k, "") for k in SHEET_INFO_COLUMNS},
                         "current_status": v.get("validation_status"),
                         "auto_findings": v.get("verification_notes", ""),
                         "moderation_flags": v.get("review_notes", "")})
    write_csv(path, SHEET_COLUMNS, rows)
    return path


def import_sheet(settings: Settings, path: Path, reviewer: str) -> list[dict]:
    """Ghi các dòng có ít nhất một quyết định. sha256 trên phiếu phải khớp file hiện tại."""
    current = {v["video_id"]: v for v in read_csv(settings.paths.videos_csv)}
    decisions = []
    for line, row in enumerate(read_csv(path), start=2):
        if not any((row.get(f) or "").strip() for f in DECISION_FIELDS):
            continue
        video = current.get(row.get("video_id", ""))
        if video is None:
            raise ReviewError(f"Dòng {line}: không có {row.get('video_id')} trong videos.csv")
        if row.get("sha256") and row["sha256"] != video.get("sha256"):
            raise ReviewError(f"Dòng {line}: {row['video_id']} đã thay đổi sau khi xuất phiếu – xuất lại phiếu")
        decisions.append({**{f: row.get(f, "") for f in DECISION_FIELDS},
                          "video_id": video["video_id"], "filename": video["filename"],
                          "sha256": video.get("sha256", ""), "reviewer": reviewer,
                          "evidence": row.get("evidence", ""), "notes": row.get("notes", "")})
    return append_reviews(settings.paths.reviews_csv, decisions)


def review_one(settings: Settings, video_id: str, reviewer: str, evidence: str, notes: str = "",
               **values: str | None) -> dict:
    video = next((v for v in read_csv(settings.paths.videos_csv) if v.get("video_id") == video_id), None)
    if video is None:
        raise ReviewError(f"Không có {video_id} trong videos.csv")
    decision = {f: values.get(f) or "" for f in DECISION_FIELDS}
    decision.update(video_id=video_id, filename=video["filename"], sha256=video.get("sha256", ""),
                    reviewer=reviewer, evidence=evidence, notes=notes)
    return append_reviews(settings.paths.reviews_csv, [decision])[0]


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Duyệt tay video: ghi kết luận xác minh có căn cứ")
    parser.add_argument("video_id", nargs="?", help="Duyệt một video (vd. MV_003)")
    parser.add_argument("--reviewer", required=True, help="Tên người duyệt (bắt buộc, ghi vào reviewed_by)")
    parser.add_argument("--export", action="store_true", help="Xuất phiếu duyệt validation/review_sheet.csv")
    parser.add_argument("--all", action="store_true", help="Dùng với --export: gồm cả video đã accepted/rejected")
    parser.add_argument("--import", dest="import_path", type=Path, help="Nhập phiếu duyệt đã điền")
    parser.add_argument("--content-type", choices=CONTENT_TYPES)
    for name in ("ai-generated", "behind-the-scenes", "lyrics", "license", "source"):
        parser.add_argument(f"--{name}", choices=VERIFICATION_VALUES)
    parser.add_argument("--content-safety", choices=SAFETY_VALUES,
                        help="safe CHỈ sau khi xem toàn video; flagged cần --safety-categories")
    parser.add_argument("--safety-categories", default="",
                        help=f"Nhóm vi phạm, ngăn bằng ';': {', '.join(SAFETY_CATEGORIES)}")
    parser.add_argument("--evidence", default="", help="Căn cứ xác minh (bắt buộc khi ghi quyết định)")
    parser.add_argument("--notes", default="")
    parser.add_argument("--dataset-dir", type=Path)
    args = parser.parse_args(argv)
    settings = Settings(dataset_dir=args.dataset_dir.resolve()) if args.dataset_dir else get_settings()
    setup_logging(settings.log_level, settings.log_dir)
    try:
        if args.export:
            path = export_sheet(settings, include_all=args.all)
            logger.info("Đã xuất phiếu duyệt: %s – điền các cột %s và evidence", path, ", ".join(DECISION_FIELDS))
            return 0
        if args.import_path:
            written = import_sheet(settings, args.import_path, args.reviewer)
        elif args.video_id:
            written = [review_one(settings, args.video_id, args.reviewer, args.evidence, args.notes,
                                  content_type=args.content_type, ai_generated_status=args.ai_generated,
                                  behind_the_scenes_status=args.behind_the_scenes, lyrics_status=args.lyrics,
                                  license_status=args.license, source_status=args.source,
                                  content_safety_status=args.content_safety,
                                  content_safety_categories=args.safety_categories)]
        else:
            parser.error("Cần --export, --import FILE hoặc VIDEO_ID")
    except ReviewError as exc:
        logger.error("Không ghi quyết định nào: %s", exc)
        return 1
    report = validate(settings, deep=False)
    logger.info("Đã ghi %d quyết định. Hiện có: %d accepted, %d rejected, %d pending_review",
                len(written), report["accepted"], report["rejected"], report["pending_review"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
