# Video Intelligence – Truy vấn đối tượng và khoảng thời gian xuất hiện trong video

Đồ án môn **Phát triển ứng dụng trên nền tảng dữ liệu lớn**.

Người dùng nhập tên một lớp đối tượng (ví dụ `person`, `car`, `chair`). Hệ thống phân tích một
Dataset **Music Video (MV) 3–5 phút** và trả về: đối tượng có trong MV nào, xuất hiện trong những
khoảng thời gian nào, bao nhiêu lần, và cho phép phát MV tại thời điểm tìm thấy.

Ví dụ minh họa định dạng đầu ra (**không phải kết quả đo được**):

```text
MV_001 – <tiêu đề>        Thời lượng 04:15
  Lần 1: 00:12 – 00:38
  Lần 2: 01:25 – 01:57
```

Timestamp được ước lượng từ frame lấy mẫu (1 hoặc 2 frame/giây), nên biên mỗi khoảng có sai số
tối đa bằng chu kỳ lấy mẫu. Bài toán nhận diện **lớp** đối tượng, không nhận diện danh tính
người (ca sĩ, diễn viên).

**Lớp mục tiêu** (đều thuộc 80 lớp COCO của YOLO): person, car, motorcycle, bus, bicycle, dog,
cat, chair, bottle, backpack. Các lớp COCO khác vẫn truy vấn được. Lớp mà mô hình không hỗ trợ
sẽ được báo rõ.

## Tiến độ

| Giai đoạn | Nội dung | Trạng thái |
|---|---|---|
| 1 | Audit project ([PROJECT_ANALYSIS.md](PROJECT_ANALYSIS.md) §12) | ✅ |
| 2 | Dataset MV: cấu trúc, metadata, kiểm tra, thống kê, công cụ tìm/tải CC BY | ✅ công cụ · ⏳ **chưa có MV nào** |
| 3 | Video processing: lấy mẫu 1/2 fps, timestamp theo PTS, checkpoint | ⏳ |
| 4 | YOLO nano (CPU) → detection Parquet; temporal segment bản pandas | ⏳ |
| 5 | Spark batch + Spark SQL, Parquet/HDFS, benchmark Python vs PySpark | ⏳ |
| 6 | Kafka + Spark Structured Streaming (Docker) | ⏳ |
| 7 | Giao diện Streamlit | ⏳ |
| 8 | Kiểm thử, đánh giá với ground truth gán tay, tài liệu | ⏳ |

Kiến trúc mục tiêu (chi tiết trong PROJECT_ANALYSIS.md §12):

```text
LUỒNG 1 – BATCH (MV đã lưu)
  raw/videos → scan/validate → lấy mẫu frame (PTS) → YOLO nano → detections.parquet
             → Spark (Window/SQL) → temporal_segments.parquet → HDFS
LUỒNG 2 – STREAMING (sự kiện detection, phát lại từ MV đã lưu – không phải video thời gian thực)
  producer → Kafka `vi.detections` → Spark Structured Streaming (checkpoint, khử trùng) → Parquet/HDFS
Streamlit ← đọc Parquet kết quả
```

## Cấu trúc thư mục

```text
VideoRetrieval-TuanTai/
├── config/
│   ├── settings.py             # cấu hình tập trung (biến VI_* trong .env)
│   └── music_presets.json      # preset tìm MV CC BY + từ khóa loại trừ
├── src/
│   ├── common/                 # ghi file atomic, sha256, CSV, logging
│   ├── dataset/                # công cụ quản lý Dataset (giai đoạn 2)
│   │   ├── create_dataset.py   # khởi tạo cấu trúc
│   │   ├── scan_videos.py      # quét MV, đo metadata, khai báo nguồn, gỡ video
│   │   ├── validate_dataset.py # lỗi, trùng lặp, thời lượng, độ phân giải, giấy phép
│   │   ├── download_videos.py  # tìm/tải MV YouTube giấy phép CC BY (yt-dlp)
│   │   ├── dataset_report.py   # thống kê + tiến độ theo mốc 5/10/30/50
│   │   └── schema.py           # cột metadata, 80 lớp COCO, 10 lớp mục tiêu
│   └── video_processing/
│       └── video_probe.py      # metadata + đọc thử video (OpenCV + FFmpeg)
├── dataset/                    # DỮ LIỆU – xem dataset/README.md
├── archive/                    # video đã gỡ khỏi dataset (gitignore) + archive_manifest.csv
├── tests/                      # pytest
├── legacy/                     # project ImageRetrieval cũ (giữ để tham khảo)
├── requirements.txt / requirements-dev.txt
├── .env.example
└── PROJECT_ANALYSIS.md
```

Các thư mục `app/`, `src/detection/`, `src/big_data/`, `src/streaming/`, `src/storage/`,
`scripts/`, `docker/` sẽ được tạo ở giai đoạn tương ứng, không tạo module rỗng trước.

## 1. Cài đặt

Yêu cầu: Windows 10/11, **Python 3.11**. FFmpeg **không cần cài riêng** vì gói `imageio-ffmpeg`
đã kèm sẵn binary.

```powershell
py -3.11 -m venv .venv-vi
.venv-vi\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env      # tùy chọn, các giá trị mặc định đã dùng được
```

Mọi lệnh bên dưới chạy **từ thư mục gốc repo** với `.venv-vi` đã kích hoạt.

> `python` mặc định trên máy là 3.14. Luôn kích hoạt `.venv-vi` (Python 3.11) trước khi chạy,
> vì torch, ultralytics và PySpark ở các giai đoạn sau cần phiên bản đã có wheel ổn định.

## 2. Dataset Music Video

| Tiêu chí | Yêu cầu | Cấu hình |
|---|---|---|
| Loại | Chỉ Music Video | `VI_ALLOWED_CATEGORIES=music_video` |
| Định dạng | MP4 | |
| Thời lượng | 3–5 phút, công cụ chấp nhận 150–330 s | `VI_MIN/MAX_VIDEO_DURATION_SEC` |
| Độ phân giải | 720p hoặc 1080p (cạnh dài ≥ 1280 px) | `VI_MIN_LONG_SIDE_PX` |
| Quy mô | 5 → 10 → 30 (chính) → 50 (mở rộng) | `VI_TARGET_MILESTONES` |
| Lấy mẫu | 1 frame/giây, thử nghiệm 2 frame/giây | (giai đoạn 3) |
| `video_id` | `MV_001`, `MV_002`, … gán một lần; file giữ tên gốc `yt_<id>.mp4` | `VI_VIDEO_ID_PREFIX` |

Ngoài khoảng thời lượng → `too_short` / `too_long`. Dưới 720p → `low_resolution`. Đây là cảnh báo,
thành lỗi khi `validate_dataset --strict`. Ước tính với 30 MV × 4 phút: khoảng 7.200 frame @1 fps
và 14.400 frame @2 fps. Đây là **ước tính**, số frame thật được thống kê sau khi trích xuất.

Không dùng video giả, không lặp video ngắn, không tạo metadata giả. Số MV trong báo cáo luôn là
số thật đếm từ `metadata/videos.csv`.

### 2.1. Khởi tạo (làm được khi chưa có video)

```powershell
python -m src.dataset.create_dataset
```

### 2.2. Đưa MV vào

**Cách 1: tìm MV YouTube giấy phép CC BY, duyệt tay rồi mới tải.**

```powershell
# 1) Chỉ tìm, KHÔNG tải. Kết quả ghi vào dataset/metadata/download_candidates.csv
python -m src.dataset.download_videos --preset music_video --max-results 30 --dry-run
# 2) Mở download_candidates.csv. Với mỗi dòng accepted=yes, kiểm tra:
#    - kênh có phải của nghệ sĩ / hãng phát hành không (không phải kênh reup)?
#    - có đúng là MV (có hình quay) không, không phải lyric/audio?
#    Chép URL đã duyệt vào approved_urls.txt, mỗi dòng một URL.
# 3) Tải các URL đã duyệt
python -m src.dataset.download_videos --url-file approved_urls.txt
```

Quy tắc của công cụ:

- Đọc metadata trước khi tải. Chỉ tải khi `license` là **CC BY thuần**. NC/SA/ND,
  "Standard YouTube License" hoặc thiếu thông tin đều bị **từ chối**. Video công khai trên YouTube
  không mặc định được phép tải.
- Chỉ nhận video dài 150–330 s và không đang livestream.
- Loại tiêu đề chứa từ khóa như lyric, audio, visualizer, 1 hour, loop, slowed, karaoke, teaser…
  (`exclude_keywords` trong preset; thêm bằng `--exclude-keyword`). So khớp theo từ, không dấu.
- Giấy phép CC BY chỉ có giá trị khi người đăng sở hữu nội dung. Công cụ không tự xác minh được
  điều này, nên **bước duyệt tay là bắt buộc**. Có thể giới hạn kênh bằng `--trusted-channel ID`.
- Tải tối đa 720p (`--max-height 1080` nếu cần), ưu tiên H.264 để trình duyệt/Streamlit phát được.
  File lưu thành `raw/videos/yt_<id>.mp4`.
- URL, tiêu đề, kênh, `channel_id` và giấy phép được ghi vào `metadata/sources.csv`. CC BY yêu
  cầu ghi công tác giả khi sử dụng.
- Bài hát còn có quyền tác giả âm nhạc riêng: chỉ dùng cho phân tích học thuật, **không phân phối
  lại file video** (video bị `.gitignore`, chỉ commit metadata).
- `metadata/download_archive.txt` giúp chạy lại không tải trùng và tiếp tục được khi bị ngắt.

**Cách 2: MV tự cung cấp hoặc được cấp phép.** Chép file vào `dataset/raw/videos/`, rồi khai báo
nguồn (không cần sửa CSV bằng tay):

```powershell
python -m src.dataset.scan_videos --register-source my_mv.mp4 --source user `
    --license "..." --category music_video --title "..." --author "..." --source-url "..."
```

**Gỡ video khỏi dataset:**

```powershell
python -m src.dataset.scan_videos --remove MV_005 --reason "reup, không phải kênh chính chủ"
python -m src.dataset.scan_videos --remove MV_005 --delete-file   # xóa hẳn file
```

File được chuyển vào `archive/videos/` (ngoài `dataset/`), không bao giờ ghi đè file đã có.
Metadata, nguồn và giấy phép được ghi lại trong `archive/archive_manifest.csv` để truy vết.

### 2.3. Quét, kiểm tra, báo cáo

```powershell
python -m src.dataset.scan_videos         # đo metadata, sha256, gán video_id MV_xxx, chia split
python -m src.dataset.validate_dataset    # exit code 1 nếu có lỗi
python -m src.dataset.dataset_report      # thống kê + tiến độ mốc -> metadata/dataset_report.md
```

`scan_videos`:

- Chạy tăng dần: file không đổi (cùng dung lượng và mtime) thì không băm lại.
- `videos.csv` được ghi sau mỗi video, nên có thể tiếp tục khi bị ngắt.
- `video_id` được gán một lần và không đổi.
- Split train/validation/test (70/15/15) theo hash `video_id`, cố định giữa các lần chạy. Đề tài
  không train mô hình: `validation` dùng để chỉnh `gap_tolerance`/`confidence`, `test` dùng để gán
  nhãn ground truth và đánh giá.

`validate_dataset` phát hiện:

| Mã | Mức | Ý nghĩa |
|---|---|---|
| `unreadable` / `corrupt` | lỗi | Không mở được, file rỗng, không giải mã được frame đầu/giữa/cuối |
| `duplicate_content` | lỗi | Trùng nội dung (sha256) với video khác |
| `duplicate_id` | lỗi | Trùng `video_id` hoặc `filepath` |
| `missing_file` | lỗi | Sai đường dẫn hoặc file đã bị xóa/di chuyển |
| `missing_metadata` / `missing_metadata_columns` | lỗi | Thiếu trường hoặc giá trị không hợp lệ (FPS = 0, …) |
| `unregistered_file` | lỗi | Video có trên đĩa nhưng chưa có metadata |
| `metadata_stale` | lỗi | File đã thay đổi so với metadata, cần chạy lại scan |
| `too_short` / `too_long` | cảnh báo | Ngoài khoảng 150–330 s |
| `low_resolution` | cảnh báo | Dưới 720p (cạnh dài < 1280 px) |
| `missing_license` | cảnh báo | Chưa khai báo giấy phép |
| `out_of_domain` | cảnh báo | `category` không phải `music_video` (hoặc chưa khai báo) |
| `probe_warning` | cảnh báo | Thời lượng OpenCV lệch container (có thể là VFR) |

`--strict` coi cảnh báo là lỗi. `--quick` bỏ bước giải mã thử frame.

### 2.4. Hiện trạng Dataset (2026-11-24)

**Dataset có 0 MV. Chưa đạt mốc nào** (mốc đầu tiên là 5 MV).

10 video thu thập theo định hướng cũ (8 video tổng hợp *Anh Trai Vượt Ngàn Chông Gai* và 2 concert,
dài 23–50 phút) **không đạt tiêu chí MV 3–5 phút**. Cả 10 đã được gỡ khỏi dataset và chuyển vào
`archive/videos/`, cùng một video walking tour gỡ trước đó. Nguồn và giấy phép của chúng được giữ
trong `archive/archive_manifest.csv`. Các video này không được dùng trong bất kỳ số liệu nào của
đề tài MV.

Tình trạng mới nhất nằm trong `dataset/metadata/dataset_report.md`.

### 2.5. Ground truth

Quy trình gán nhãn tay nằm trong
[dataset/annotations/annotation_guide.md](dataset/annotations/annotation_guide.md). Nhãn **không
bao giờ** được sinh từ kết quả YOLO.

## 3. Kiểm thử

```powershell
python -m pytest
```

Có 54 test, không cần mạng và không cần video thật. Video vài giây dùng trong test được sinh tạm
bằng OpenCV trong thư mục tạm của pytest, **không** ghi vào `dataset/` hay `archive/` và không dùng
để báo cáo kết quả. Phạm vi: dataset rỗng, metadata, quá ngắn/quá dài, độ phân giải thấp, file hỏng
/rỗng/cắt cụt, trùng lặp, sai đường dẫn, chưa đăng ký, thiếu trường/cột, metadata cũ, quét tăng dần,
khai báo nguồn, split, báo cáo và mốc, CLI khi output bị chuyển hướng, domain MV, gỡ video vào archive (không ghi đè), phân loại giấy
phép, khoảng thời lượng khi tải, từ khóa loại trừ, kênh tin cậy, dry-run ghi danh sách ứng viên, lỗi
mạng.

## 4. Môi trường đã kiểm tra

| Thành phần | Phiên bản trên máy | Dùng ở giai đoạn |
|---|---|---|
| Python (`.venv-vi`) | 3.11.9 | tất cả |
| OpenCV (headless) | 5.1.0 | 2–4 |
| FFmpeg (imageio-ffmpeg) | 9.0.2, không kèm `ffprobe` | 2–3 |
| yt-dlp | 2026.10.26 | 2 |
| Java | 17.0.20.1 | 5–6 |
| Apache Spark | 4.2.0 (`SPARK_HOME`), chưa có jar `spark-sql-kafka` | 5–6 |
| Docker | 29.9.3, **phương án chính** cho Kafka và HDFS (cần bật Docker Desktop) | 5–6 |
| Apache Hadoop | 3.4.0 native (`HADOOP_HOME`, có winutils), dự phòng | 5 |
| Apache Kafka | 4.3.1 native (KRaft), dự phòng | 6 |

Rủi ro và kế hoạch chi tiết xem trong [PROJECT_ANALYSIS.md](PROJECT_ANALYSIS.md).

## 5. Legacy

Project ImageRetrieval cũ (truy vấn ảnh Castle5000: Qwen3-VL, GroundingDINO, SAM2, FastAPI,
React) đã được chuyển nguyên vẹn vào [legacy/](legacy/). Lịch sử git vẫn được giữ. Xem
[legacy/README.md](legacy/README.md). File `.env` cũ, chứa token Kaggle, nằm tại `legacy/.env` và
không được commit.
