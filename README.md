# Video Intelligence – Truy vấn đối tượng và khoảng thời gian xuất hiện trong video

Đồ án môn **Phát triển ứng dụng trên nền tảng dữ liệu lớn**.

Hệ thống cho phép tìm một đối tượng mà mô hình YOLO hỗ trợ (ví dụ `person`, `microphone`,
`guitar`, …) trong một tập **video ca nhạc**. Kết quả trả về gồm: video nào có đối tượng, thời
điểm bắt đầu và kết thúc mỗi lần xuất hiện, tổng thời lượng, độ tin cậy, và phát lại video tại
thời điểm tìm được.

**Phạm vi dữ liệu:** chỉ video ca nhạc, ưu tiên show **Anh Trai Vượt Ngàn Chông Gai**, các MV và
concert khác vẫn được. Danh sách lớp đối tượng cụ thể sẽ do mô hình chọn ở Giai đoạn 4 quyết định
(xem [PROJECT_ANALYSIS.md §11.3](PROJECT_ANALYSIS.md)), vì 80 lớp COCO không có micro hay nhạc cụ.

| Video | Bắt đầu | Kết thúc |
|---|---|---|
| video_001 | 00:01:15 | 00:01:42 |
| video_001 | 00:05:20 | 00:05:48 |

> Bảng trên chỉ minh họa định dạng đầu ra, không phải kết quả đo được.

## Tiến độ

| Giai đoạn | Nội dung | Trạng thái |
|---|---|---|
| 1 | Phân tích project, môi trường ([PROJECT_ANALYSIS.md](PROJECT_ANALYSIS.md)) | ✅ Hoàn thành |
| 2 | Dataset: cấu trúc, quét, kiểm tra, tải video CC BY, báo cáo | ✅ Hoàn thành |
| 3 | Video processing: đọc theo luồng, lấy mẫu frame, timestamp theo PTS | ⏳ Chưa làm |
| 4 | Nhận diện đối tượng bằng YOLO (nano, CPU) → Parquet | ⏳ |
| 5 | Spark + HDFS: gom temporal segments, Spark SQL, benchmark | ⏳ |
| 6 | Kafka + Spark Structured Streaming | ⏳ |
| 7 | Giao diện Streamlit | ⏳ |
| 8 | Kiểm thử tích hợp, benchmark, tài liệu triển khai | ⏳ |

Kiến trúc mục tiêu:

```text
Video → kiểm tra → metadata → lấy mẫu frame → YOLO → detection events
      → Kafka / Parquet → Spark → temporal segments → Parquet / HDFS → truy vấn → Streamlit
```

## Cấu trúc thư mục

```text
VideoRetrieval-TuanTai/
├── config/
│   ├── settings.py             # cấu hình tập trung (biến VI_* trong .env)
│   └── music_presets.json      # preset tải video ca nhạc (atvncg, concert, music_video)
├── src/
│   ├── common/                 # ghi file atomic, sha256, CSV, logging
│   ├── dataset/                # công cụ quản lý Dataset (giai đoạn 2)
│   │   ├── create_dataset.py   # khởi tạo cấu trúc
│   │   ├── scan_videos.py      # quét video, đo metadata, khai báo nguồn
│   │   ├── validate_dataset.py # kiểm tra lỗi, trùng lặp, thời lượng, giấy phép
│   │   ├── download_videos.py  # tải video YouTube giấy phép CC BY (yt-dlp)
│   │   ├── dataset_report.py   # thống kê tổng quan
│   │   └── schema.py           # cột metadata, 80 lớp COCO
│   └── video_processing/
│       └── video_probe.py      # metadata + đọc thử video (OpenCV + FFmpeg)
├── dataset/                    # DỮ LIỆU – xem dataset/README.md
├── tests/                      # pytest
├── legacy/                     # project ImageRetrieval cũ (giữ để tham khảo)
├── requirements.txt / requirements-dev.txt
├── .env.example
└── PROJECT_ANALYSIS.md
```

Các thư mục `app/`, `src/object_detection/`, `src/big_data/`, `src/streaming/`, `src/storage/`,
`src/query/`, `scripts/`, `docker/` sẽ được tạo ở giai đoạn tương ứng. Tôi không tạo module rỗng
trước.

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

> Dùng `.venv-vi` (Python 3.11) thay cho Python 3.14 mặc định của máy, vì torch và ultralytics
> ở các giai đoạn sau cần phiên bản đã có wheel ổn định. `.venv` cũ chỉ dùng cho `legacy/`.

## 2. Dataset

Chi tiết cấu trúc và các trường metadata nằm trong [dataset/README.md](dataset/README.md).

**Yêu cầu:**
- Chỉ video ca nhạc. Mỗi video có `category` thuộc `music_show` (show truyền hình, ví dụ ATVNCG),
  `music_video` hoặc `concert`, cấu hình qua `VI_ALLOWED_CATEGORIES`.
- Mỗi video dài ≥ 10 phút (mục tiêu 10–30 phút), 10–20 video, tổng 2–5 giờ.
- Không dùng video giả, không lặp video ngắn.
- Mỗi video phải ghi rõ nguồn, kênh đăng và giấy phép.

MV đơn lẻ thường chỉ dài 3–5 phút nên bị loại. Với thể loại `music_video`, nên dùng live session
hoặc bản tổng hợp dài ≥ 10 phút.

### 2.1. Khởi tạo (làm được cả khi chưa có video)

```powershell
python -m src.dataset.create_dataset
```

Lệnh này tạo cây thư mục `raw/`, `metadata/`, `processed/`, `annotations/`, `splits/`, file
`dataset/README.md`, các CSV chỉ có header (không chứa dòng giả), `labels.csv` (80 lớp COCO) và
`dataset_info.json`. Chạy lại nhiều lần không ghi đè dữ liệu có sẵn.

### 2.2. Đưa video vào

**Cách 1: tải video YouTube có giấy phép Creative Commons Attribution (CC BY)**

```powershell
# Ưu tiên: Anh Trai Vượt Ngàn Chông Gai. Xem trước, không tải
python -m src.dataset.download_videos --preset atvncg --max-results 20 --dry-run
# Tải video đã chọn (preset vẫn kiểm tra kênh chính chủ và tiêu đề)
python -m src.dataset.download_videos --preset atvncg --url "https://www.youtube.com/watch?v=VIDEO_ID"
# Hoặc tự động tải N video đầu tiên đạt yêu cầu
python -m src.dataset.download_videos --preset atvncg --limit 5

# Concert / MV khác
python -m src.dataset.download_videos --preset concert --max-results 10 --dry-run
python -m src.dataset.download_videos --category concert --url "https://www.youtube.com/watch?v=VIDEO_ID"
```

Preset nằm trong [config/music_presets.json](config/music_presets.json). Preset `atvncg`:
- chỉ nhận video từ kênh **YeaH1 Channel** (đơn vị sản xuất chương trình);
- tiêu đề phải chứa "anh trai vượt ngàn chông gai", so khớp không dấu. Nhờ vậy các show khác cùng
  kênh như "Gia Đình Haha" bị loại.

Lý do lọc theo kênh: giấy phép CC BY chỉ có giá trị khi người đăng sở hữu nội dung, nên video do
kênh khác đăng lại sẽ bị từ chối. Có thể thêm kênh tin cậy bằng `--trusted-channel CHANNEL_ID` và
từ khóa bằng `--title-keyword`.

Quy tắc của công cụ:

- Đọc metadata trước khi tải. Video chỉ được tải khi trường `license` là **CC BY thuần**.
  Giấy phép NC/SA/ND, "Standard YouTube License" hoặc thiếu thông tin đều bị **từ chối**.
- Từ chối video ngắn hơn `VI_MIN_VIDEO_DURATION_SEC` (mặc định 10 phút) và video đang livestream.
- `--search` dùng bộ lọc Creative Commons của YouTube, kết hợp bộ lọc độ dài qua `--length`:
  `long` > 20 phút (mặc định), `medium` 4–20 phút, `any`. Giấy phép vẫn được kiểm tra lại cho
  từng video.
- Video được tải ở độ phân giải tối đa 720p (`--max-height`), ưu tiên H.264 để trình duyệt phát
  được, lưu với tên `raw/videos/yt_<id>.mp4`.
- URL, tiêu đề, kênh (`channel_id`), giấy phép, `category` và `series` được ghi vào
  `metadata/sources.csv`. CC BY yêu cầu ghi công tác giả khi sử dụng.
- Bài hát trong video còn có quyền tác giả âm nhạc riêng. Chỉ dùng cho phân tích học thuật,
  **không phân phối lại file video**.
- File `metadata/download_archive.txt` giúp chạy lại không tải trùng, và tiếp tục được khi bị ngắt.

**Cách 2: video tự cung cấp.** Chép file vào `dataset/raw/videos/`, rồi khai báo nguồn bằng lệnh
(không cần sửa CSV bằng tay):

```powershell
python -m src.dataset.scan_videos --register-source my_video.mp4 --source user `
    --license "..." --category concert --series "..." --title "..."
```

**Gỡ video khỏi dataset** (ví dụ video ngoài domain):

```powershell
python -m src.dataset.scan_videos --remove video_005                # file chuyển vào raw/removed/
python -m src.dataset.scan_videos --remove video_005 --delete-file  # xóa hẳn file
```

### 2.3. Quét, kiểm tra, báo cáo

```powershell
python -m src.dataset.scan_videos         # đo metadata, sha256, gán video_id, chia split
python -m src.dataset.validate_dataset    # exit code 1 nếu có lỗi
python -m src.dataset.dataset_report      # in thống kê + ghi metadata/dataset_report.md
```

`scan_videos` hoạt động như sau:

- Chạy tăng dần: file không đổi (cùng dung lượng và mtime) thì không băm lại.
- `videos.csv` được ghi sau mỗi video, nên có thể tiếp tục khi bị ngắt.
- `video_id` (`video_001`, …) được gán một lần và không đổi.
- Split train/validation/test (70/15/15): video được xếp theo hash `video_id` rồi cắt theo tỷ lệ.
  Kết quả cố định giữa các lần chạy, và validation/test luôn có ít nhất 1 video khi có ≥ 3 video.

`validate_dataset` phát hiện các trường hợp sau:

| Mã | Mức | Ý nghĩa |
|---|---|---|
| `unreadable` / `corrupt` | lỗi | Không mở được, file rỗng, không giải mã được frame đầu/giữa/cuối |
| `duplicate_content` | lỗi | Trùng nội dung (sha256) với video khác |
| `duplicate_id` | lỗi | Trùng `video_id` hoặc `filepath` |
| `missing_file` | lỗi | Sai đường dẫn hoặc file đã bị xóa/di chuyển |
| `missing_metadata` / `missing_metadata_columns` | lỗi | Thiếu trường hoặc giá trị không hợp lệ (FPS = 0, …) |
| `unregistered_file` | lỗi | Video có trên đĩa nhưng chưa có metadata |
| `metadata_stale` | lỗi | File đã thay đổi so với metadata, cần chạy lại scan |
| `too_short` | cảnh báo | Ngắn hơn ngưỡng thời lượng |
| `missing_license` | cảnh báo | Chưa khai báo giấy phép |
| `out_of_domain` | cảnh báo | `category` không phải video ca nhạc (hoặc chưa khai báo) |
| `probe_warning` | cảnh báo | Thời lượng OpenCV lệch container (có thể là VFR) |

`--strict` coi cảnh báo là lỗi. `--quick` bỏ bước giải mã thử frame. Video có lỗi được chuyển
`processing_status = invalid`, còn lại được chuyển thành `validated`.

### 2.4. Hiện trạng Dataset

Đã đạt mục tiêu tối thiểu: **10 video, 5,38 giờ** (05:22:30), dung lượng 3,1 GB. Tất cả đều có
giấy phép CC BY và đạt `validate_dataset --strict` (0 lỗi, 0 cảnh báo). Thành phần: 8 video
`music_show` – Anh Trai Vượt Ngàn Chông Gai (kênh YeaH1) và 2 video `concert`.
Split train/validation/test là 6/2/2.

| video_id | Tiêu đề (kênh) | Thời lượng | Độ phân giải | FPS | Thể loại |
|---|---|---|---|---|---|
| video_001 | [ANH TRAI VƯỢT NGÀN CHÔNG GAI – Tổng hợp khoảnh khắc tập luyện Đào Liễu](https://www.youtube.com/watch?v=7cM6_ZONDGM) (YeaH1 Channel) | 00:24:12 | 1280×720 | 29.97 | music_show |
| video_002 | [ANH TRAI VƯỢT NGÀN CHÔNG GAI – Khoảnh khắc tập luyện siêu ồn của Nhà Hát và cái kết](https://www.youtube.com/watch?v=adToS1DJd74) (YeaH1 Channel) | 00:25:41 | 1280×720 | 29.97 | music_show |
| video_003 | [ANH TRAI VƯỢT NGÀN CHÔNG GAI – Tổng hợp khoảnh khắc tập luyện Dạ Cổ của Nhà Mứt Gừng](https://www.youtube.com/watch?v=e20m2bjeh1U) (YeaH1 Channel) | 00:23:31 | 1280×720 | 29.97 | music_show |
| video_004 | [ANH TRAI VƯỢT NGÀN CHÔNG GAI – Tổng hợp khoảnh khắc tập luyện Chiếc Khắn Piêu của Nhà Cá Lớn](https://www.youtube.com/watch?v=ektRXdwR_vI) (YeaH1 Channel) | 00:23:55 | 1280×720 | 29.97 | music_show |
| video_005 | [ANH TRAI VƯỢT NGÀN CHÔNG GAI – Nhìn lại các màn tập luyện cực nhiệt huyết của các Anh Tài](https://www.youtube.com/watch?v=tjqs7-iQ7Ko) (YeaH1 Channel) | 00:28:08 | 1280×720 | 29.97 | music_show |
| video_006 | [ANH TRAI VƯỢT NGÀN CHÔNG GAI – Tổng hợp khoảnh khắc cực căng tại vòng song ca ở Công Diễn 4](https://www.youtube.com/watch?v=W6vvT3sfkzA) (YeaH1 Channel) | 00:43:50 | 1280×720 | 29.97 | music_show |
| video_007 | [ANH TRAI VƯỢT NGÀN CHÔNG GAI – Đằng sau tiết mục cực cháy của Nhà Ngũ Hành là cái giá cần phải trả](https://www.youtube.com/watch?v=WAg2mTtHbYE) (YeaH1 Channel) | 00:31:07 | 1280×720 | 29.97 | music_show |
| video_008 | [[FULL FOCUS CAM] NHÀ TRẺ - TỔNG HỢP TIẾT MỤC CÔNG DIỄN 4 I ANH TRAI VƯỢT NGÀN CHÔNG GAI 2024](https://www.youtube.com/watch?v=X1V-tMufAPA) (YeaH1 Channel) | 00:28:27 | 360×640 | 30.00 | music_show |
| video_009 | [Ausomapto live [Full concert]](https://www.youtube.com/watch?v=HMhyzt0xoGs) (Ausomapto) | 00:43:39 | 1280×720 | 29.97 | concert |
| video_010 | [Liveshow Hà Nội và em khi thu chớm đông sang (P1) - Phú Quang](https://www.youtube.com/watch?v=nFl49167ac4) (Phú Quang) | 00:49:59 | 854×480 | 25.00 | concert |

Ghi chú:
- `video_008` là video quay dọc (focus cam, 360×640).
- Hai video concert được chọn vì tên kênh trùng với nghệ sĩ. Quyền sở hữu chưa được xác minh độc lập.
- Video walking tour tải ở lần thử trước nằm ngoài domain ca nhạc, đã được gỡ và chuyển vào
  `dataset/raw/removed/`. Có thể xóa hẳn thư mục này.

Tình trạng mới nhất nằm trong `dataset/metadata/dataset_report.md`. File video không được commit
(`.gitignore`), chỉ metadata được commit. Muốn khôi phục video, chạy lại `download_videos --url` với
URL trong `sources.csv`.

## 3. Kiểm thử

```powershell
python -m pytest
```

Có 39 test, không cần mạng và không cần video thật. Video vài giây dùng trong test được sinh tạm
bằng OpenCV trong thư mục tạm của pytest, **không** ghi vào `dataset/` và không dùng để báo cáo
kết quả. Phạm vi test:

- Dataset rỗng.
- Video hợp lệ và metadata.
- Video quá ngắn (cảnh báo, thành lỗi khi `--strict`).
- File hỏng, file rỗng, file bị cắt cụt.
- Trùng lặp.
- Sai đường dẫn.
- Video chưa đăng ký.
- Thiếu trường hoặc cột metadata.
- Metadata cũ.
- Quét tăng dần.
- Khai báo nguồn.
- Split.
- Báo cáo.
- Phân loại giấy phép (CC BY, NC, SA, Standard).
- Kênh chính chủ và từ khóa tiêu đề không dấu (preset `atvncg`).
- Domain ca nhạc (`out_of_domain`).
- Gỡ video.
- Dry-run.
- Lỗi mạng khi đọc metadata.

## 4. Môi trường đã kiểm tra

| Thành phần | Phiên bản trên máy | Dùng ở giai đoạn |
|---|---|---|
| Python (`.venv-vi`) | 3.11.9 | tất cả |
| OpenCV (headless) | 5.1.0 | 2–4 |
| FFmpeg (imageio-ffmpeg) | 9.0.2 | 2–3 |
| yt-dlp | 2026.10.26 | 2 |
| Java | 17.0.20.1 | 5–6 |
| Apache Spark | 4.2.0 (`SPARK_HOME`), đã chạy thử Parquet | 5–6 |
| Apache Hadoop | 3.4.0 (`HADOOP_HOME`, có winutils) | 5 |
| Apache Kafka | 4.3.1 (KRaft) | 6 |
| Docker | 29.9.3 (dự phòng cho HDFS/Kafka) | 5–6 |

Rủi ro và kế hoạch chi tiết xem trong [PROJECT_ANALYSIS.md](PROJECT_ANALYSIS.md).

## 5. Legacy

Project ImageRetrieval cũ (truy vấn ảnh Castle5000: Qwen3-VL, GroundingDINO, SAM2, FastAPI,
React) đã được chuyển nguyên vẹn vào [legacy/](legacy/). Lịch sử git vẫn được giữ, và 32 test cũ
vẫn đạt sau khi di chuyển. Xem [legacy/README.md](legacy/README.md). File `.env` cũ, chứa token
Kaggle, nằm tại `legacy/.env` và không được commit.
