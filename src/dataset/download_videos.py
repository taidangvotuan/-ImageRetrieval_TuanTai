"""Tìm và tải **Music Video** có giấy phép **Creative Commons Attribution (CC BY)** bằng yt-dlp.

Quy tắc an toàn pháp lý:
* Luôn đọc metadata (``extract_info(download=False)``) TRƯỚC khi tải, và chỉ tải khi trường
  ``license`` là CC BY thuần. "Standard YouTube License", thiếu thông tin giấy phép, hoặc CC
  NC/SA/ND đều bị TỪ CHỐI. Video công khai trên YouTube KHÔNG mặc định được phép tải.
* Sàng lọc dùng CHUNG tiêu chí với validator (``config/dataset_criteria.json``): thời lượng
  150–420 s, luật loại trừ (AI, hậu trường, lyrics, teaser, …) trên tiêu đề + mô tả + tags.
  ``screen_result``: ``pass`` (không có dấu hiệu nào), ``review`` (có dấu hiệu weak – cần xem kỹ),
  ``reject`` (giấy phép / thời lượng / luật strong).
* Qua sàng lọc KHÔNG có nghĩa là hợp lệ: video tải về luôn ở ``pending_review`` cho tới khi
  người duyệt xác minh nguồn gốc, giấy phép và nội dung (``review_video``). Công cụ ghi
  ``license_verified=no`` vì nhãn CC BY của YouTube chưa chứng minh người đăng sở hữu nội dung.
* Metadata gốc (tiêu đề, mô tả, tags, kênh, ngày đăng) lưu vào ``metadata/source_info/`` làm căn cứ.

    python -m src.dataset.download_videos --preset music_video --max-results 30 --dry-run
    python -m src.dataset.download_videos --url-file approved_urls.txt
    python -m src.dataset.download_videos --url https://www.youtube.com/watch?v=XXXXXXXXXXX
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

from config.settings import PROJECT_ROOT, Settings, get_settings
from src.common.io_utils import write_csv
from src.common.logging_utils import ensure_utf8_stdio, setup_logging
from src.dataset.create_dataset import create_dataset
from src.dataset.scan_videos import register_source, scan, write_source_info
from src.dataset.validation.exclusion_rules import STRONG, RuleSet, normalize, phrase_regex

logger = logging.getLogger(__name__)

PRESETS_FILE = PROJECT_ROOT / "config" / "music_presets.json"

# Tham số ``sp`` (protobuf base64) của trang tìm kiếm YouTube: bộ lọc Creative Commons, có thể kết
# hợp bộ lọc độ dài. Chỉ để thu hẹp kết quả – giấy phép/thời lượng vẫn được kiểm tra lại từng video.
YOUTUBE_SEARCH = "https://www.youtube.com/results?search_query={query}&sp={sp}"
SEARCH_FILTERS = {
    "any": "EgIwAQ%253D%253D",      # CC
    "short": "EgQYATAB",            # CC + < 4 phút
    "medium": "EgQYAzAB",           # CC + 4–20 phút
}
CC_BY_LICENSE_URL = "https://creativecommons.org/licenses/by/3.0/"  # YouTube dùng CC BY 3.0

_CC_BY_TEXT = re.compile(r"creative\s+commons\s+attribution(?!\s*[-‐–]?\s*(non|share|no))", re.I)
_CC_BY_URL = re.compile(r"creativecommons\.org/licenses/by/\d", re.I)
_NON_BY = re.compile(r"non\s*-?commercial|share\s*-?alike|no\s*-?deriv|by-nc|by-sa|by-nd", re.I)

CANDIDATE_COLUMNS = ["screen_result", "reason", "weak_signals", "url", "title", "uploader", "channel_id",
                     "duration", "license_raw", "checked_at"]
# Trường của yt-dlp được lưu làm căn cứ xác minh.
SOURCE_INFO_KEYS = ("id", "title", "description", "tags", "categories", "channel", "channel_id",
                    "channel_url", "uploader", "upload_date", "duration", "license", "webpage_url",
                    "width", "height", "age_limit")
# Lỗi yt-dlp khi video bị YouTube giới hạn độ tuổi (không đọc được metadata nếu không đăng nhập).
_AGE_GATE = re.compile(r"confirm your age|age[- ]restricted|inappropriate for some users", re.I)


@dataclass
class Rules:
    min_duration: float
    max_duration: float | None = None
    category: str = "music_video"
    series: str = ""
    trusted_channels: dict[str, str] = field(default_factory=dict)  # channel_id -> mô tả; rỗng = mọi kênh
    title_keywords: list[str] = field(default_factory=list)          # nếu có: cần khớp ít nhất 1
    exclude_keywords: list[str] = field(default_factory=list)        # cụm strong bổ sung (CLI)
    ruleset: RuleSet | None = None                                    # None = nạp từ file tiêu chí
    moderator: object | None = None                                   # TextModerator (an toàn nội dung)

    def text_moderator(self):
        if self.moderator is None:
            from src.dataset.validation.content_safety import text_moderator_for

            self.moderator = text_moderator_for(get_settings())
        return self.moderator

    def rules(self) -> RuleSet:
        if self.ruleset is None:
            self.ruleset = RuleSet.from_settings()
            self.ruleset.add_custom(self.exclude_keywords)
        return self.ruleset


@dataclass
class Candidate:
    url: str
    video_id: str | None
    title: str | None
    uploader: str | None
    channel_id: str | None
    duration: float | None
    license_raw: str | None
    accepted: bool                    # qua sàng lọc (được phép tải) – KHÔNG phải validation_status
    reason: str
    weak_signals: list[str] = field(default_factory=list)
    info: dict = field(default_factory=dict, repr=False)

    @property
    def screen_result(self) -> str:
        if not self.accepted:
            return "reject"
        return "review" if self.weak_signals else "pass"


def classify_license(license_text: str | None) -> tuple[bool, str]:
    """Chỉ chấp nhận CC BY thuần (không NC/SA/ND)."""
    if not license_text:
        return False, "không có thông tin giấy phép"
    if _NON_BY.search(license_text):
        return False, f"giấy phép không phải CC BY thuần: {license_text}"
    if _CC_BY_TEXT.search(license_text) or _CC_BY_URL.search(license_text):
        return True, "CC BY"
    return False, f"giấy phép không phải CC BY: {license_text}"


def evaluate(info: dict, rules: Rules) -> Candidate:
    license_raw = info.get("license")
    duration = info.get("duration")
    channel_id = info.get("channel_id")
    title = info.get("title") or ""
    uploader = info.get("channel") or info.get("uploader")
    signals = rules.rules().evaluate({"title": title, "description": info.get("description"),
                                      "tags": info.get("tags") or []}, uploader=uploader)
    strong = [s for s in signals if s.level == STRONG]
    # An toàn nội dung trên metadata: chỉ từ strong (không mơ hồ) ở tiêu đề/tags mới tự loại.
    from src.dataset.validation.content_safety.text_moderator import check_age_restriction

    safety = rules.text_moderator().check("metadata", {"title": title, "description": info.get("description"),
                                                       "tags": info.get("tags") or []})
    age = check_age_restriction(info, rules.text_moderator().config)
    safety_auto = [f for f in safety.flags + age.flags if f.auto_reject]
    ok, reason = classify_license(license_raw)
    # Thông báo là lambda: chỉ format khi điều kiện đúng (tránh format None).
    checks = [
        (rules.trusted_channels and channel_id not in rules.trusted_channels,
         lambda: f"kênh '{uploader}' ({channel_id}) không thuộc danh sách kênh tin cậy"),
        (rules.title_keywords and not any(phrase_regex(k).search(normalize(title)) for k in rules.title_keywords),
         lambda: f"tiêu đề không chứa từ khóa {rules.title_keywords}"),
        (bool(strong), lambda: "luật loại trừ: " + "; ".join(s.describe() for s in strong)),
        (bool(safety_auto), lambda: "an toàn nội dung: " + "; ".join(f.describe() for f in safety_auto)),
        (duration is None, lambda: "không xác định được thời lượng"),
        (duration is not None and duration < rules.min_duration,
         lambda: f"quá ngắn: {duration:.0f} s < {rules.min_duration:g} s"),
        (duration is not None and rules.max_duration is not None and duration > rules.max_duration,
         lambda: f"quá dài: {duration:.0f} s > {rules.max_duration:g} s"),
        (info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"),
         lambda: "đang livestream – không tải"),
    ]
    for failed, message in checks:
        if ok and failed:
            ok, reason = False, message()
    return Candidate(
        url=info.get("webpage_url") or info.get("original_url") or info.get("url", ""),
        video_id=info.get("id"), title=info.get("title"), uploader=uploader, channel_id=channel_id,
        duration=duration, license_raw=license_raw, accepted=ok, reason=reason,
        weak_signals=[s.describe() for s in signals if s.level != STRONG]
        + [f"an toàn nội dung: {f.describe()}" for f in safety.flags if not f.auto_reject],
        info={k: info.get(k) for k in SOURCE_INFO_KEYS if info.get(k) is not None},
    )


def load_preset(name: str, path: Path = PRESETS_FILE) -> dict:
    presets = json.loads(path.read_text(encoding="utf-8"))
    if name not in presets or name.startswith("_"):
        raise KeyError(f"Không có preset '{name}'. Có: {[k for k in presets if not k.startswith('_')]}")
    return presets[name]


def _ydl(extra: dict | None = None):
    import yt_dlp

    opts = {"quiet": True, "no_warnings": True, "noprogress": True, "skip_download": True}
    opts.update(extra or {})
    return yt_dlp.YoutubeDL(opts)


def search_urls(query: str, max_results: int, length: str = "any") -> list[str]:
    url = YOUTUBE_SEARCH.format(query=quote_plus(query), sp=SEARCH_FILTERS[length])
    with _ydl({"extract_flat": "in_playlist", "playlistend": max_results}) as ydl:
        result = ydl.extract_info(url, download=False)
    urls = []
    for entry in (result or {}).get("entries") or []:
        if entry and entry.get("id") and entry.get("ie_key", "Youtube") == "Youtube":
            urls.append(f"https://www.youtube.com/watch?v={entry['id']}")
    return urls[:max_results]


def inspect(url: str, rules: Rules) -> Candidate:
    with _ydl() as ydl:
        info = ydl.extract_info(url, download=False)
    return evaluate(info, rules)


def download(candidate: Candidate, settings: Settings, max_height: int, rules: Rules) -> Path:
    import yt_dlp

    from src.video_processing.video_probe import ffmpeg_executable

    raw = settings.paths.raw_videos
    raw.mkdir(parents=True, exist_ok=True)
    # Ưu tiên H.264 (avc1) trong MP4 để trình duyệt / Streamlit phát được; chỉ MP4 được chấp nhận.
    fmt = (f"bv*[height<={max_height}][vcodec^=avc1][ext=mp4]+ba[ext=m4a]/"
           f"b[height<={max_height}][ext=mp4]/bv*[height<={max_height}]+ba/b[height<={max_height}]")
    opts = {
        "format": fmt,
        "merge_output_format": "mp4",
        "outtmpl": str(raw / "yt_%(id)s.%(ext)s"),
        "download_archive": str(settings.paths.metadata / "download_archive.txt"),  # resume / chống tải lại
        "quiet": True, "no_warnings": True, "noprogress": True,
        "retries": 5, "fragment_retries": 5, "continuedl": True,
        "ffmpeg_location": ffmpeg_executable(),
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(candidate.url, download=True)
        # info là None khi video đã có trong download_archive (đã tải trước đó).
        if info is not None:
            # Kiểm tra lại giấy phép/kênh trên đúng metadata của lần tải (phòng thay đổi giữa chừng).
            again = evaluate(info, Rules(0, trusted_channels=rules.trusted_channels, ruleset=rules.rules()))
            if not again.accepted:
                raise RuntimeError(f"Metadata thay đổi khi tải: {again.reason}")
    path = raw / f"yt_{candidate.video_id}.mp4"
    if not path.exists():
        matches = sorted(raw.glob(f"yt_{candidate.video_id}.*"))
        if not matches:
            raise FileNotFoundError(f"Không thấy file đã tải cho {candidate.video_id}")
        path = matches[0]
    return path


def write_candidates(path: Path, summary: dict) -> None:
    """Ghi danh sách ứng viên để người dùng chọn trước khi tải."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = []
    for c in summary["accepted"] + summary["rejected"]:
        rows.append({**c, "screen_result": c["screen_result"], "weak_signals": " | ".join(c["weak_signals"]),
                     "checked_at": now})
    write_csv(path, CANDIDATE_COLUMNS, rows)


def _as_row(cand: Candidate) -> dict:
    row = asdict(cand)
    row.pop("info", None)
    row["screen_result"] = cand.screen_result
    return row


def run(settings: Settings, urls: list[str], dry_run: bool, max_height: int, rules: Rules,
        limit: int | None = None) -> dict:
    create_dataset(settings)
    summary = {"accepted": [], "rejected": [], "downloaded": [], "failed": []}
    for url in dict.fromkeys(urls):  # bỏ URL trùng, giữ thứ tự
        if limit is not None and len(summary["accepted"]) >= limit:
            break
        try:
            cand = inspect(url, rules)
        except Exception as exc:
            if _AGE_GATE.search(str(exc)):
                # YouTube giới hạn độ tuổi: đây là kết quả kiểm duyệt của nguồn, không phải lỗi mạng.
                logger.warning("TỪ CHỐI %s: nguồn giới hạn độ tuổi (age_restricted)", url)
                summary["rejected"].append(_as_row(Candidate(
                    url, None, None, None, None, None, None, False,
                    "age_restricted: nguồn giới hạn độ tuổi (18+)")))
                continue
            logger.error("Không đọc được metadata %s: %s", url, exc)
            summary["failed"].append({"url": url, "error": str(exc)})
            continue
        length = f"{cand.duration:.0f} s" if cand.duration else "?"
        if not cand.accepted:
            logger.warning("TỪ CHỐI %s (%s, %s): %s", url, cand.title, length, cand.reason)
            summary["rejected"].append(_as_row(cand))
            continue
        logger.info("%s %s | %s | %s | kênh: %s (%s)%s", cand.screen_result.upper(), url, cand.title, length,
                    cand.uploader, cand.channel_id,
                    f" | cần chú ý: {'; '.join(cand.weak_signals)}" if cand.weak_signals else "")
        summary["accepted"].append(_as_row(cand))
        if dry_run:
            continue
        try:
            path = download(cand, settings, max_height, rules)
        except Exception as exc:
            logger.error("Tải thất bại %s: %s", url, exc)
            summary["failed"].append({"url": url, "error": str(exc)})
            continue
        register_source(settings, path.name, source="youtube", source_url=cand.url, title=cand.title,
                        author=cand.uploader, channel_id=cand.channel_id, license="CC BY",
                        license_url=CC_BY_LICENSE_URL, license_verified="no",
                        category=rules.category, series=rules.series,
                        retrieved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        notes=f"yt-dlp license field: {cand.license_raw} (chưa xác minh quyền sở hữu)")
        write_source_info(settings, path.name, cand.info)
        summary["downloaded"].append(path.name)
        logger.info("Đã tải: %s (pending_review – cần duyệt bằng review_video)", path.name)
    if dry_run:
        out = settings.paths.metadata / "download_candidates.csv"
        write_candidates(out, summary)
        logger.info("Đã ghi danh sách ứng viên: %s – chọn URL rồi tải bằng --url / --url-file", out)
    if summary["downloaded"]:
        scan(settings)
    return summary


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Tìm/tải Music Video YouTube giấy phép CC BY vào dataset")
    parser.add_argument("--preset", help="Preset trong config/music_presets.json (vd. music_video)")
    parser.add_argument("--url", action="append", default=[], help="URL video (lặp lại được)")
    parser.add_argument("--url-file", type=Path, help="File text, mỗi dòng một URL (dòng # là chú thích)")
    parser.add_argument("--search", action="append", default=[], help="Truy vấn tìm kiếm (bộ lọc CC)")
    parser.add_argument("--max-results", type=int, default=20, help="Số kết quả mỗi truy vấn")
    parser.add_argument("--length", choices=sorted(SEARCH_FILTERS), default="any",
                        help="Bộ lọc độ dài khi tìm (mặc định any; thời lượng vẫn lọc theo tiêu chí trong code)")
    parser.add_argument("--limit", type=int, help="Dừng sau khi N video qua sàng lọc")
    parser.add_argument("--category", default="music_video", help="(cũ) nhãn ghi vào sources.csv")
    parser.add_argument("--series", help="Tùy chọn: album / dự án / nghệ sĩ")
    parser.add_argument("--trusted-channel", action="append", default=[], help="Chỉ nhận channel_id này (lặp lại được)")
    parser.add_argument("--title-keyword", action="append", default=[], help="Tiêu đề phải chứa (không dấu)")
    parser.add_argument("--exclude-keyword", action="append", default=[], help="Thêm cụm loại trừ (strong)")
    parser.add_argument("--max-height", type=int, default=720, help="Độ phân giải tối đa (720 hoặc 1080)")
    parser.add_argument("--min-duration", type=float, help="Ghi đè ngưỡng dưới (giây)")
    parser.add_argument("--max-duration", type=float, help="Ghi đè ngưỡng trên (giây)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Chỉ kiểm tra, không tải; ghi metadata/download_candidates.csv")
    parser.add_argument("--dataset-dir", type=Path)
    args = parser.parse_args(argv)

    settings = Settings(dataset_dir=args.dataset_dir.resolve()) if args.dataset_dir else get_settings()
    setup_logging(settings.log_level, settings.log_dir)

    preset = load_preset(args.preset) if args.preset else {}
    rules = Rules(
        min_duration=settings.min_video_duration_sec if args.min_duration is None else args.min_duration,
        max_duration=settings.max_video_duration_sec if args.max_duration is None else args.max_duration,
        category=args.category or preset.get("category") or "music_video",
        series=args.series if args.series is not None else preset.get("series", ""),
        trusted_channels={**preset.get("trusted_channels", {}), **{c: "CLI" for c in args.trusted_channel}},
        title_keywords=args.title_keyword or preset.get("title_keywords", []),
        exclude_keywords=list(args.exclude_keyword),
    )

    urls = list(args.url)
    if args.url_file:
        urls += [line.strip() for line in args.url_file.read_text(encoding="utf-8").splitlines()
                 if line.strip() and not line.startswith("#")]
    for query in args.search or ([] if urls else preset.get("queries", [])):
        found = search_urls(query, args.max_results, args.length)
        logger.info("Tìm thấy %d kết quả (bộ lọc CC, độ dài=%s) cho '%s'", len(found), args.length, query)
        urls += found
    if not urls:
        parser.error("Cần --url, --url-file, --search hoặc --preset có queries")
    logger.info("Quy tắc: thời lượng %s–%s s, kênh tin cậy=%s", rules.min_duration, rules.max_duration,
                list(rules.trusted_channels) or "mọi kênh")
    summary = run(settings, urls, args.dry_run, args.max_height, rules, args.limit)
    logger.info("Tổng kết: %d qua sàng lọc, %d từ chối, %d đã tải, %d lỗi",
                len(summary["accepted"]), len(summary["rejected"]), len(summary["downloaded"]), len(summary["failed"]))
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
