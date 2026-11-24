# PROJECT ANALYSIS – Video Intelligence (Giai đoạn 1)

> Đề tài: **Video Intelligence – Hệ thống truy vấn đối tượng và xác định khoảng thời gian xuất hiện trong video**
> Môn: Phát triển ứng dụng trên nền tảng dữ liệu lớn
> Ngày phân tích: 2026-11-23 · Trạng thái: **chỉ phân tích, chưa sửa/xóa file nào của project**

---

## 1. Phạm vi đã kiểm tra

| Hạng mục | Cách kiểm tra |
|---|---|
| Cấu trúc thư mục, file được track | `git ls-files`, quét toàn bộ cây thư mục (bỏ `.git`, `node_modules`, `.venv`) |
| Lịch sử & thay đổi chưa commit | `git log`, `git diff` (README.md đang sửa dở: đổi tên Image → Video) |
| Source Python | Đọc toàn bộ `common.py`, `pipeline.py`, `config.json`, các `requirements-*.txt`; đọc lược đồ hàm/lớp của 20 module còn lại |
| Cấu hình | `.gitignore`, `.env.example`, `.vscode/settings.json`, khóa (không đọc giá trị) trong `.env` |
| Kiểm thử hiện có | Chạy `python -m unittest discover` trong `.venv` → **32 test, OK (1 skipped)** |
| Môi trường | Python, Java, Spark, Hadoop, Kafka, FFmpeg, Docker, GPU, RAM, ổ đĩa |
| Spark thực tế | Chạy smoke test PySpark `local[2]`: ghi/đọc Parquet 100.000 dòng + groupBy → **thành công** |

---

## 2. Hiện trạng project

### 2.1. Bản chất source code hiện tại

Repo được fork/đổi tên từ project **ImageRetrieval** (truy vấn ảnh, bộ Castle5000 – 5.000 ảnh bếp). Thư mục đã đổi tên thành `source/video_retrieval/` nhưng **nội dung vẫn 100% là hệ thống truy vấn ảnh**:

```text
VideoRetrieval-TuanTai/
├── README.md                 # hướng dẫn ImageRetrieval (đang sửa dở)
├── .env / .env.example       # biến IMAGE_*, KAGGLE_*, QWEN_*
├── frontend/                 # React + Vite + Playwright: trang tìm ảnh, DRES
└── source/video_retrieval/   # ~3.900 dòng Python
    ├── common.py             # atomic_json, sha256, manifest ảnh, màu HSV, validate vector
    ├── pipeline.py           # Kaggle 2×T4: Qwen3-VL embedding + GroundingDINO + SAM2
    ├── search_api.py         # FastAPI + Qdrant/SQLite, tìm ảnh theo text/ảnh/object/màu
    ├── multi_search.py, model_registry.py, reranker.py, local_runtime.py
    ├── encoder_service.py, worker_service.py, manage*.py   # Kaggle worker + cloudflared tunnel
    ├── query_agent.py        # chỉnh query qua Codex CLI (gpt-6-luna)
    ├── dres.py               # adapter DRES v2 (cuộc thi VBS/LSC)
    ├── build_index.py, embed_corpus.py, benchmark_search.py, download_data.py
    └── test_*.py             # 6 file test, 32 test case
```

Không có: OpenCV, FFmpeg, YOLO/Ultralytics, Spark, HDFS, Kafka, Streamlit, xử lý video, timestamp, temporal segment, dataset video.

### 2.2. Môi trường máy (đo thực tế)

| Thành phần | Hiện trạng | Đánh giá |
|---|---|---|
| OS | Windows 11, 12 luồng CPU, RAM **15,7 GB**, ổ E: trống **159 GB** | Đủ cho 2–5 giờ video + Parquet |
| Python | 3.14.6 (mặc định, PATH), 3.13 (Store), **3.11.9** | Nên dùng 3.11 cho venv mới (xem rủi ro R1) |
| `.venv` của repo | Python 3.11.9, tạo tại đường dẫn cũ `E:\ImageRetrieval-TuanTai\.venv`; có fastapi, qdrant, numpy, pyarrow 23; **không có pytest, pyspark, cv2, ultralytics** | Dùng được cho code cũ; không phù hợp cho hệ thống mới |
| Python 3.14 toàn cục | pyspark 4.2.0, pandas 2.3.3, pyarrow 26.0.1, numpy 2.5.5, kafka-python 3.0.13, psutil 7.2.2 | Không dùng trực tiếp, chỉ để đối chiếu phiên bản |
| Java | JDK 17.0.20.1 (`JAVA_HOME` đúng); ngoài ra có JDK 25 trên PATH | 17 tương thích Spark 4.x |
| Spark | `SPARK_HOME=C:\spark-4.2.0-bin-hadoop3` (Scala 2.13, client Hadoop 3.5.0) | **Smoke test OK**, nhưng mất ~104 s lần đầu |
| Hadoop | `HADOOP_HOME=C:\hadoop-3.4.0`, có `winutils.exe`, `hadoop.dll`, `fs.defaultFS=hdfs://localhost:9000` | Đã cấu hình, NameNode **chưa chạy** (cổng 9000/9870 đóng) |
| Kafka | `C:\kafka_2.13-4.3.1` (KRaft, broker+controller, `log.dirs=C:/tmp/kraft-combined-logs`) | Đã cài, **chưa chạy** (cổng 9092 đóng) |
| Spark ↔ Kafka | Thư mục `jars` của Spark **không có** `spark-sql-kafka-0-10` | Cần `--packages org.apache.spark:spark-sql-kafka-0-10_2.13:4.2.0` |
| FFmpeg/ffprobe | **Không có** trên PATH | Phải cài (choco/winget) hoặc dùng `imageio-ffmpeg` |
| OpenCV, Ultralytics, Streamlit | **Chưa cài** ở bất kỳ môi trường nào | Cài trong venv mới |
| Docker | 29.9.3 đã cài, **daemon chưa chạy** | Phương án dự phòng cho HDFS/Kafka |
| GPU | Có driver NVIDIA (nvidia-smi bị từ chối quyền) | Chưa xác định model/VRAM; mặc định chạy CPU |

---

## 3. Đánh giá khả năng tái sử dụng

### 3.1. Tái sử dụng được (sao chép/chuyển thể, không sửa file gốc)

| Thành phần cũ | Dùng cho | Ghi chú |
|---|---|---|
| `common.atomic_json`, ghi file tạm + `os.replace` | Ghi metadata, checkpoint an toàn | Mẫu chuẩn cho cơ chế **resume** khi bị gián đoạn |
| `common.digest_file` (sha256 theo chunk 1 MB) | Phát hiện **video trùng lặp** trong `validate_dataset.py` | Không đọc toàn bộ file vào RAM |
| `pipeline.write_parquet` + schema pyarrow tường minh | Ghi detection events ra Parquet | Đổi schema sang video |
| Mô hình checkpoint per-item + fingerprint config/code (`pipeline.run_all`) | Checkpoint theo từng video / từng chunk frame | Tránh resume nhầm khi đổi config |
| Mẫu validate nghiêm ngặt (raise khi sai, không bỏ qua lỗi) | Validation dataset | Phù hợp nguyên tắc "không bỏ qua lỗi" |
| `make_overlay` (vẽ bbox) | Ảnh minh họa frame có đối tượng trên Streamlit | Thay PIL mask bằng bbox YOLO |
| `benchmark_search.py` (đo p50/p95) | Ý tưởng đo **query latency** | Viết lại cho Spark/pandas |
| Cấu trúc test bằng fixture nhỏ (`test_download_data.py`) | Test dataset tool không cần video thật | |

### 3.2. Không còn phù hợp (giữ nguyên, không xóa, đề xuất chuyển sang `legacy/`)

| Thành phần | Lý do |
|---|---|
| `pipeline.py` (Qwen3-VL 8B, GroundingDINO, SAM2, bắt buộc 2×T4) | Quá nặng, không dùng YOLO, không chạy trên laptop CPU |
| `search_api.py`, `multi_search.py`, `model_registry.py`, `reranker.py`, `local_runtime.py`, `build_index.py`, `embed_corpus.py` | Tìm ảnh theo embedding + Qdrant, không có khái niệm thời gian |
| `manage*.py`, `worker_service.py`, `encoder_service.py`, `cloudflared_release.json` | Điều phối Kaggle notebook + tunnel, ngoài phạm vi |
| `query_agent.py` | Phụ thuộc Codex CLI/`gpt-6-luna`, không thuộc yêu cầu |
| `dres.py` | Adapter cuộc thi DRES, không thuộc yêu cầu |
| `frontend/` (React) | Đề tài yêu cầu Streamlit |
| `config.json`, `public_data.json` | Cấu hình Castle5000 (ảnh) |

**Đề xuất:** sau khi được xác nhận, `git mv source/ legacy/image_retrieval/` và `frontend/` → `legacy/frontend/` để lịch sử git còn nguyên, code cũ vẫn chạy được, và không lẫn với hệ thống mới. Nếu muốn giữ nguyên vị trí thì hệ thống mới vẫn tách hoàn toàn trong `src/` – hai phương án không ảnh hưởng nhau.

---

## 4. Vấn đề phát hiện trong project hiện tại

| # | Mức độ | Vấn đề | Bằng chứng |
|---|---|---|---|
| B1 | Cao | `.gitignore` vẫn ignore `source/image_retrieval/output`, `.work`, `.venv` – sau khi đổi tên, `source/video_retrieval/output/` và `.work/` (chứa state Kaggle, output GB) **không còn bị ignore** → nguy cơ commit dữ liệu lớn/state riêng | `git check-ignore source/video_retrieval/output/x` → không khớp |
| B2 | Cao | README còn **19** chỗ trỏ `source/image_retrieval/...`; các lệnh hướng dẫn chạy sẽ lỗi "file not found" | grep `image_retrieval` |
| B3 | TB | `python -m source.image_retrieval` trong docstring/README không còn tồn tại; `manage.py` (3 chỗ), `serve.py`, `search_api.py`, `download_data.py`, `__init__.py`, `__main__.py`, `test_search_api.py` vẫn ghi tên cũ | grep |
| B4 | TB | `.venv` được tạo tại `E:\ImageRetrieval-TuanTai\.venv`; launcher `pip.exe`/`*.exe` trong `Scripts/` gắn đường dẫn cũ → có thể lỗi; `python.exe` vẫn chạy | `pyvenv.cfg` |
| B5 | Thấp | `pytest` chưa cài; test cũ chỉ chạy qua `unittest` | |
| B6 | Thấp | `httpx` + `starlette.testclient` cảnh báo deprecated | output test |
| B7 | Thông tin | README.md đang có thay đổi chưa commit (đổi tên repo) | `git status` |
| B8 | Bảo mật | `.env` chứa token Kaggle thật – đã được `.gitignore` đúng; **không đưa vào bất kỳ file mới nào** | |

B1–B3 sẽ được sửa ở Giai đoạn 2 (chỉ sửa đường dẫn trong `.gitignore`/README, không đổi logic).

---

## 5. Kiến trúc đề xuất

### 5.1. Cấu trúc thư mục

Giữ gần sát cấu trúc trong đề bài, điều chỉnh 2 điểm có lý do kỹ thuật:

1. **`src/kafka/` → `src/streaming/`**: package tên `kafka` sẽ **che** thư viện `kafka-python` (`import kafka`) khi `src/` nằm trên `sys.path` → lỗi import khó phát hiện.
2. **`src/dataset/` tách khỏi `dataset/`**: `dataset/` là dữ liệu (gitignore video), `src/dataset/` là code.

```text
VideoRetrieval-TuanTai/
├── app/streamlit_app.py
├── config/
│   ├── settings.py              # dataclass cấu hình tập trung, đọc .env, pathlib
│   └── spark-defaults.conf      # driver memory, shuffle partitions cho laptop
├── dataset/                     # DỮ LIỆU (raw/videos bị gitignore)
│   ├── raw/videos/
│   ├── metadata/{videos.csv, dataset_info.json, sources.csv}
│   ├── processed/{frames, detections, temporal_segments, embeddings}/
│   ├── annotations/{labels.csv, ground_truth.csv}
│   ├── splits/{train,validation,test}.csv
│   └── README.md
├── models/                      # yolo nano .pt (gitignore)
├── src/
│   ├── common/{logging_utils.py, io_utils.py}   # atomic write, sha256 (chuyển từ common.py cũ)
│   ├── dataset/{create_dataset, scan_videos, validate_dataset, download_videos, dataset_report}.py
│   ├── video_processing/{video_reader, frame_extractor, timestamp_manager}.py
│   ├── object_detection/yolo_detector.py
│   ├── streaming/{events.py, producer.py, consumer.py}   # schema sự kiện + Kafka
│   ├── big_data/{spark_session, spark_batch, spark_streaming, temporal_aggregation}.py
│   ├── storage/{parquet_storage, hdfs_storage}.py
│   └── query/object_search.py
├── scripts/{run_detection, run_pipeline, benchmark}.py
├── tests/                       # pytest, fixture video tổng hợp cực ngắn chỉ để TEST
├── docker/docker-compose.yml    # HDFS (namenode+datanode) + Kafka KRaft – phương án dự phòng
├── notebooks/colab_detection.ipynb  # chạy YOLO trên GPU Colab (tùy chọn)
├── legacy/                      # (nếu được đồng ý) code ImageRetrieval cũ
├── requirements.txt / requirements-dev.txt
├── .env.example, README.md, PROJECT_ANALYSIS.md
```

> Ghi chú về video test: `tests/` sẽ sinh **video tổng hợp vài giây bằng OpenCV ngay lúc chạy test** để kiểm thử logic đọc/sampling/timestamp. Video này **không bao giờ** được ghi vào `dataset/` và không được dùng để báo cáo kết quả.

### 5.2. Luồng dữ liệu

```text
dataset/raw/videos ──scan/validate──► metadata/videos.csv (+ Parquet)
        │
        ▼  (stream từng frame, không nạp cả video vào RAM)
FrameExtractor: sample mỗi N giây, timestamp = PTS (CAP_PROP_POS_MSEC / ffprobe)
        │
        ▼  batch nhỏ (vd. 8–16 frame)
YoloDetector (yolo11n/yolov8n, CPU) ──► DetectionEvent{video_id, frame_idx, pts_sec, class, conf, bbox}
        │
        ├── [BATCH]  Parquet partition theo video_id ─► dataset/processed/detections/  ─► (đồng bộ) HDFS
        └── [STREAM] Kafka producer ─► topic `vi.detections` ─► Spark Structured Streaming ─► Parquet/HDFS
                                                                (checkpointLocation để exactly-once sink)
        ▼
Spark batch: window/lag theo (video_id, object_name) sắp theo pts
        → gap > gap_tolerance ⇒ đoạn mới → temporal_segments (start, end, duration, n_frames, avg_conf, max_conf)
        ▼
Query: Spark SQL / (pandas+pyarrow cho truy vấn tương tác nhanh) ─► Streamlit
        ▼
Streamlit: danh sách video, trạng thái, tìm "car", bảng đoạn, st.video(start_time=start)
```

### 5.3. Thuật toán temporal segment (thiết kế)

Với mỗi `(video_id, object_name)` sắp theo `timestamp`:

```text
prev_ts   = LAG(timestamp)
new_seg   = 1 nếu prev_ts IS NULL hoặc timestamp - prev_ts > gap_tolerance
segment_id = SUM(new_seg) OVER (ORDER BY timestamp)   -- cộng dồn
GROUP BY segment_id → start=min(ts), end=max(ts)+sample_interval/2 (tùy chọn), n_frames, avg(conf)
```

- Trước khi gom: lọc `confidence ≥ threshold` và gộp nhiều box cùng class trong một frame thành 1 sự kiện "có mặt" (giữ `max_conf`, `count`) → tránh đếm trùng.
- Tùy chọn `min_segment_frames` để loại đoạn nhiễu 1 frame.
- `gap_tolerance` mặc định = `2 × sample_interval` (cho phép bỏ lỡ 1 frame mẫu).
- Ví dụ đề bài (10,11,12,13,20,21 s; gap_tolerance=2 s) → `[10–13]`, `[20–21]`.
- **Sai số thời gian**: với khoảng lấy mẫu Δ, biên thật của đoạn nằm trong `[start−Δ, start]` và `[end, end+Δ]`. Kết quả sẽ ghi rõ `temporal_resolution_sec = Δ`; không khẳng định chính xác tuyệt đối.
- Timestamp lấy từ PTS của frame (`CAP_PROP_POS_MSEC`), có kiểm tra đơn điệu; video VFR được phát hiện bằng ffprobe (`r_frame_rate` ≠ `avg_frame_rate`) và ghi cờ vào metadata.
- Cùng một thuật toán có 2 bản: **Spark** (Window functions) và **pandas tuần tự** → vừa để benchmark, vừa để kiểm chứng chéo kết quả bằng test.

### 5.4. Batch vs Stream (phân biệt rõ)

| | Batch | Stream |
|---|---|---|
| Đầu vào | Parquet detection của video đã lưu | Sự kiện JSON trên Kafka topic |
| Engine | Spark DataFrame/SQL | Spark Structured Streaming (`readStream.format("kafka")`) |
| Mục đích | Xây temporal segments đầy đủ, thống kê, benchmark | Tiếp nhận sự kiện liên tục, ghi append, đếm theo cửa sổ thời gian |
| Gọi tên | "xử lý batch video lưu sẵn" | "xử lý luồng sự kiện nhận diện" — **không** gọi là "phân tích video thời gian thực" vì nguồn vẫn là video đã lưu được phát lại |

### 5.5. HDFS – phương án chạy

1. **Chính:** Hadoop 3.4.0 native Windows đã cài (`winutils` có sẵn): `hdfs namenode -format` một lần → `start-dfs.cmd`. Spark ghi `hdfs://localhost:9000/video_intel/...`.
2. **Dự phòng 1:** `docker/docker-compose.yml` (namenode + datanode) khi native lỗi.
3. **Dự phòng 2:** `STORAGE_BACKEND=local` → cùng code ghi Parquet ra `dataset/processed/`. `hdfs_storage.py` dùng chung giao diện, chỉ đổi URI.

Tương tự Kafka: native `kafka-server-start.bat` (KRaft) hoặc container Docker.

---

## 6. Dataset – định hướng nguồn video

Không tự tạo video giả, không lặp video ngắn. Các nguồn sẽ được hỗ trợ (giấy phép **phải được kiểm tra cho từng video** và ghi vào `sources.csv`):

| Nguồn | Ưu | Lưu ý |
|---|---|---|
| Video người dùng tự quay/cung cấp (camera giao thông, đường phố) | Chủ động thời lượng ≥ 10 phút, rõ giấy phép | Khuyến nghị số 1 |
| YouTube có giấy phép **Creative Commons BY** (live cam giao thông, walking tour) qua `yt-dlp` | Dễ có video 10–60 phút, nhiều car/person/motorcycle | `download_videos.py` sẽ đọc trường `license` từ metadata và **từ chối** nếu không phải CC |
| Wikimedia Commons, Internet Archive (public domain / CC) | Giấy phép rõ ràng | Ít video dài có nhiều đối tượng COCO |
| Dataset học thuật (VIRAT Ground, v.v.) | Có ground truth | Thường cần đăng ký; điều khoản chỉ dùng nghiên cứu |

Nếu tải tự động không được phép/không thành công → công cụ `scan_videos.py` nhận video người dùng tự chép vào `dataset/raw/videos/` và tự sinh metadata (không cần sửa CSV bằng tay).

---

## 7. Rủi ro kỹ thuật

| # | Rủi ro | Ảnh hưởng | Giảm thiểu |
|---|---|---|---|
| R1 | Python 3.14 mặc định quá mới cho torch/ultralytics/opencv wheel | Cài lỗi/xung đột | Tạo venv riêng **Python 3.11** (`.venv-vi`), không đụng `.venv` cũ; ghim phiên bản |
| R2 | PySpark pip phải **khớp đúng** Spark 4.2.0 của `SPARK_HOME` | Lỗi Py4J/version mismatch | Ghim `pyspark==4.2.0`; kiểm tra lúc khởi tạo session |
| R3 | Spark khởi động chậm (~104 s lần đầu) trên Windows | Benchmark sai lệch, UI chậm | Tách thời gian startup khỏi thời gian xử lý trong benchmark; Streamlit đọc Parquet kết quả bằng pyarrow thay vì mở Spark mỗi truy vấn |
| R4 | Spark trên Windows: `winutils`, quyền `C:\tmp`, đường dẫn có dấu cách | Lỗi ghi Parquet/checkpoint | Đã có winutils; dùng thư mục tạm trong repo; fallback chạy trong Docker |
| R5 | `spark-sql-kafka` chưa có trong `jars` | Structured Streaming không đọc được Kafka | `--packages ...:4.2.0` (cần mạng lần đầu) hoặc tải jar vào `jars/` |
| R6 | Client Hadoop của Spark (3.5.0) vs HDFS 3.4.0 | Lệch phiên bản RPC | Thường tương thích ngược; kiểm thử thực tế ở Giai đoạn 5, fallback Docker |
| R7 | YOLO trên CPU chậm: ước tính ~10–20 frame/s với nano 640px (**chưa đo**) | 5 giờ video @1 fps = 18.000 frame → ~15–30 phút; @5 fps lâu gấp 5 | Sampling mặc định 1 fps, imgsz 640, batch nhỏ; checkpoint theo video; tùy chọn notebook Colab GPU |
| R8 | Sampling thưa → biên đoạn sai số ±Δ, đối tượng xuất hiện < Δ có thể bị bỏ sót | Độ chính xác thời gian | Ghi `temporal_resolution_sec`; đánh giá với `ground_truth.csv` |
| R9 | Video VFR / PTS không liên tục | Timestamp sai nếu tính `frame_idx / fps` | Luôn dùng PTS; test riêng trường hợp timestamp nhảy |
| R10 | FFmpeg chưa cài | Không lấy được metadata chi tiết/VFR, không cắt clip | `choco install ffmpeg` hoặc `imageio-ffmpeg`; code tự phát hiện và báo rõ |
| R11 | RAM 16 GB dùng chung cho Spark + YOLO + Kafka + HDFS | Treo máy | `spark.driver.memory=2g`, `local[4]`, `shuffle.partitions=8`; không chạy YOLO song song với Spark streaming khi benchmark |
| R12 | Giấy phép video không rõ | Vi phạm bản quyền | `sources.csv` bắt buộc trường `license`; validate từ chối video thiếu nguồn ở chế độ `--strict` |
| R13 | Video lớn bị commit vào git | Repo phình to | Thêm `dataset/raw/`, `dataset/processed/`, `models/*.pt` vào `.gitignore` |
| R14 | Docker daemon chưa chạy | Phương án dự phòng không sẵn sàng | Hướng dẫn bật Docker Desktop; native là phương án chính |

---

## 8. File đề xuất tạo mới / chỉnh sửa

### 8.1. Chỉnh sửa (Giai đoạn 2, cần xác nhận)

| File | Thay đổi |
|---|---|
| `.gitignore` | Sửa `source/image_retrieval/*` → `source/video_retrieval/*` (hoặc `legacy/...`); thêm `dataset/raw/`, `dataset/processed/`, `models/*.pt`, `spark-warehouse/`, `checkpoints/`, `.venv-vi/` |
| `README.md` | Viết lại theo đề tài mới; chuyển hướng dẫn cũ vào `legacy/README.md` (giữ nguyên nội dung) |
| `.env.example` | Thêm nhóm biến `VI_*` (dataset dir, YOLO model, sample interval, Spark memory, Kafka bootstrap, HDFS URI, storage backend). Giữ nguyên biến cũ nếu giữ legacy |

### 8.2. Tạo mới theo giai đoạn

| Giai đoạn | File |
|---|---|
| 2 – Dataset | `config/settings.py`, `src/common/*`, `src/dataset/{create_dataset,scan_videos,validate_dataset,download_videos,dataset_report}.py`, `dataset/README.md`, `dataset/metadata/*` (chỉ header + mô tả, không có video giả), `requirements.txt`, `requirements-dev.txt`, `tests/test_dataset_*.py` |
| 3 – Video | `src/video_processing/{video_reader,frame_extractor,timestamp_manager}.py`, `tests/test_video_*.py` |
| 4 – Detection | `src/object_detection/yolo_detector.py`, `src/storage/parquet_storage.py`, `scripts/run_detection.py`, `tests/test_detection_*.py` |
| 5 – Big Data | `src/big_data/{spark_session,spark_batch,temporal_aggregation}.py`, `src/storage/hdfs_storage.py`, `config/spark-defaults.conf`, `scripts/benchmark.py`, `docker/docker-compose.yml` |
| 6 – Kafka | `src/streaming/{events,producer,consumer}.py`, `src/big_data/spark_streaming.py`, `scripts/kafka_setup.py` |
| 7 – UI | `app/streamlit_app.py`, `src/query/object_search.py` |
| 8 – Hoàn thiện | `scripts/run_pipeline.py`, integration tests, `README.md`, `docs/DEPLOYMENT.md`, `docs/BENCHMARK.md` (số liệu đo thật) |

---

## 9. Kế hoạch triển khai và tiêu chí nghiệm thu

| GĐ | Nội dung | Tiêu chí nghiệm thu (đều phải kiểm chứng bằng lệnh thực tế) |
|---|---|---|
| 1 | Phân tích | File này; không file nào của project bị sửa/xóa ✅ |
| 2 | Dataset + venv 3.11 | `create_dataset.py` tạo đủ cây thư mục, README, metadata mẫu khi **0 video**; `scan_videos.py` + `validate_dataset.py` + `dataset_report.py` chạy không lỗi với 0 video; test phát hiện: file hỏng, trùng sha256, không đọc được, < 10 phút, thiếu metadata, sai đường dẫn |
| 3 | Video processing | Đọc stream không nạp toàn bộ video (RAM ổn định khi đọc video ≥ 10 phút); timestamp theo PTS, test timestamp nhảy/VFR; sampling theo giây |
| 4 | YOLO | Detection events Parquet đúng schema; resume sau khi ngắt giữa chừng không trùng dữ liệu; đo FPS xử lý thật |
| 5 | Spark + HDFS | Temporal segments bằng Spark khớp 100% bản pandas trên cùng dữ liệu; Spark SQL truy vấn theo class; ghi/đọc HDFS (hoặc fallback đã ghi rõ); benchmark tuần tự vs Spark |
| 6 | Kafka | Producer gửi event đúng schema; Structured Streaming ghi Parquet có checkpoint; test broker ngắt kết nối (retry + báo lỗi rõ, không mất im lặng) |
| 7 | Streamlit | Upload → xử lý → tìm `car` → bảng đoạn → phát video tại `start` |
| 8 | Kiểm thử & tài liệu | 12 kịch bản test trong đề bài; bảng benchmark 7 chỉ số với số liệu đo thật trên dataset thật |

---

## 10. Quyết định đã chốt (2026-11-23)

| # | Câu hỏi | Quyết định | Đã thực hiện |
|---|---|---|---|
| 1 | Code ImageRetrieval cũ | Chuyển vào `legacy/` | `legacy/source/image_retrieval/` + `legacy/frontend/` (giữ bố cục tương đối nên `parents[2]`, `.env`, `frontend/dist` vẫn đúng); `.env` cũ → `legacy/.env`; 32 test legacy vẫn OK |
| 2 | Môi trường | `.venv-vi` Python 3.11, giữ `.venv` cũ | Đã tạo, `pandas` ghim 2.3.3 cho PySpark 4.2.0 |
| 3 | Nguồn video | Công cụ tải YouTube CC BY bằng `yt-dlp` | `src/dataset/download_videos.py`, đã kiểm thử thật (tải 1 video 22 phút) |
| 4 | FFmpeg | `imageio-ffmpeg` qua pip | FFmpeg 9.0.2 đi kèm, dùng cho probe metadata và merge của yt-dlp |

Vấn đề B1–B3 (`.gitignore`, README, tên `image_retrieval`) đã được xử lý trong Giai đoạn 2.
Lưu ý: `imageio-ffmpeg` **không kèm `ffprobe`** → Giai đoạn 3 lấy PTS bằng OpenCV
(`CAP_PROP_POS_MSEC`) và `ffmpeg -i`/`showinfo` thay vì ffprobe.

---

## 11. Thay đổi phạm vi: chỉ video ca nhạc (2026-11-23)

**Yêu cầu mới:** chỉ truy vấn trong **video ca nhạc**, ưu tiên show **Anh Trai Vượt Ngàn Chông Gai**
(ATVNCG), MV/concert khác vẫn được. Mọi yêu cầu cũ (≥ 10 phút, giấy phép rõ ràng, metadata,
validation, không dữ liệu giả) vẫn giữ nguyên.

### 11.1. Giấy phép – kết quả kiểm tra thực tế

| Nguồn | Kết quả | Quyết định |
|---|---|---|
| ATVNCG trên kênh **YeaH1 Channel** (`UC9G7oeTsVSoD2Z2Lt7eKdjQ`, ~610 nghìn người theo dõi; YeaH1 là đơn vị sản xuất) | Nhiều video 20–47 phút gắn **CC BY** | Nhận; preset `atvncg` **chỉ tin kênh này** |
| Video cùng kênh YeaH1 nhưng show khác (Gia Đình Haha, Quán Nhà Haha) | CC BY nhưng không phải ca nhạc | Loại bằng `title_keywords` (so khớp không dấu) |
| Concert do kênh cá nhân đăng lại (fan cam, reupload) | Có gắn CC BY | **Không dùng**: người đăng có thể không sở hữu bản quyền nên CC BY có thể vô hiệu |
| Concert trên kênh trùng tên nghệ sĩ (Phú Quang, Ausomapto) | CC BY | Nhận; ghi `channel_id`. Quyền sở hữu chỉ suy ra từ tên kênh, chưa xác minh độc lập |
| MV đơn lẻ | Thường dài 3–5 phút | Bị loại vì < 10 phút. Chỉ nhận live session/tổng hợp ≥ 10 phút |

Lưu ý: CC BY cho phép sử dụng nhưng **bắt buộc ghi công** (tác giả, URL nằm trong `sources.csv`).
Bài hát trong chương trình còn có quyền tác giả âm nhạc riêng → chỉ dùng cho phân tích học thuật,
**không phân phối lại file video**. Vì vậy file video bị `.gitignore`, chỉ metadata được commit.

### 11.2. Thay đổi đã thực hiện

- `videos.csv` / `sources.csv` thêm `category`, `series`, `channel_id`.
- `VI_ALLOWED_CATEGORIES=music_show,music_video,concert` → validation cảnh báo `out_of_domain`.
- `config/music_presets.json`: preset `atvncg` (kênh tin cậy + từ khóa), `concert`, `music_video`.
- `download_videos.py`: `--preset`, `--category`, `--series`, `--trusted-channel`, `--title-keyword`, `--limit`.
- `scan_videos.py --remove`: gỡ video ngoài domain. Video walking tour trước đó đã được chuyển
  vào `raw/removed/`.

### 11.3. Ảnh hưởng tới Giai đoạn 4 (mô hình nhận diện) – CẦN QUYẾT ĐỊNH

80 lớp COCO của YOLO chuẩn **không có** micro, guitar, trống, piano, violin, tức các đối tượng đặc
trưng của video ca nhạc. Trong video ca nhạc, COCO chỉ hữu ích cho `person`, `chair`, `tv`,
`cell phone`, `bottle`…

| Phương án | Ưu | Nhược |
|---|---|---|
| A. YOLOv8n **Open Images V7** (`yolov8n-oiv7.pt`, ~600 lớp, có sẵn trong Ultralytics) | Nano, chạy CPU, dự kiến có Microphone, Guitar, Drum, Piano, Violin… | Độ chính xác từng lớp thấp hơn COCO; **danh sách lớp sẽ kiểm chứng khi cài ultralytics** |
| B. YOLO-World (open-vocabulary, tự đặt lớp "microphone", "guitar", "stage light") | Linh hoạt theo từ khóa | Chậm hơn đáng kể trên CPU |
| C. Giữ COCO | Đơn giản, nhanh | Không tìm được nhạc cụ hay micro |

Đề xuất: **A** làm mặc định (cân bằng tốc độ CPU và độ phủ lớp), B làm tùy chọn để so sánh.
Lúc đó `annotations/labels.csv` sẽ được sinh lại từ danh sách lớp thật của mô hình.
