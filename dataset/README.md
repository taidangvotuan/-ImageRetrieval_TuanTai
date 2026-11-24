# Dataset – Video Intelligence (domain: Music Video)

Thư mục này được tạo bởi `python -m src.dataset.create_dataset`. Không sửa CSV bằng tay:
mọi file metadata được sinh lại bằng các công cụ trong `src/dataset/`.

## Tiêu chí

| Tiêu chí | Yêu cầu |
|---|---|
| Loại dữ liệu | Chỉ Music Video (`category` thuộc: `music_video`) |
| Định dạng | MP4 (hỗ trợ đọc: .mp4, .avi, .mkv, .mov) |
| Thời lượng / video | 3–5 phút; công cụ chấp nhận 150–330 s, ngoài khoảng là `too_short` / `too_long` |
| Độ phân giải | 720p hoặc 1080p (cạnh dài ≥ 1280 px), thấp hơn là `low_resolution` |
| Quy mô | Mốc 5 → 10 → 30 → 50 MV (5 kiểm tra pipeline, 10 nhiều video, 30 Dataset chính, 50 mở rộng) |
| Lấy mẫu frame | Mặc định 1 frame/giây, thử nghiệm thêm 2 frame/giây |
| Nguồn | Giấy phép rõ ràng: CC BY do chính chủ đăng, nguồn mở, hoặc video tự cung cấp có khai báo |

Không dùng video giả, không lặp video ngắn, không tạo metadata giả. Số lượng MV trong báo cáo
luôn là số thật đếm từ `metadata/videos.csv`.

## Cách đưa MV vào

**Cách 1: tìm MV YouTube giấy phép CC BY, duyệt rồi mới tải.**

```powershell
# 1) Chỉ tìm, không tải -> ghi metadata/download_candidates.csv
python -m src.dataset.download_videos --preset music_video --max-results 30 --dry-run
# 2) Mở download_candidates.csv, kiểm tra từng dòng accepted=yes:
#    kênh có phải của nghệ sĩ / hãng không? có phải MV thật (không phải lyric, reup) không?
#    Chép URL được duyệt vào một file text, mỗi dòng một URL.
# 3) Tải các URL đã duyệt
python -m src.dataset.download_videos --url-file approved_urls.txt
```

Giấy phép CC BY chỉ có giá trị khi người đăng sở hữu nội dung. Công cụ không tự xác minh được
điều này, nên bước 2 là bắt buộc. Bài hát còn có quyền tác giả âm nhạc riêng: chỉ dùng cho phân
tích học thuật, **không phân phối lại file video**.

**Cách 2: video tự cung cấp hoặc được cấp phép.** Chép file vào `dataset/raw/videos/`, rồi khai báo:

```powershell
python -m src.dataset.scan_videos --register-source my_mv.mp4 --source user `
    --license "..." --category music_video --title "..." --author "..." --source-url "..."
```

**Gỡ video:** `python -m src.dataset.scan_videos --remove MV_00X --reason "..."`. File được chuyển
vào `archive/videos/` (ngoài `dataset/`), nguồn và giấy phép được ghi lại trong
`archive/archive_manifest.csv`. Thêm `--delete-file` để xóa hẳn.

Sau mỗi lần thêm hoặc gỡ, chạy:

```powershell
python -m src.dataset.scan_videos        # đo metadata, sha256, gán video_id MV_xxx, chia split
python -m src.dataset.validate_dataset   # lỗi / trùng / thời lượng / độ phân giải / giấy phép
python -m src.dataset.dataset_report     # thống kê + tiến độ theo mốc
```

## Cấu trúc

| Thư mục / file | Nội dung |
|---|---|
| `raw/videos/` | MV gốc, giữ tên file gốc (vd. `yt_<id>.mp4`), **không chỉnh sửa**. Bị `.gitignore` |
| `metadata/videos.csv` | Một dòng/MV: video_id, filename, filepath, title, duration_seconds, fps, total_frames, width, height, file_size, source, license, processing_status, sha256… |
| `metadata/sources.csv` | Nguồn gốc và giấy phép từng file (URL, tác giả, kênh, license) |
| `metadata/dataset_info.json` | Tổng số MV, tổng thời lượng, dung lượng, trạng thái |
| `metadata/validation_report.json` | Kết quả lần validate gần nhất |
| `metadata/download_candidates.csv` | Danh sách ứng viên của lần `--dry-run` gần nhất, chờ duyệt |
| `processed/frames/` | Chỉ frame minh họa cho từng khoảng xuất hiện (không lưu toàn bộ frame) |
| `processed/detections/` | Detection YOLO (Parquet) – Giai đoạn 4 |
| `processed/temporal_segments/` | Khoảng thời gian xuất hiện (Parquet) – Giai đoạn 5 |
| `annotations/labels.csv` | 80 lớp COCO mà YOLO pretrained hỗ trợ; `is_target=yes` là 10 lớp mục tiêu |
| `annotations/ground_truth.csv` | Khoảng xuất hiện **gán nhãn tay** để đánh giá – xem `annotations/annotation_guide.md` |
| `splits/*.csv` | Chia theo video. Đề tài không train mô hình: `validation` để chỉnh `gap_tolerance`/`confidence`, `test` để gán nhãn và đánh giá |

`processing_status`: `pending` → `validated` / `invalid` → `processing` → `detected` → `aggregated`.
