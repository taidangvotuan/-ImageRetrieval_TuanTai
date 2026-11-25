"""Hồi quy công cụ Dataset: tạo cấu trúc, quét, metadata, lỗi kỹ thuật, trùng lặp, gỡ video, CLI."""
from __future__ import annotations

import os
import shutil

import pytest

from config.settings import Settings
from src.common.io_utils import read_csv, read_csv_header, read_json, write_csv
from src.dataset import scan_videos as scan_mod
from src.dataset.create_dataset import create_dataset
from src.dataset.dataset_report import build_report
from src.dataset.scan_videos import assign_splits, register_source, scan
from src.dataset.schema import VIDEO_COLUMNS
from src.dataset.validate_dataset import validate
from src.video_processing.video_probe import probe_video
from tests.conftest import by_filename, make_settings, make_video


def reasons(settings, filename) -> set[str]:
    row = by_filename(settings)[filename]
    return set(filter(None, row["rejection_reason"].split(";")))


def issue_codes(report) -> set[str]:
    return {i["code"] for i in report["dataset_issues"]}


# ---------------------------------------------------------------- dataset rỗng
def test_create_empty_dataset_structure(settings):
    create_dataset(settings)
    p = settings.paths
    for d in p.all_dirs():
        assert d.is_dir(), d
    assert read_csv_header(p.videos_csv) == VIDEO_COLUMNS
    assert read_csv(p.videos_csv) == []  # không có dòng giả
    labels = read_csv(p.annotations / "labels.csv")
    assert len(labels) == 80 and labels[2]["object_name"] == "car"
    targets = {r["object_name"] for r in labels if r["is_target"] == "yes"}
    assert targets == {"person", "car", "motorcycle", "bus", "bicycle", "dog", "cat", "chair", "bottle", "backpack"}
    assert (p.annotations / "annotation_guide.md").exists()
    assert p.reviews_csv.exists() and read_csv(p.reviews_csv) == []
    for name in ("train", "validation", "test"):
        assert (p.splits / f"{name}.csv").exists()
    info = read_json(p.dataset_info)
    assert info["total_videos"] == 0 and info["accepted_videos"] == 0 and info["schema_version"] == 3
    assert (p.root / "README.md").read_text(encoding="utf-8").startswith("# Dataset")


def test_create_dataset_is_idempotent(settings):
    create_dataset(settings)
    readme = settings.paths.root / "README.md"
    readme.write_text("custom", encoding="utf-8")
    created_at = read_json(settings.paths.dataset_info)["created_at"]
    create_dataset(settings)
    assert readme.read_text(encoding="utf-8") == "custom"
    assert read_json(settings.paths.dataset_info)["created_at"] == created_at
    assert not settings.paths.backups.exists()          # dataset mới: không cần migration


def test_tools_succeed_on_empty_dataset(settings):
    assert scan(settings) == []
    report = validate(settings)
    assert report["ok"] and report["total_videos"] == 0
    stats, md = build_report(settings)
    assert stats["total_checked"] == 0 and "chưa có MV nào" in md
    for name in ("accepted", "rejected", "pending_review"):
        assert (settings.paths.validation / f"{name}.csv").exists()
    for path in (settings.paths.report_md, settings.paths.report_json, settings.paths.report_csv):
        assert path.exists()


def test_main_entrypoints_on_empty_dataset(tmp_path):
    from src.dataset import create_dataset as c, dataset_report as r, validate_dataset as v
    args = ["--dataset-dir", str(tmp_path / "ds")]
    assert c.main(args) == 0
    assert scan_mod.main(args) == 0
    assert v.main(args) == 0
    assert r.main(args) == 0


# ---------------------------------------------------------------- video và metadata
def test_valid_video_metadata_starts_pending(settings, video_factory):
    video_factory("a.mp4", seconds=3, fps=10)
    rows = scan(settings)
    assert len(rows) == 1
    row = rows[0]
    assert row["video_id"] == "MV_001"
    assert row["filepath"] == "raw/videos/a.mp4"  # tương đối, không tuyệt đối
    assert float(row["fps"]) == pytest.approx(10)
    assert int(row["total_frames"]) == 30
    assert (int(row["width"]), int(row["height"])) == (64, 48)
    assert float(row["duration_seconds"]) == pytest.approx(3, abs=0.2)
    assert len(row["sha256"]) == 64 and row["probe_ok"] == "yes"
    assert (row["validation_status"], row["processing_status"]) == ("pending_review", "not_started")

    report = validate(settings)
    assert report["ok"]
    row = by_filename(settings)["a.mp4"]
    # Chưa có nguồn, chưa duyệt -> pending_review, mọi tiêu chí unknown, KHÔNG có confirmed.
    assert row["validation_status"] == "pending_review" and row["rejection_reason"] == ""
    assert {row[f] for f in ("ai_generated_status", "lyrics_status", "license_status", "source_status")} == {"unknown"}
    assert row["content_type"] == "unknown" and "chưa xác minh" in row["verification_notes"]


# ---------------------------------------------------------------- lỗi kỹ thuật -> rejected
def test_corrupt_random_bytes(settings):
    path = settings.paths.raw_videos / "broken.mp4"
    path.parent.mkdir(parents=True)
    path.write_bytes(os.urandom(4096))
    scan(settings)
    validate(settings)
    assert by_filename(settings)["broken.mp4"]["validation_status"] == "rejected"
    assert reasons(settings, "broken.mp4") & {"unreadable", "missing_metadata", "duration_unknown"}


def test_empty_file_unreadable(settings):
    path = settings.paths.raw_videos / "empty.mp4"
    path.parent.mkdir(parents=True)
    path.touch()
    assert not probe_video(path).readable
    scan(settings)
    validate(settings)
    assert "unreadable" in reasons(settings, "empty.mp4")


def test_quick_mode_keeps_stored_probe_failure(settings, video_factory):
    video_factory("a.mp4")
    scan(settings)
    rows = read_csv(settings.paths.videos_csv)
    rows[0]["probe_ok"] = "no"          # lần đọc thử trước đã thất bại
    write_csv(settings.paths.videos_csv, VIDEO_COLUMNS, rows)
    validate(settings, quick=True)
    assert "unreadable" in reasons(settings, "a.mp4")


def test_truncated_video(settings, video_factory):
    path = video_factory("t.mp4", seconds=3)
    data = path.read_bytes()
    path.write_bytes(data[: len(data) // 2])  # mp4v: moov atom ở cuối -> mất
    probe = probe_video(path)
    assert not probe.readable and probe.errors


def test_non_mp4_is_rejected_not_converted(settings):
    path = make_video(settings.paths.raw_videos / "clip.avi")
    scan(settings)
    validate(settings)
    assert "not_mp4" in reasons(settings, "clip.avi")
    assert path.exists() and not (settings.paths.raw_videos / "clip.mp4").exists()


def test_unsupported_file_is_reported(settings, video_factory):
    video_factory("a.mp4")
    (settings.paths.raw_videos / "notes.txt").write_text("x")
    scan(settings)
    assert "unsupported_file" in issue_codes(validate(settings))


# ---------------------------------------------------------------- trùng lặp, đường dẫn, metadata
def test_duplicate_content_flagged_never_deleted(settings, video_factory):
    a = video_factory("a.mp4", seed=1)
    copy = settings.paths.raw_videos / "b_copy.mp4"
    shutil.copy(a, copy)
    scan(settings)
    report = validate(settings)
    rows = by_filename(settings)
    assert rows["a.mp4"]["validation_status"] == "pending_review"
    assert rows["b_copy.mp4"]["validation_status"] == "rejected"
    assert "duplicate_content" in reasons(settings, "b_copy.mp4")
    dup = report["duplicates"][0]
    assert (dup["video_id"], dup["duplicate_of"]) == (rows["b_copy.mp4"]["video_id"], "MV_001")
    assert a.exists() and copy.exists()                          # không xóa file nào
    _, md = build_report(settings)
    assert "Trùng lặp cần xử lý thủ công" in md
    # Xóa bản gốc -> bản sao không còn bị coi là trùng ở lần kiểm tra sau.
    a.unlink()
    scan(settings)
    validate(settings)
    assert "duplicate_content" not in reasons(settings, "b_copy.mp4")


def test_duplicate_filename_in_subfolders(settings):
    make_video(settings.paths.raw_videos / "x" / "same.mp4", seed=1)
    make_video(settings.paths.raw_videos / "y" / "SAME.mp4", seed=2)
    scan(settings)
    report = validate(settings)
    assert [d["code"] for d in report["duplicates"]] == ["duplicate_filename"]


def test_duplicate_source_url(settings, video_factory):
    video_factory("a.mp4", seed=1)
    video_factory("b.mp4", seed=2)
    for name, url in (("a.mp4", "https://www.youtube.com/watch?v=AAAAAAAAAAA"),
                      ("b.mp4", "https://youtu.be/AAAAAAAAAAA")):
        register_source(settings, name, license="CC BY", source_url=url)
    scan(settings)
    validate(settings)
    assert "duplicate_source" in reasons(settings, "b.mp4")


def test_missing_file_wrong_path(settings, video_factory):
    path = video_factory("a.mp4")
    scan(settings)
    path.unlink()
    validate(settings)
    assert "missing_file" in reasons(settings, "a.mp4")
    rows = scan(settings)  # không xóa âm thầm dòng metadata
    assert len(rows) == 1 and "không tồn tại" in rows[0]["notes"]


def test_unregistered_file(settings, video_factory):
    video_factory("a.mp4", seed=1)
    scan(settings)
    video_factory("new.mp4", seed=2)
    report = validate(settings)
    assert "unregistered_file" in issue_codes(report) and not report["ok"]


def test_missing_metadata_field(settings, video_factory):
    video_factory("a.mp4")
    scan(settings)
    rows = read_csv(settings.paths.videos_csv)
    rows[0]["fps"] = ""
    write_csv(settings.paths.videos_csv, VIDEO_COLUMNS, rows)
    validate(settings, quick=True)
    assert "missing_metadata" in reasons(settings, "a.mp4")


def test_missing_metadata_columns_reported_without_writing(settings, video_factory):
    video_factory("a.mp4")
    scan(settings)
    write_csv(settings.paths.videos_csv, ["video_id", "filename"], read_csv(settings.paths.videos_csv))
    report = validate(settings, quick=True, update_status=False)
    assert "missing_metadata_columns" in issue_codes(report) and not report["ok"]


def test_stale_metadata_blocks_acceptance(settings, video_factory):
    path = video_factory("a.mp4", seconds=2, seed=1)
    scan(settings)
    tmp = settings.paths.root / "x.mp4"
    make_video(tmp, seconds=4, seed=5)
    shutil.move(tmp, path)
    validate(settings, quick=True)
    row = by_filename(settings)["a.mp4"]
    assert row["validation_status"] != "accepted" and "metadata_stale" in row["verification_notes"]


# ---------------------------------------------------------------- incremental / resume
def test_incremental_scan_keeps_ids_and_skips_rehash(settings, video_factory, monkeypatch):
    video_factory("b.mp4", seed=1)
    video_factory("a.mp4", seed=2)
    first = {r["filename"]: r["video_id"] for r in scan(settings)}
    assert sorted(first.values()) == ["MV_001", "MV_002"]

    calls = []
    original = scan_mod.sha256_file
    monkeypatch.setattr(scan_mod, "sha256_file", lambda p: calls.append(p) or original(p))
    video_factory("c.mp4", seed=3)
    second = {r["filename"]: r["video_id"] for r in scan(settings)}
    assert {k: second[k] for k in first} == first      # id cũ không đổi
    assert second["c.mp4"] == "MV_003"
    assert [p.name for p in calls] == ["c.mp4"]         # chỉ băm file mới


def test_register_source_is_declaration_not_verification(settings, video_factory):
    video_factory("a.mp4")
    register_source(settings, "a.mp4", source="user", license="CC BY 4.0",
                    source_url="https://example.org/mv", title="Bài A (Official MV)")
    assert read_csv(settings.paths.sources_csv)[0]["license_verified"] == "no"
    scan(settings)
    validate(settings)
    row = by_filename(settings)["a.mp4"]
    assert (row["source"], row["license"], row["source_url"], row["title"]) == \
        ("user", "CC BY 4.0", "https://example.org/mv", "Bài A (Official MV)")
    assert row["license_status"] == "unknown" and row["validation_status"] == "pending_review"
    with pytest.raises(FileNotFoundError):
        register_source(settings, "khong_co.mp4", license="x")


def test_splits_deterministic_and_accepted_only(settings, video_factory):
    ids = [f"MV_{i:03d}" for i in range(1, 11)]
    a = assign_splits(ids, (0.7, 0.15, 0.15))
    assert a == assign_splits(list(reversed(ids)), (0.7, 0.15, 0.15))   # không phụ thuộc thứ tự
    counts = {s: list(a.values()).count(s) for s in ("train", "validation", "test")}
    assert counts == {"train": 6, "validation": 2, "test": 2}
    assert set(assign_splits(["a", "b"], (0.7, 0.15, 0.15)).values()) == {"train"}
    for i in range(3):
        video_factory(f"v{i}.mp4", seed=i)
    scan(settings)
    validate(settings)
    total = sum(len(read_csv(settings.paths.splits / f"{s}.csv")) for s in ("train", "validation", "test"))
    assert total == 0   # chưa video nào accepted -> split rỗng


def test_remove_video_moves_to_archive_outside_dataset(settings, video_factory):
    from src.dataset.scan_videos import remove_video
    video_factory("yt_abc.mp4", seed=1)
    video_factory("keep.mp4", seed=2)
    register_source(settings, "yt_abc.mp4", license="CC BY", source="youtube",
                    source_url="https://www.youtube.com/watch?v=abc", title="Long show", author="Kênh A")
    (settings.paths.metadata / "download_archive.txt").write_text("youtube abc\nyoutube zzz\n", encoding="utf-8")
    scan(settings)
    vid = by_filename(settings)["yt_abc.mp4"]["video_id"]
    remove_video(settings, vid, reason="không phải MV 3–5 phút")
    archived = settings.archive_dir / "videos" / "yt_abc.mp4"
    assert archived.exists() and not archived.is_relative_to(settings.paths.root)
    rec = read_csv(settings.archive_dir / "archive_manifest.csv")[0]
    assert (rec["video_id"], rec["license"], rec["source_url"], rec["author"], rec["reason"]) == \
        (vid, "CC BY", "https://www.youtube.com/watch?v=abc", "Kênh A", "không phải MV 3–5 phút")
    assert rec["archived_path"] == "videos/yt_abc.mp4" and len(rec["sha256"]) == 64
    assert [r["filename"] for r in scan(settings)] == ["keep.mp4"]   # không bị đăng ký lại
    assert read_csv(settings.paths.sources_csv) == []
    assert (settings.paths.metadata / "download_archive.txt").read_text(encoding="utf-8") == "youtube zzz\n"
    with pytest.raises(KeyError):
        remove_video(settings, "MV_999")


def test_remove_refuses_to_overwrite_archive(settings, video_factory):
    from src.dataset.scan_videos import remove_video
    video_factory("a.mp4", seed=1)
    scan(settings)
    (settings.archive_dir / "videos").mkdir(parents=True)
    (settings.archive_dir / "videos" / "a.mp4").write_bytes(b"old")
    with pytest.raises(FileExistsError):
        remove_video(settings, "MV_001")
    assert (settings.paths.raw_videos / "a.mp4").exists()        # file gốc còn nguyên
    assert read_csv(settings.paths.videos_csv)                    # metadata chưa bị xóa


def test_settings_defaults_come_from_criteria_file(monkeypatch):
    from config.settings import load_criteria
    for var in ("VI_MIN_VIDEO_DURATION_SEC", "VI_MAX_VIDEO_DURATION_SEC", "VI_MIN_LONG_SIDE_PX", "VI_VIDEO_EXTENSIONS"):
        monkeypatch.delenv(var, raising=False)
    s = Settings()
    c = load_criteria()
    assert (s.min_video_duration_sec, s.max_video_duration_sec) == (150, 420) == \
        (c["duration"]["min_seconds"], c["duration"]["max_seconds"])
    assert (s.target_min_duration_sec, s.target_max_duration_sec) == (180, 300)
    assert s.min_long_side_px == 1280 and s.accepted_extensions == (".mp4",)


def test_make_settings_is_isolated(tmp_path):
    s = make_settings(tmp_path)
    assert s.archive_dir.is_relative_to(tmp_path) and s.dataset_dir.is_relative_to(tmp_path)


# ---------------------------------------------------------------- CLI khi output bị chuyển hướng
@pytest.mark.parametrize("module", ["create_dataset", "scan_videos", "validate_dataset", "download_videos",
                                    "dataset_report", "review_video", "migrate_metadata",
                                    "moderate_videos"])
def test_cli_help_works_when_output_is_piped(module):
    """Windows: stdout bị pipe dùng cp125x -> --help tiếng Việt từng gây UnicodeEncodeError."""
    import subprocess
    import sys
    from pathlib import Path
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
    result = subprocess.run([sys.executable, "-m", f"src.dataset.{module}", "--help"], capture_output=True,
                            cwd=Path(__file__).resolve().parents[1], env=env, timeout=120)
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")[-500:]
    assert "usage" in result.stdout.decode("utf-8")
