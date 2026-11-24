# Dataset – Video Intelligence (domain: video ca nhạc)

Thư mục này được tạo bởi `python -m src.dataset.create_dataset`. Không chỉnh sửa
CSV bằng tay – mọi file metadata được sinh lại bằng các công cụ trong `src/dataset/`.

## Yêu cầu video

| Tiêu chí | Giá trị |
|---|---|
| Domain | Chỉ video ca nhạc – `category` thuộc: `music_show`, `music_video`, `concert` |
| Ưu tiên | Show **Anh Trai Vượt Ngàn Chông Gai** (`music_show`); MV / concert khác vẫn được |
| Thời lượng tối thiểu / video | 10 phút (video ngắn hơn bị cảnh báo `too_short`) |
| Mục tiêu | 10–30 phút/video, 10–20 video, tổng 2–5 giờ |
| Định dạng hỗ trợ | .mp4, .avi, .mkv, .mov |
| Nguồn | Giấy phép rõ ràng (CC BY do chính chủ đăng, hoặc video tự cung cấp có khai báo) |

Không dùng video giả, không lặp một video ngắn để "làm to" dataset.
Video ngoài domain bị cảnh báo `out_of_domain` (lỗi khi `validate_dataset --strict`).

## Cách đưa video vào

**Cách 1 – Tải video YouTube giấy phép CC BY** (tự kiểm tra giấy phép, thời lượng, kênh):

```powershell
# Anh Trai Vượt Ngàn Chông Gai – chỉ nhận video từ kênh chính chủ YeaH1
python -m src.dataset.download_videos --preset atvncg --max-results 20 --dry-run
python -m src.dataset.download_videos --preset atvncg --url https://www.youtube.com/watch?v=XXXXXXXXXXX
# Concert / MV khác
python -m src.dataset.download_videos --preset concert --dry-run
python -m src.dataset.download_videos --category concert --url https://www.youtube.com/watch?v=XXXXXXXXXXX
```

**Cách 2 – Video tự cung cấp:** chép file vào `dataset/raw/videos/`, rồi khai báo nguồn,
giấy phép và thể loại (không cần sửa CSV):

```powershell
python -m src.dataset.scan_videos --register-source my_video.mp4 --source user --license "..." --category music_video --title "..."
```

**Gỡ video khỏi dataset:** `python -m src.dataset.scan_videos --remove video_00X`
(file được chuyển vào `raw/removed/`; thêm `--delete-file` để xóa hẳn).

Sau đó luôn chạy:

```powershell
python -m src.dataset.scan_videos        # quét, đo metadata, gán video_id
python -m src.dataset.validate_dataset   # kiểm tra lỗi / trùng / thời lượng / giấy phép
python -m src.dataset.dataset_report     # thống kê tổng quan
```

## Cấu trúc

| Thư mục / file | Nội dung |
|---|---|
| `raw/videos/` | Video gốc, **không chỉnh sửa**. Bị `.gitignore`. |
| `metadata/videos.csv` | Một dòng/video: video_id, đường dẫn, thời lượng, FPS, frame, độ phân giải, dung lượng, nguồn, giấy phép, trạng thái xử lý, sha256 |
| `metadata/sources.csv` | Nguồn gốc & giấy phép từng file (URL, tác giả, license) |
| `metadata/dataset_info.json` | Tên, phiên bản, tổng số video, tổng thời lượng, dung lượng, định dạng, ngày tạo |
| `metadata/validation_report.json` | Kết quả lần validate gần nhất |
| `processed/frames/` | Chỉ frame minh họa (không lưu toàn bộ frame) |
| `processed/detections/` | Detection events YOLO (Parquet) – Giai đoạn 4 |
| `processed/temporal_segments/` | Các khoảng thời gian xuất hiện (Parquet) – Giai đoạn 5 |
| `processed/embeddings/` | Dự phòng cho mở rộng |
| `annotations/labels.csv` | 80 lớp COCO mà YOLO pretrained hỗ trợ (kèm tên tiếng Việt) |
| `annotations/ground_truth.csv` | Khoảng thời gian gán nhãn tay để đánh giá |
| `splits/*.csv` | Chia train/validation/test theo video (deterministic) |

`processing_status`: `pending` → `validated` / `invalid` → `processing` → `detected` → `aggregated`.
