"""Migration schema v1 -> v2: sao lưu, không mất dữ liệu, không tự sinh 'confirmed', idempotent."""
from __future__ import annotations

import json

from src.common.io_utils import read_csv, read_csv_header, write_csv
from src.dataset.migrate_metadata import migrate, needs_migration
from src.dataset.scan_videos import scan
from src.dataset.schema import VERIFICATION_FIELDS, VIDEO_COLUMNS
from tests.conftest import make_video

V1_COLUMNS = ["video_id", "filename", "title", "filepath", "duration_seconds", "fps", "total_frames", "width",
              "height", "file_size", "source", "license", "processing_status", "category", "series", "codec",
              "container_duration_seconds", "sha256", "file_mtime", "scanned_at", "notes"]


def make_v1(settings, video_path=None):
    m = settings.paths.metadata
    m.mkdir(parents=True, exist_ok=True)
    rows = [
        {"video_id": "MV_001", "filename": "a.mp4", "title": "A (Official MV)", "filepath": "raw/videos/a.mp4",
         "duration_seconds": "240", "fps": "25", "total_frames": "6000", "width": "1280", "height": "720",
         "file_size": "123", "source": "youtube", "license": "CC BY", "processing_status": "validated",
         "category": "music_video", "sha256": "abc", "notes": "ghi chú cũ"},
        {"video_id": "MV_002", "filename": "b.mp4", "title": "B", "filepath": "raw/videos/b.mp4",
         "duration_seconds": "60", "processing_status": "invalid", "sha256": "def"},
    ]
    write_csv(settings.paths.videos_csv, V1_COLUMNS, rows)
    write_csv(settings.paths.sources_csv, ["filename", "source", "license", "license_verified"],
              [{"filename": "a.mp4", "source": "youtube", "license": "CC BY", "license_verified": "yes"}])
    (m / "validation_report.json").write_text('{"old": true}', encoding="utf-8")
    (m / "dataset_report.md").write_text("# báo cáo cũ", encoding="utf-8")
    settings.paths.dataset_info.write_text(json.dumps({"name": "x", "created_at": "2026-11-23T00:00:00+00:00"}),
                                           encoding="utf-8")
    return rows


def test_v1_metadata_is_backed_up_and_upgraded(settings):
    old_rows = make_v1(settings)
    assert needs_migration(settings)
    summary = migrate(settings)
    assert summary["migrated"] and summary["from_version"] == 1 and summary["to_version"] == 3

    backups = list(settings.paths.backups.iterdir())
    assert len(backups) == 1
    for name in ("videos.csv", "sources.csv", "validation_report.json", "dataset_report.md", "dataset_info.json"):
        assert (backups[0] / name).exists(), name
    assert read_csv_header(backups[0] / "videos.csv") == V1_COLUMNS           # bản sao nguyên vẹn

    rows = read_csv(settings.paths.videos_csv)
    assert read_csv_header(settings.paths.videos_csv)[:len(VIDEO_COLUMNS)] == VIDEO_COLUMNS
    assert len(rows) == len(old_rows)                                          # không mất dòng nào
    for new, old in zip(rows, old_rows):
        for key, value in old.items():
            if key != "processing_status":
                assert new[key] == value, key                                  # giữ nguyên giá trị cũ
        assert new["processing_status"] == "not_started"
        assert new["validation_status"] == "pending_review"
        assert new["content_type"] == "unknown"
        for f in VERIFICATION_FIELDS:
            assert new[f] == "unknown"                                         # không suy ra 'confirmed'
    assert "confirmed" not in settings.paths.videos_csv.read_text(encoding="utf-8")

    assert not (settings.paths.metadata / "validation_report.json").exists()   # báo cáo cũ -> backup
    info = json.loads(settings.paths.dataset_info.read_text(encoding="utf-8"))
    assert info["schema_version"] == 3 and info["created_at"] == "2026-11-23T00:00:00+00:00"
    assert read_csv(settings.paths.sources_csv)[0]["license_verified"] == "yes"  # giữ khai báo cũ (không xóa)


def test_migration_is_idempotent(settings):
    make_v1(settings)
    migrate(settings)
    before = settings.paths.videos_csv.read_text(encoding="utf-8")
    assert not needs_migration(settings)
    assert migrate(settings)["migrated"] is False
    assert len(list(settings.paths.backups.iterdir())) == 1
    assert settings.paths.videos_csv.read_text(encoding="utf-8") == before


def test_dry_run_writes_nothing(settings):
    make_v1(settings)
    before = settings.paths.videos_csv.read_text(encoding="utf-8")
    summary = migrate(settings, dry_run=True)
    assert summary["migrated"] is False and "content_type" in summary["added_columns"]
    assert settings.paths.videos_csv.read_text(encoding="utf-8") == before and not settings.paths.backups.exists()


def test_scan_auto_migrates_old_metadata_and_keeps_ids(settings):
    make_v1(settings)
    make_video(settings.paths.raw_videos / "a.mp4", seed=1)
    rows = {r["filename"]: r for r in scan(settings)}
    assert rows["a.mp4"]["video_id"] == "MV_001"                   # id cũ được giữ
    assert settings.paths.backups.exists()
    assert read_csv_header(settings.paths.videos_csv) == VIDEO_COLUMNS
    # Sau scan, dataset_info vẫn ghi schema_version -> không migrate lặp lại.
    assert not needs_migration(settings)
