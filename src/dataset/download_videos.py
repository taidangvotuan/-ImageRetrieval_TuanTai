"""Tìm và tải **Music Video** có giấy phép **Creative Commons Attribution (CC BY)** bằng yt-dlp.

Quy tắc an toàn pháp lý:
* Luôn đọc metadata (``extract_info(download=False)``) TRƯỚC khi tải, và chỉ tải
  khi trường ``license`` là CC BY thuần. "Standard YouTube License", thiếu thông tin
  giấy phép, hoặc CC NC/SA/ND đều bị TỪ CHỐI. Video công khai trên YouTube KHÔNG mặc
  định được phép tải.
* Giấy phép CC BY chỉ có giá trị khi người đăng sở hữu nội dung. Công cụ không tự xác
  minh được điều đó, nên quy trình là: ``--dry-run`` -> ghi danh sách ứng viên ra
  ``metadata/download_candidates.csv`` -> **người dùng duyệt từng video** (kênh có phải
  của nghệ sĩ/hãng không) -> tải bằng ``--url`` / ``--url-file``. Có thể giới hạn kênh
  bằng ``--trusted-channel``.
* Chỉ nhận MV dài trong khoảng ``VI_MIN/MAX_VIDEO_DURATION_SEC`` (mặc định 150–330 s).
* Loại tiêu đề kiểu lyric / audio / visualizer / bản lặp 1 giờ… (``exclude_keywords``),
  vì những video này không phải MV hoặc chỉ có ảnh tĩnh.
* Nguồn, kênh, tác giả, URL, giấy phép ghi vào ``metadata/sources.csv`` để ghi công
  (CC BY bắt buộc ghi công tác giả).

    python -m src.dataset.download_videos --preset music_video --max-results 30 --dry-run
    python -m src.dataset.download_videos --url-file my_approved_urls.txt
    python -m src.dataset.download_videos --url https://www.youtube.com/watch?v=XXXXXXXXXXX
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

from config.settings import PROJECT_ROOT, Settings, get_settings
from src.common.io_utils import write_csv
from src.common.logging_utils import ensure_utf8_stdio, setup_logging
from src.dataset.create_dataset import create_dataset
from src.dataset.scan_videos import register_source, scan

logger = logging.getLogger(__name__)

PRESETS_FILE = PROJECT_ROOT / "config" / "music_presets.json"

# Tham số ``sp`` (protobuf base64) của trang tìm kiếm YouTube: bộ lọc Creative Commons,
# có thể kết hợp bộ lọc độ dài. Chỉ để thu hẹp kết quả – giấy phép/thời lượng vẫn được
# kiểm tra lại cho từng video. MV 3–5 phút nằm vắt qua mốc 4 phút của bộ lọc "medium"
# (4–20 phút), nên mặc định dùng "any" rồi lọc thời lượng trong code.
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

CANDIDATE_COLUMNS = ["accepted", "reason", "url", "title", "uploader", "channel_id", "duration",
                     "license_raw", "checked_at"]


@dataclass
class Rules:
    min_duration: float
    max_duration: float | None = None
    category: str = "music_video"
    series: str = ""
    trusted_channels: dict[str, str] = field(default_factory=dict)  # channel_id -> mô tả; rỗng = mọi kênh
    title_keywords: list[str] = field(default_factory=list)          # nếu có: cần khớp ít nhất 1
    exclude_keywords: list[str] = field(default_factory=list)        # khớp bất kỳ -> từ chối


@dataclass
class Candidate:
    url: str
    video_id: str | None
    title: str | None
    uploader: str | None
    channel_id: str | None
    duration: float | None
    license_raw: str | None
    accepted: bool
    reason: str


def normalize(text: str) -> str:
    """Bỏ dấu tiếng Việt, chữ thường, gộp khoảng trắng: 'Vượt Ngàn' -> 'vuot ngan'."""
    text = unicodedata.normalize("NFD", (text or "").replace("đ", "d").replace("Đ", "D"))
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", text).strip().lower()


def _contains_word(text: str, keyword: str) -> bool:
    """So khớp theo ranh giới từ (không dấu): 'audio' không khớp 'audiovisual'."""
    return re.search(rf"(?<![a-z0-9]){re.escape(normalize(keyword))}(?![a-z0-9])", normalize(text)) is not None


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
    excluded = next((k for k in rules.exclude_keywords if _contains_word(title, k)), None)
    ok, reason = classify_license(license_raw)
    # Thông báo là lambda: chỉ format khi điều kiện đúng (tránh format None).
    checks = [
        (rules.trusted_channels and channel_id not in rules.trusted_channels,
         lambda: f"kênh '{info.get('channel')}' ({channel_id}) không thuộc danh sách kênh tin cậy"),
        (rules.title_keywords and not any(_contains_word(title, k) for k in rules.title_keywords),
         lambda: f"tiêu đề không chứa từ khóa {rules.title_keywords}"),
        (excluded is not None, lambda: f"tiêu đề chứa từ khóa loại trừ '{excluded}' (không phải MV)"),
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
        video_id=info.get("id"), title=info.get("title"),
        uploader=info.get("channel") or info.get("uploader"), channel_id=channel_id,
        duration=duration, license_raw=license_raw, accepted=ok, reason=reason,
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
    # Ưu tiên H.264 (avc1) để trình duyệt / Streamlit phát được.
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
            again = evaluate(info, Rules(0, trusted_channels=rules.trusted_channels))
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
    """Ghi danh sách ứng viên (chấp nhận + từ chối) để người dùng duyệt trước khi tải."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = [{**c, "accepted": "yes" if c["accepted"] else "no", "checked_at": now}
            for c in summary["accepted"] + summary["rejected"]]
    write_csv(path, CANDIDATE_COLUMNS, rows)


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
            logger.error("Không đọc được metadata %s: %s", url, exc)
            summary["failed"].append({"url": url, "error": str(exc)})
            continue
        length = f"{cand.duration:.0f} s" if cand.duration else "?"
        if not cand.accepted:
            logger.warning("TỪ CHỐI %s (%s, %s): %s", url, cand.title, length, cand.reason)
            summary["rejected"].append(asdict(cand))
            continue
        logger.info("CHẤP NHẬN %s | %s | %s | kênh: %s (%s)", url, cand.title, length, cand.uploader,
                    cand.channel_id)
        summary["accepted"].append(asdict(cand))
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
                        license_url=CC_BY_LICENSE_URL, license_verified="yes",
                        category=rules.category, series=rules.series,
                        retrieved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        notes=f"yt-dlp license field: {cand.license_raw}")
        summary["downloaded"].append(path.name)
        logger.info("Đã tải: %s", path.name)
    if dry_run:
        out = settings.paths.metadata / "download_candidates.csv"
        write_candidates(out, summary)
        logger.info("Đã ghi danh sách ứng viên: %s – duyệt kênh/tiêu đề rồi tải bằng --url / --url-file", out)
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
                        help="Bộ lọc độ dài khi tìm (mặc định any; thời lượng vẫn lọc 150–330 s trong code)")
    parser.add_argument("--limit", type=int, help="Dừng sau khi chấp nhận N video")
    parser.add_argument("--category", help="music_video")
    parser.add_argument("--series", help="Tùy chọn: album / dự án / nghệ sĩ")
    parser.add_argument("--trusted-channel", action="append", default=[], help="Chỉ nhận channel_id này (lặp lại được)")
    parser.add_argument("--title-keyword", action="append", default=[], help="Tiêu đề phải chứa (không dấu)")
    parser.add_argument("--exclude-keyword", action="append", default=[], help="Thêm từ khóa loại trừ")
    parser.add_argument("--max-height", type=int, default=720, help="Độ phân giải tối đa (720 hoặc 1080)")
    parser.add_argument("--min-duration", type=float, help="Ghi đè VI_MIN_VIDEO_DURATION_SEC (giây)")
    parser.add_argument("--max-duration", type=float, help="Ghi đè VI_MAX_VIDEO_DURATION_SEC (giây)")
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
        exclude_keywords=preset.get("exclude_keywords", []) + args.exclude_keyword,
    )
    if settings.allowed_categories and rules.category not in settings.allowed_categories:
        parser.error(f"--category phải thuộc {list(settings.allowed_categories)}")

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
    logger.info("Quy tắc: %s", rules)
    summary = run(settings, urls, args.dry_run, args.max_height, rules, args.limit)
    logger.info("Tổng kết: %d chấp nhận, %d từ chối, %d đã tải, %d lỗi",
                len(summary["accepted"]), len(summary["rejected"]), len(summary["downloaded"]), len(summary["failed"]))
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
