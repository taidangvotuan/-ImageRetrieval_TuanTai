"""Chạy các bước kiểm duyệt nội dung NẶNG (phụ đề, OCR; âm thanh/hình ảnh ở M3/M4) cho từng video.

Ngoại lệ đã được chấp thuận của cổng kiểm soát: bước này ĐỌC video ``pending_review`` (cần nội dung
để quyết định) nhưng CHỈ ghi vào ``dataset/moderation/`` – không bao giờ ghi vào ``processed/``.
Pipeline chính thức (YOLO, Spark, …) vẫn chỉ nhận video ``accepted``.

* Bỏ qua video bị loại vì lỗi kỹ thuật (không đọc được, sai thời lượng, …).
* Checkpoint theo (video, bước): kết quả đã có cho đúng sha256 và đúng phương pháp thì không chạy
  lại (``--force`` để chạy lại). Bị ngắt giữa chừng -> chạy lại tiếp từ video chưa xong.
* Sau khi chạy, validator cập nhật trạng thái (kết quả chỉ gắn cờ / tự loại theo chính sách).

    python -m src.dataset.moderate_videos                       # phụ đề + OCR cho mọi video cần kiểm
    python -m src.dataset.moderate_videos --stages subtitles --video MV_003
    python -m src.dataset.moderate_videos --stages ocr --ocr-every 1.0 --force
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

from config.settings import Settings, get_settings
from src.common.io_utils import read_csv
from src.common.logging_utils import ensure_utf8_stdio, setup_logging
from src.dataset.schema import TECHNICAL_REASONS
from src.dataset.validation.content_safety import save_result, text_moderator_for
from src.dataset.validation.content_safety.ocr import OcrModerator
from src.dataset.validation.content_safety.subtitles import fetch_subtitles, moderate_subtitles, subtitle_dir
from src.dataset.validation.dataset_validator import validate

logger = logging.getLogger(__name__)
STAGES = ("subtitles", "ocr")


def _stored(settings: Settings, video_id: str, scope: str) -> dict | None:
    try:
        return json.loads((settings.paths.moderation / video_id / f"{scope}.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def needs_run(settings: Settings, row: dict, scope: str, method: str, force: bool) -> bool:
    if force:
        return True
    stored = _stored(settings, row["video_id"], scope)
    return not (stored and stored.get("sha256") == row.get("sha256")
                and stored["result"].get("method") == method and stored["result"].get("method") != "not_run")


def select_videos(settings: Settings, video_ids: list[str]) -> list[dict]:
    rows = read_csv(settings.paths.videos_csv)
    if video_ids:
        rows = [r for r in rows if r.get("video_id") in video_ids]
    selected = []
    for r in rows:
        technical = set(filter(None, (r.get("rejection_reason") or "").split(";"))) & TECHNICAL_REASONS
        if technical:
            logger.info("Bỏ qua %s: bị loại vì lỗi kỹ thuật (%s)", r["video_id"], ", ".join(sorted(technical)))
            continue
        if not (settings.paths.root / r.get("filepath", "")).is_file():
            continue
        selected.append(r)
    return selected


def run(settings: Settings, stages: tuple[str, ...], video_ids: list[str] | None = None, force: bool = False,
        ocr_every: float | None = None, fetch: bool = True, ocr_factory=None, fetcher=fetch_subtitles) -> dict:
    validate(settings, deep=False)          # trạng thái mới nhất trước khi chọn video
    safety = settings.criteria["content_safety"]
    moderator = text_moderator_for(settings)
    ocr_cfg = dict(safety["ocr"])
    if ocr_every:
        ocr_cfg["sample_every_sec"] = ocr_every
    ocr = OcrModerator(moderator, ocr_cfg, ocr_factory)
    sources = {r["filename"]: r for r in read_csv(settings.paths.sources_csv)}
    summary = {"processed": [], "skipped": [], "errors": []}
    for row in select_videos(settings, video_ids or []):
        vid, sha = row["video_id"], row.get("sha256", "")
        for stage in stages:
            try:
                if stage == "subtitles":
                    out = subtitle_dir(settings, row["filename"])
                    url = row.get("source_url") or sources.get(row["filename"], {}).get("source_url")
                    if fetch and url and (force or not (out / "manifest.json").exists()):
                        fetcher(url, out, safety["subtitles"])
                    result = moderate_subtitles(moderator, out) if (url or (out / "manifest.json").exists()) else None
                    if result is None:
                        from src.dataset.validation.content_safety.providers import CheckResult
                        result = CheckResult("subtitles", "unknown", "no_source",
                                             notes=["subtitles: không có URL nguồn để lấy phụ đề"])
                elif stage == "ocr":
                    if not needs_run(settings, row, "ocr", ocr.method(), force):
                        summary["skipped"].append((vid, stage))
                        continue
                    started = time.perf_counter()
                    result = ocr.run(settings.paths.root / row["filepath"],
                                     text_out=settings.paths.moderation / vid / "ocr_text.json",
                                     auto_min_frames=int(safety["auto_reject"].get("ocr_min_frames", 2)))
                    logger.info("%s OCR xong trong %.0f s", vid, time.perf_counter() - started)
                else:
                    raise ValueError(f"bước không hỗ trợ: {stage}")
                save_result(settings, vid, sha, result)
                summary["processed"].append((vid, stage, result.status))
                logger.info("%s %-9s %-14s %s", vid, stage, result.status,
                            "; ".join(f.describe() for f in result.flags) or "")
            except Exception as exc:
                logger.error("%s %s lỗi: %s", vid, stage, exc)
                summary["errors"].append((vid, stage, str(exc)))
    report = validate(settings, deep=False)
    summary["validation"] = {k: report[k] for k in ("accepted", "rejected", "pending_review")}
    return summary


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Kiểm duyệt nội dung: phụ đề YouTube, OCR chữ trên khung hình")
    parser.add_argument("--stages", default=",".join(STAGES), help=f"Các bước, ngăn bằng dấu phẩy: {', '.join(STAGES)}")
    parser.add_argument("--video", action="append", default=[], help="Chỉ chạy video_id này (lặp lại được)")
    parser.add_argument("--force", action="store_true", help="Chạy lại kể cả khi đã có kết quả")
    parser.add_argument("--ocr-every", type=float, help="Khoảng lấy mẫu OCR (giây), mặc định theo cấu hình")
    parser.add_argument("--no-fetch", action="store_true", help="Không tải phụ đề (chỉ dùng file đã có)")
    parser.add_argument("--dataset-dir", type=Path)
    args = parser.parse_args(argv)
    stages = tuple(s.strip() for s in args.stages.split(",") if s.strip())
    if bad := [s for s in stages if s not in STAGES]:
        parser.error(f"Bước không hỗ trợ: {bad}. Hiện có: {STAGES} (âm thanh: M3, hình ảnh: M4)")
    settings = Settings(dataset_dir=args.dataset_dir.resolve()) if args.dataset_dir else get_settings()
    setup_logging(settings.log_level, settings.log_dir)
    summary = run(settings, stages, args.video, args.force, args.ocr_every, fetch=not args.no_fetch)
    logger.info("Tổng kết: %d bước đã chạy, %d bỏ qua (đã có kết quả), %d lỗi. Hiện có: %s",
                len(summary["processed"]), len(summary["skipped"]), len(summary["errors"]), summary["validation"])
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
