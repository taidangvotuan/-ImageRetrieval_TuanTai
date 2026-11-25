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
| 1 | Audit project ([PROJECT_ANALYSIS.md](PROJECT_ANALYSIS.md) §12–13) | ✅ |
| 2 | Dataset MV: tiêu chí tập trung, validator, duyệt tay, migration, báo cáo, cổng accepted | ✅ công cụ · ⏳ **chưa có MV nào** |
| 3 | Video processing: lấy mẫu 1/2 fps, timestamp theo PTS, checkpoint | ⏳ |
| 4 | YOLO nano (CPU) → detection Parquet; temporal segment bản pandas | ⏳ |
| 5 | Spark batch + Spark SQL, Parquet/HDFS, benchmark Python vs PySpark | ⏳ |
| 6 | Kafka + Spark Structured Streaming (Docker) | ⏳ |
| 7 | Giao diện Streamlit | ⏳ |
| 8 | Kiểm thử, đánh giá với ground truth gán tay, tài liệu | ⏳ |

Kiến trúc mục tiêu (chi tiết trong PROJECT_ANALYSIS.md §12–13):

```text
                 config/dataset_criteria.json  (tiêu chí DUY NHẤT)
                               │
raw/videos → scan → validate (luật + kỹ thuật) → review_video (người duyệt) → accepted / rejected / pending_review
                                                                                   │
                       accepted_registry  ◄────────── CỔNG: mọi bước sau chỉ nhận video accepted
                               │
LUỒNG 1 – BATCH:  lấy mẫu frame (PTS) → YOLO nano → detections.parquet → Spark (Window/SQL) → segments → HDFS
LUỒNG 2 – STREAMING (sự kiện detection phát lại từ MV đã lưu – không phải video thời gian thực):
                  producer → Kafka `vi.detections` → Spark Structured Streaming → Parquet/HDFS
Streamlit ← đọc Parquet, lọc theo accepted_ids
```

## Cấu trúc thư mục

```text
VideoRetrieval-TuanTai/
├── config/
│   ├── settings.py             # cấu hình tập trung; ngưỡng đọc từ dataset_criteria.json
│   ├── dataset_criteria.json   # TIÊU CHÍ DATASET DUY NHẤT: thời lượng, MP4, 720p, luật loại trừ, an toàn nội dung
│   ├── moderation/lexicons/    # từ nhạy cảm (CSV / ngôn ngữ) – nơi DUY NHẤT chứa chúng
│   └── music_presets.json      # truy vấn tìm MV CC BY (không chứa tiêu chí)
├── src/
│   ├── common/                 # ghi file atomic, sha256, CSV, logging
│   ├── dataset/
│   │   ├── validation/         # kiểm tra tập trung
│   │   │   ├── exclusion_rules.py    # chuẩn hóa + so khớp luật (strong / weak / ngữ cảnh)
│   │   │   ├── video_filter.py       # content_type + trạng thái nội dung từ metadata nguồn
│   │   │   ├── metadata_validator.py # file, MP4, thời lượng, độ phân giải, trùng lặp
│   │   │   ├── review_store.py       # quyết định duyệt tay (nguồn duy nhất của "confirmed")
│   │   │   ├── content_safety/       # kiểm duyệt nội dung: biến thể, ngôn ngữ, lexicon, gộp kết quả
│   │   │   ├── dataset_validator.py  # kết luận accepted / rejected / pending_review
│   │   │   └── accepted_registry.py  # CỔNG cho GĐ3–7
│   │   ├── create_dataset.py   # khởi tạo cấu trúc
│   │   ├── scan_videos.py      # quét MV, đo metadata, khai báo nguồn, gỡ video
│   │   ├── validate_dataset.py # CLI kiểm tra
│   │   ├── review_video.py     # CLI duyệt tay (phiếu duyệt hoặc từng video)
│   │   ├── migrate_metadata.py # nâng schema metadata (có sao lưu)
│   │   ├── moderate_videos.py  # CLI kiểm duyệt nặng: phụ đề, OCR (có checkpoint)
│   │   ├── download_videos.py  # tìm/tải MV YouTube giấy phép CC BY (yt-dlp)
│   │   ├── dataset_report.py   # báo cáo JSON / CSV / MD
│   │   └── schema.py           # cột metadata, giá trị trạng thái, mã lý do, 80 lớp COCO
│   └── video_processing/
│       ├── video_probe.py      # metadata + đọc thử video (OpenCV + FFmpeg)
│       └── frame_sampler.py    # lấy mẫu frame theo PTS, đọc tuần tự (kiểm duyệt + GĐ3)
├── dataset/                    # DỮ LIỆU – xem dataset/README.md
├── docs/DATASET_CRITERIA.md    # tiêu chí, cách so khớp, quy trình duyệt, hợp đồng cổng
├── docs/CONTENT_MODERATION.md  # an toàn nội dung: nhóm vi phạm, phương pháp, giới hạn, duyệt tay
├── archive/                    # video đã gỡ khỏi dataset (gitignore) + archive_manifest.csv
├── tests/                      # pytest
├── legacy/                     # project ImageRetrieval cũ (giữ để tham khảo)
├── requirements.txt / requirements-dev.txt / requirements-moderation.txt (torch CPU + EasyOCR)
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
Copy-Item .env.example .env      # tùy chọn; tiêu chí Dataset nằm trong config/dataset_criteria.json
```

Mọi lệnh bên dưới chạy **từ thư mục gốc repo** với `.venv-vi` đã kích hoạt.

> `python` mặc định trên máy là 3.14. Luôn kích hoạt `.venv-vi` (Python 3.11) trước khi chạy,
> vì torch, ultralytics và PySpark ở các giai đoạn sau cần phiên bản đã có wheel ổn định.

## 2. Dataset Music Video

Tiêu chí chi tiết: [docs/DATASET_CRITERIA.md](docs/DATASET_CRITERIA.md). Giá trị nằm trong
[config/dataset_criteria.json](config/dataset_criteria.json) – nguồn **duy nhất**, mọi module dùng chung.

| Tiêu chí | Yêu cầu | Khi vi phạm |
|---|---|---|
| Loại | Music Video **chính thức**, hoàn chỉnh, quay thực tế | |
| Thời lượng | Luật cứng **150–420 s** (2:30–7:00); mục tiêu 3–5 phút chỉ để thống kê | `rejected: duration_out_of_range` |
| Định dạng | MP4 (không tự chuyển đổi) | `rejected: not_mp4` |
| Độ phân giải | ≥ 720p (cạnh dài ≥ 1280 px) | `rejected: low_resolution` |
| Loại trừ | AI-generated, hậu trường, lyric video, teaser/trailer, reaction/review, tổng hợp, sân khấu/live, audio/visualizer, fan made/reupload, bản cover | `rejected: <nhóm>` |
| Trùng lặp | sha256 / tên file / nguồn | `rejected: duplicate_*`, **không xóa file** |
| Nguồn, giấy phép | Hợp pháp, **được người duyệt xác minh** | `pending_review` tới khi xác minh |
| An toàn nội dung | Không tục tĩu/xúc phạm, bạo lực/máu me, kích động bạo lực, khiêu dâm; không giới hạn tuổi 18+ ([docs/CONTENT_MODERATION.md](docs/CONTENT_MODERATION.md)) | `rejected: <nhóm>` / `pending_review` |
| Quy mô | 5 → 10 → 30 (chính) → 50 (mở rộng) MV **accepted** | |
| `video_id` | `MV_001`, … gán một lần; file giữ tên gốc `yt_<id>.mp4` | |

Ba trạng thái xác minh: **`accepted`** (chỉ trạng thái này vào pipeline), `rejected`,
`pending_review`. Luật tự động chỉ có thể **loại**; không video nào tự thành `accepted` – người
duyệt phải xác nhận MV chính thức, không AI, không hậu trường, không lyrics, giấy phép, nguồn gốc
và **an toàn nội dung** (sau khi xem toàn video).

**Kiểm duyệt an toàn nội dung** (tiêu chuẩn cộng đồng) – trạng thái hiện tại: metadata (lexicon đa
ngôn ngữ + nhận diện ngôn ngữ), giới hạn tuổi 18+ (M1), phụ đề YouTube và OCR chữ trên khung hình
(M2, chạy bằng `python -m src.dataset.moderate_videos` sau khi cài `requirements-moderation.txt`) đã
triển khai. Lời hát qua Speech-to-Text (M3) và hình ảnh (M4) **chưa triển khai** và luôn hiện
`unknown`. **Chưa có lexicon nào có hiệu lực**: bản nháp tiếng Việt/Anh ở
`config/moderation/lexicons/drafts/` chờ nhóm duyệt, nên kiểm tra văn bản hiện trả `unknown`/`unsupported`.
Tự động chỉ gắn cờ hoặc loại khi bằng chứng rõ ràng – hệ thống không phát hiện được mọi ngôn ngữ và
mọi dạng vi phạm.

Ước tính với 30 MV × 4 phút: khoảng 7.200 frame @1 fps và 14.400 frame @2 fps. Đây là **ước tính**,
số frame thật được thống kê sau khi trích xuất. Không dùng video giả, không cắt/lặp video, không tạo
metadata giả.

### 2.1. Khởi tạo (làm được khi chưa có video)

```powershell
python -m src.dataset.create_dataset
```

Lệnh này cũng tự nâng metadata schema cũ lên schema hiện hành (sao lưu trước vào
`dataset/metadata/backups/`). Chạy riêng: `python -m src.dataset.migrate_metadata [--dry-run]`.

### 2.2. Đưa MV vào

**Cách 1: tìm MV YouTube giấy phép CC BY rồi chọn URL để tải.**

```powershell
# 1) Chỉ đọc metadata, KHÔNG tải -> dataset/metadata/download_candidates.csv
python -m src.dataset.download_videos --preset music_video --max-results 30 --dry-run
# 2) Trong download_candidates.csv: screen_result = pass (không có dấu hiệu), review (có dấu hiệu
#    cần xem kỹ – cột weak_signals), reject (giấy phép / thời lượng / luật loại trừ).
#    Chép URL muốn dùng vào approved_urls.txt, mỗi dòng một URL.
# 3) Tải
python -m src.dataset.download_videos --url-file approved_urls.txt
```

- Chỉ tải khi `license` là **CC BY thuần**; NC/SA/ND, "Standard YouTube License" hoặc thiếu thông
  tin đều bị **từ chối**. Video công khai trên YouTube không mặc định được phép tải.
- Sàng lọc dùng **cùng luật** với validator (thời lượng, AI, hậu trường, lyrics, …) trên tiêu đề,
  mô tả và tags. Thêm cụm loại trừ tạm thời bằng `--exclude-keyword`.
- Qua sàng lọc **không có nghĩa là hợp lệ**: video tải về ở `pending_review`, `license_verified=no`.
  CC BY chỉ có giá trị khi người đăng sở hữu nội dung – người duyệt phải kiểm tra kênh.
- Mô tả, tags, kênh, ngày đăng được lưu vào `metadata/source_info/<file>.json` làm căn cứ xác minh.
- Tải tối đa 720p (`--max-height 1080` nếu cần), ưu tiên H.264/MP4. File lưu thành `raw/videos/yt_<id>.mp4`.
- Bài hát còn có quyền tác giả âm nhạc riêng: chỉ dùng cho phân tích học thuật, **không phân phối
  lại file video** (video bị `.gitignore`, chỉ commit metadata).

**Cách 2: MV tự cung cấp hoặc được cấp phép.** Chép file vào `dataset/raw/videos/`, rồi khai báo nguồn (không cần sửa CSV bằng tay):

```powershell
python -m src.dataset.scan_videos --register-source my_mv.mp4 --source user `
    --license "..." --title "..." --author "..." --source-url "..." --description "..."
```

**Gỡ video khỏi dataset:**

```powershell
python -m src.dataset.scan_videos --remove MV_005 --reason "reup, không phải kênh chính chủ"
python -m src.dataset.scan_videos --remove MV_005 --delete-file   # xóa hẳn file
```

File được chuyển vào `archive/videos/` (ngoài `dataset/`), không bao giờ ghi đè file đã có.
Metadata, nguồn và giấy phép được ghi lại trong `archive/archive_manifest.csv` để truy vết.

### 2.3. Quét, kiểm tra, duyệt, báo cáo

```powershell
python -m src.dataset.scan_videos         # đo metadata, sha256, đọc thử, gán video_id MV_xxx
python -m src.dataset.validate_dataset    # -> accepted / rejected / pending_review
python -m src.dataset.review_video --export --reviewer "Tên người duyệt"
#   điền dataset/validation/review_sheet.csv (các cột quyết định + evidence), rồi:
python -m src.dataset.review_video --import dataset/validation/review_sheet.csv --reviewer "Tên người duyệt"
python -m src.dataset.dataset_report      # -> dataset/reports/dataset_report.{md,json,csv}
```

- `scan_videos` chỉ đo đạc: chạy tăng dần (file không đổi thì không băm lại), ghi `videos.csv`
  sau mỗi video, `video_id` không đổi. Video mới luôn bắt đầu ở `pending_review`.
- `validate_dataset` ghi kết luận vào `videos.csv`, danh sách `dataset/validation/{accepted,rejected,pending_review}.csv`
  và `dataset/reports/validation_report.json`. Exit code 1 chỉ khi có lỗi cấp dataset (thiếu cột,
  trùng `video_id`, file chưa đăng ký); video bị loại là kết quả bình thường. `--quick` bỏ bước
  giải mã thử nhưng vẫn dùng kết quả đọc thử đã lưu (`probe_ok`).
- `review_video`: mọi quyết định cần `--reviewer` và căn cứ (`evidence`), được **ghi thêm** vào
  `metadata/reviews.csv` và gắn với sha256 – file đổi thì quyết định cũ hết hiệu lực.
- Split train/validation/test (70/15/15) **chỉ gồm video accepted**, cố định theo hash `video_id`.
  Đề tài không train mô hình: `validation` dùng để chỉnh `gap_tolerance`/`confidence`, `test` để
  gán ground truth và đánh giá.
- Báo cáo có: tổng số video, accepted / rejected / pending_review, số bị loại do AI / hậu trường /
  lyrics / sai thời lượng / trùng lặp, tổng thời lượng **accepted**, trạng thái giấy phép và nguồn,
  trùng lặp cần xử lý, danh sách video kèm lý do.

### 2.4. Hiện trạng Dataset (2026-11-25)

**Dataset có 0 MV. Chưa đạt mốc nào** (mốc đầu tiên là 5 MV accepted).

10 video thu thập theo định hướng cũ (8 video tổng hợp *Anh Trai Vượt Ngàn Chông Gai* và 2 concert,
dài 23–50 phút) **không đạt tiêu chí MV 2 phút 30 giây – 7 phút**. Cả 10 đã được gỡ khỏi dataset và chuyển vào
`archive/videos/`, cùng một video walking tour gỡ trước đó. Nguồn và giấy phép của chúng được giữ
trong `archive/archive_manifest.csv`. Các video này không được dùng trong bất kỳ số liệu nào của
đề tài MV.

Metadata đã được nâng lên schema v2 (sao lưu tại `dataset/metadata/backups/`). Tình trạng mới
nhất nằm trong `dataset/reports/dataset_report.md`.

### 2.5. Ground truth

Quy trình gán nhãn tay nằm trong
[dataset/annotations/annotation_guide.md](dataset/annotations/annotation_guide.md). Nhãn **không
bao giờ** được sinh từ kết quả YOLO.

## 3. Kiểm thử

```powershell
python -m pytest
```

Có 276 test, không cần mạng và không cần video thật. Video dùng trong test được sinh tạm bằng
OpenCV trong thư mục tạm của pytest (test tích hợp dùng 1 fps để có video 2–7,5 phút nhưng rất
nhỏ), **không** ghi vào `dataset/` hay `archive/` và không dùng để báo cáo kết quả.

| File | Phạm vi |
|---|---|
| `test_exclusion_rules.py` | Chuẩn hóa (có/không dấu, gạch nối, dấu câu); hậu trường, BTS là tên nghệ sĩ; lyrics, MV có phụ đề, lời trong mô tả; AI-generated, "ai" tiếng Việt, AI hậu kỳ, AI trong mô tả, VFX; teaser, reaction, tổng hợp, live, audio, fan made; không khớp xuyên đoạn |
| `test_dataset_validator.py` | Bảng §8.1: MV 4 phút xác minh đầy đủ, chưa xác minh nguồn, 2 phút / 4 phút / 7:30, trùng lặp, ghi đè của người duyệt, lỗi kỹ thuật không ghi đè được, quyết định cũ hết hiệu lực |
| `test_integration_dataset.py` | §8.2: import MV hợp lệ / hậu trường / lyrics / AI / chưa xác minh / sai thời lượng; Dataset hỗn hợp; danh sách, split, báo cáo chỉ tính accepted; cổng chặn video không accepted; kết quả cũ của video bị loại được lọc (không xóa); phiếu duyệt |
| `test_migration.py` | Schema v1 → v2: sao lưu, không mất dòng, không sinh `confirmed`, idempotent, dry-run, scan tự nâng cấp |
| `test_dataset_tools.py` | Hồi quy: cấu trúc, dataset rỗng, metadata, file hỏng/rỗng/cắt cụt, không phải MP4, trùng nội dung/tên file/nguồn, sai đường dẫn, chưa đăng ký, metadata cũ, quét tăng dần, gỡ video, CLI khi output bị chuyển hướng |
| `test_download_videos.py` | Giấy phép CC BY, khoảng thời lượng, luật chung khi sàng lọc, dấu hiệu weak, kênh tin cậy, dry-run, lỗi mạng |
| `test_content_safety.py` | Khử biến thể (leetspeak, ký tự lặp, chữ bị tách), lexicon, nhận diện ngôn ngữ (không đoán), chính sách tự loại; bảng tình huống §8 với kết quả kiểm duyệt giả lập (kiểm tra **logic**, không chứng minh độ chính xác mô hình). Lexicon test dùng từ giữ chỗ vô nghĩa |
| `test_moderation_m2.py` | Frame sampler (timestamp theo PTS, khoảng lấy mẫu, cửa sổ, thu nhỏ), đọc VTT (giữ điệp khúc lặp lại), chọn phụ đề (bỏ bản dịch máy), phụ đề chỉ gắn cờ, OCR với reader giả (≥ 2 frame mới tự loại, bỏ frame giống nhau, chữ không đọc được → unknown), EasyOCR thật trên video tổng hợp, CLI `moderate_videos` (checkpoint, bỏ video lỗi kỹ thuật) |
| `test_content_safety_integration.py` | scan → validate với lexicon tạm, giới hạn tuổi, cờ trong mô tả, mâu thuẫn, kết quả kiểm duyệt cũ bị bỏ qua, sàng lọc khi tải, migration v2 → v3 |

Chưa có test cho frame extraction, YOLO, temporal segment, Spark, Kafka, HDFS, Streamlit, cũng
như OCR / Speech-to-Text / kiểm duyệt hình ảnh, vì các module này **chưa được xây** (GĐ3–7, M2–M4). Hợp đồng mà chúng phải tuân theo đã được test qua cổng
`accepted_registry`.

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
