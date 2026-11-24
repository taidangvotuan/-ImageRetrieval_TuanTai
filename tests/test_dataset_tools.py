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


def codes(report, level=None):
    return {i["code"] for i in report["issues"] if level is None or i["level"] == level}


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
    for name in ("train", "validation", "test"):
        assert (p.splits / f"{name}.csv").exists()
    assert (p.annotations / "ground_truth.csv").exists()
    info = read_json(p.dataset_info)
    assert info["total_videos"] == 0 and info["total_duration_seconds"] == 0
    assert (p.root / "README.md").read_text(encoding="utf-8").startswith("# Dataset")


def test_create_dataset_is_idempotent(settings):
    create_dataset(settings)
    readme = settings.paths.root / "README.md"
    readme.write_text("custom", encoding="utf-8")
    created_at = read_json(settings.paths.dataset_info)["created_at"]
    create_dataset(settings)
    assert readme.read_text(encoding="utf-8") == "custom"
    assert read_json(settings.paths.dataset_info)["created_at"] == created_at


def test_tools_succeed_on_empty_dataset(settings):
    assert scan(settings) == []
    report = validate(settings)
    assert report["ok"] and report["total_videos"] == 0
    stats, md = build_report(settings)
    assert stats["total_videos"] == 0 and "chưa có video" in md


def test_main_entrypoints_on_empty_dataset(tmp_path):
    from src.dataset import create_dataset as c, dataset_report as r, validate_dataset as v
    args = ["--dataset-dir", str(tmp_path / "ds")]
    assert c.main(args) == 0
    assert scan_mod.main(args) == 0
    assert v.main(args) == 0
    assert r.main(args) == 0


# ---------------------------------------------------------------- video hợp lệ
def test_valid_video_metadata(settings, video_factory):
    video_factory("a.mp4", seconds=3, fps=10)
    rows = scan(settings)
    assert len(rows) == 1
    row = rows[0]
    assert row["video_id"] == "video_001"
    assert row["filepath"] == "raw/videos/a.mp4"  # tương đối, không tuyệt đối
    assert float(row["fps"]) == pytest.approx(10)
    assert int(row["total_frames"]) == 30
    assert (int(row["width"]), int(row["height"])) == (64, 48)
    assert float(row["duration_seconds"]) == pytest.approx(3, abs=0.2)
    assert row["processing_status"] == "pending" and len(row["sha256"]) == 64

    report = validate(settings)
    assert report["ok"], report["issues"]
    assert "missing_license" in codes(report, "warning")
    assert read_csv(settings.paths.videos_csv)[0]["processing_status"] == "validated"


def test_too_short_is_warning_and_strict_error(tmp_path):
    from tests.conftest import make_video
    s = Settings(dataset_dir=tmp_path / "ds", log_dir=tmp_path / "l", min_video_duration_sec=600)
    make_video(s.paths.raw_videos / "short.mp4", seconds=2)
    scan(s)
    report = validate(s)
    assert report["ok"] and "too_short" in codes(report, "warning")
    strict = validate(s, strict=True)
    assert not strict["ok"] and "too_short" in codes(strict, "error")
    assert read_csv(s.paths.videos_csv)[0]["processing_status"] == "invalid"


# ---------------------------------------------------------------- video lỗi
def test_corrupt_random_bytes(settings):
    path = settings.paths.raw_videos / "broken.mp4"
    path.parent.mkdir(parents=True)
    path.write_bytes(os.urandom(4096))
    rows = scan(settings)
    assert rows[0]["processing_status"] == "invalid"
    report = validate(settings)
    assert not report["ok"] and codes(report, "error") & {"unreadable", "corrupt", "missing_metadata"}


def test_empty_file_unreadable(settings):
    path = settings.paths.raw_videos / "empty.mp4"
    path.parent.mkdir(parents=True)
    path.touch()
    assert not probe_video(path).readable
    scan(settings)
    assert "unreadable" in codes(validate(settings), "error")


def test_truncated_video(settings, video_factory):
    path = video_factory("t.mp4", seconds=3)
    data = path.read_bytes()
    path.write_bytes(data[: len(data) // 2])  # mp4v: moov atom ở cuối -> mất
    probe = probe_video(path)
    assert not probe.readable and probe.errors


def test_unsupported_file_is_reported(settings, video_factory):
    video_factory("a.mp4")
    (settings.paths.raw_videos / "notes.txt").write_text("x")
    scan(settings)
    report = validate(settings)
    assert "unsupported_format" in codes(report, "warning")


# ---------------------------------------------------------------- trùng lặp, đường dẫn, metadata
def test_duplicate_content_detected(settings, video_factory):
    a = video_factory("a.mp4", seed=1)
    shutil.copy(a, settings.paths.raw_videos / "b_copy.mp4")
    rows = {r["filename"]: r for r in scan(settings)}
    assert rows["a.mp4"]["processing_status"] == "pending"
    assert rows["b_copy.mp4"]["processing_status"] == "invalid"
    assert "video_001" in rows["b_copy.mp4"]["notes"]
    assert "duplicate_content" in codes(validate(settings), "error")
    # Xóa bản gốc -> bản sao không còn bị coi là trùng ở lần quét sau.
    a.unlink()
    rows = {r["filename"]: r for r in scan(settings)}
    assert rows["b_copy.mp4"]["processing_status"] == "pending"


def test_missing_file_wrong_path(settings, video_factory):
    path = video_factory("a.mp4")
    scan(settings)
    path.unlink()
    report = validate(settings)
    assert "missing_file" in codes(report, "error")
    rows = scan(settings)  # không xóa âm thầm dòng metadata
    assert rows[0]["processing_status"] == "invalid" and "không tồn tại" in rows[0]["notes"]


def test_unregistered_file(settings, video_factory):
    video_factory("a.mp4", seed=1)
    scan(settings)
    video_factory("new.mp4", seed=2)
    assert "unregistered_file" in codes(validate(settings), "error")


def test_missing_metadata_field(settings, video_factory):
    video_factory("a.mp4")
    scan(settings)
    rows = read_csv(settings.paths.videos_csv)
    rows[0]["fps"] = ""
    write_csv(settings.paths.videos_csv, VIDEO_COLUMNS, rows)
    report = validate(settings, quick=True)
    assert "missing_metadata" in codes(report, "error")
    assert read_csv(settings.paths.videos_csv)[0]["processing_status"] == "invalid"


def test_missing_metadata_columns(settings, video_factory):
    video_factory("a.mp4")
    scan(settings)
    write_csv(settings.paths.videos_csv, ["video_id", "filename"], read_csv(settings.paths.videos_csv))
    assert "missing_metadata_columns" in codes(validate(settings, quick=True, update_status=False), "error")


def test_stale_metadata(settings, video_factory):
    path = video_factory("a.mp4", seconds=2, seed=1)
    scan(settings)
    tmp = settings.paths.root / "x.mp4"
    from tests.conftest import make_video
    make_video(tmp, seconds=4, seed=5)
    shutil.move(tmp, path)
    assert "metadata_stale" in codes(validate(settings, quick=True), "error")


# ---------------------------------------------------------------- incremental / resume
def test_incremental_scan_keeps_ids_and_skips_rehash(settings, video_factory, monkeypatch):
    video_factory("b.mp4", seed=1)
    video_factory("a.mp4", seed=2)
    first = {r["filename"]: r["video_id"] for r in scan(settings)}
    assert sorted(first.values()) == ["video_001", "video_002"]

    calls = []
    original = scan_mod.sha256_file
    monkeypatch.setattr(scan_mod, "sha256_file", lambda p: calls.append(p) or original(p))
    video_factory("c.mp4", seed=3)
    second = {r["filename"]: r["video_id"] for r in scan(settings)}
    assert {k: second[k] for k in first} == first      # id cũ không đổi
    assert second["c.mp4"] == "video_003"
    assert [p.name for p in calls] == ["c.mp4"]         # chỉ băm file mới


def test_register_source_sets_license(settings, video_factory):
    video_factory("a.mp4")
    register_source(settings, "a.mp4", source="user", license="CC BY 4.0", license_verified="yes")
    row = scan(settings)[0]
    assert (row["source"], row["license"]) == ("user", "CC BY 4.0")
    assert "missing_license" not in codes(validate(settings))
    with pytest.raises(FileNotFoundError):
        register_source(settings, "khong_co.mp4", license="x")


def test_splits_deterministic(settings, video_factory):
    ids = [f"video_{i:03d}" for i in range(1, 11)]
    a = assign_splits(ids, (0.7, 0.15, 0.15))
    assert a == assign_splits(list(reversed(ids)), (0.7, 0.15, 0.15))   # không phụ thuộc thứ tự
    counts = {s: list(a.values()).count(s) for s in ("train", "validation", "test")}
    assert counts == {"train": 6, "validation": 2, "test": 2}   # round(1.5) = 2 cho val/test
    assert set(assign_splits(["a", "b"], (0.7, 0.15, 0.15)).values()) == {"train"}
    for i in range(3):
        video_factory(f"v{i}.mp4", seed=i)
    scan(settings)
    total = sum(len(read_csv(settings.paths.splits / f"{s}.csv")) for s in ("train", "validation", "test"))
    assert total == 3


def test_report_counts(settings, video_factory):
    video_factory("a.mp4", seconds=3, seed=1)
    video_factory("b.mp4", seconds=3, seed=2)
    scan(settings)
    validate(settings)
    stats, md = build_report(settings)
    assert stats["total_videos"] == 2 and stats["valid_videos"] == 2
    assert stats["formats"] == {".mp4": 2}
    assert "video_002" in md and settings.paths.report_md.exists()


# ---------------------------------------------------------------- domain ca nhạc
def test_out_of_domain_warning(settings, video_factory):
    video_factory("walk.mp4", seed=1)
    video_factory("show.mp4", seed=2)
    register_source(settings, "show.mp4", source="youtube", license="CC BY", category="music_show",
                    series="Anh Trai Vượt Ngàn Chông Gai")
    register_source(settings, "walk.mp4", source="youtube", license="CC BY", category="walking_tour")
    rows = {r["filename"]: r for r in scan(settings)}
    assert rows["show.mp4"]["series"] == "Anh Trai Vượt Ngàn Chông Gai"
    report = validate(settings)
    flagged = {i["filename"] for i in report["issues"] if i["code"] == "out_of_domain"}
    assert flagged == {"walk.mp4"} and report["ok"]
    strict = validate(settings, strict=True)
    status = {r["filename"]: r["processing_status"] for r in read_csv(settings.paths.videos_csv)}
    assert not strict["ok"] and status == {"walk.mp4": "invalid", "show.mp4": "validated"}
    stats, md = build_report(settings)
    assert stats["out_of_domain"] == 1 and "Anh Trai Vượt Ngàn Chông Gai" in md


def test_no_domain_restriction(tmp_path):
    from tests.conftest import make_video
    s = Settings(dataset_dir=tmp_path / "ds", log_dir=tmp_path / "l", min_video_duration_sec=1,
                 allowed_categories=())
    make_video(s.paths.raw_videos / "a.mp4")
    scan(s)
    assert "out_of_domain" not in codes(validate(s))


def test_remove_video(settings, video_factory):
    from src.dataset.scan_videos import remove_video
    video_factory("yt_abc.mp4", seed=1)
    video_factory("keep.mp4", seed=2)
    register_source(settings, "yt_abc.mp4", license="CC BY")
    (settings.paths.metadata / "download_archive.txt").write_text("youtube abc\nyoutube zzz\n", encoding="utf-8")
    scan(settings)
    vid = next(r["video_id"] for r in read_csv(settings.paths.videos_csv) if r["filename"] == "yt_abc.mp4")
    remove_video(settings, vid)
    assert (settings.paths.root / "raw" / "removed" / "yt_abc.mp4").exists()
    assert [r["filename"] for r in scan(settings)] == ["keep.mp4"]   # không bị đăng ký lại
    assert read_csv(settings.paths.sources_csv) == []
    assert (settings.paths.metadata / "download_archive.txt").read_text(encoding="utf-8") == "youtube zzz\n"
    with pytest.raises(KeyError):
        remove_video(settings, "video_999")
