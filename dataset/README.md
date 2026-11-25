# Dataset – Video Intelligence (Music Video chính thức)

Thư mục này được tạo bởi `python -m src.dataset.create_dataset`. Không sửa CSV bằng tay:
metadata được sinh lại bằng các công cụ trong `src/dataset/`. Tiêu chí nằm trong **một file
duy nhất** `config/dataset_criteria.json` – xem `docs/DATASET_CRITERIA.md`.

## Tiêu chí

| Tiêu chí | Yêu cầu |
|---|---|
| Loại | Music Video **chính thức**, hoàn chỉnh, quay thực tế (không phải AI-generated) |
| Định dạng | Chỉ MP4 – định dạng khác bị `rejected: not_mp4`, không tự chuyển đổi |
| Thời lượng | Luật cứng 150–420 s; khoảng mục tiêu 180–300 s (3–5 phút, chỉ để thống kê) |
| Độ phân giải | ≥ 720p (cạnh dài ≥ 1280 px) – thấp hơn bị `rejected: low_resolution` |
| Loại trừ | AI-generated, hậu trường, lyric video, teaser/trailer, reaction/review, tổng hợp/medley, sân khấu/live, audio/visualizer, fan made/reupload, bản cover, trùng lặp, file lỗi |
| Nguồn | Hợp pháp, có quyền sử dụng – **phải được người duyệt xác minh** |
| An toàn nội dung | Không tục tĩu/xúc phạm, bạo lực/máu me, kích động bạo lực, khiêu dâm, giới hạn tuổi 18+ – xem `docs/CONTENT_MODERATION.md` |
| Quy mô | Mốc 5 → 10 → 30 → 50 MV accepted (30 chính thức, 50 mở rộng) |

Không dùng video giả, không cắt/lặp video để đạt thời lượng, không tạo metadata giả.

## Trạng thái xác minh

| validation_status | Ý nghĩa |
|---|---|
| `accepted` | Không vi phạm tiêu chí nào VÀ người duyệt đã xác nhận: MV chính thức, không AI, không hậu trường, không lyrics, giấy phép, nguồn gốc, an toàn nội dung (đã xem toàn video). **Chỉ trạng thái này được đưa vào pipeline.** |
| `rejected` | Vi phạm ít nhất một tiêu chí – lý do trong `rejection_reason` |
| `pending_review` | Chưa đủ căn cứ – mục còn thiếu trong `verification_notes` |

Luật tự động chỉ có thể **loại** (từ khóa rõ nghĩa trong tiêu đề, lỗi kỹ thuật). Không có video
nào tự trở thành `accepted`: AI-generated, nguồn gốc và giấy phép phải do người xem và xác nhận.

## Quy trình đưa MV vào

```powershell
# 1) Tìm MV CC BY (chỉ đọc metadata, KHÔNG tải) -> metadata/download_candidates.csv
python -m src.dataset.download_videos --preset music_video --max-results 30 --dry-run
# 2) Chọn URL có screen_result=pass hoặc review, chép vào approved_urls.txt rồi tải
python -m src.dataset.download_videos --url-file approved_urls.txt
#    (hoặc MV tự cung cấp: chép vào raw/videos/ rồi khai báo nguồn)
python -m src.dataset.scan_videos --register-source my_mv.mp4 --source user --license "..." `
    --title "..." --author "..." --source-url "..." --description "..."
# 3) Quét + kiểm tra -> accepted / rejected / pending_review
python -m src.dataset.scan_videos
python -m src.dataset.validate_dataset
# 4) Duyệt tay các video pending_review (xem video, kiểm tra kênh, giấy phép)
python -m src.dataset.review_video --export --reviewer "Tên người duyệt"
#    điền validation/review_sheet.csv rồi:
python -m src.dataset.review_video --import validation/review_sheet.csv --reviewer "Tên người duyệt"
# 5) Báo cáo
python -m src.dataset.dataset_report
```

**Gỡ video:** `python -m src.dataset.scan_videos --remove MV_00X --reason "..."` – file chuyển vào
`archive/videos/` (ngoài dataset/), nguồn và giấy phép giữ trong `archive/archive_manifest.csv`.
Video trùng lặp **không bao giờ bị tự động xóa**: bản sau bị `rejected: duplicate_*` và được liệt
kê trong báo cáo để xử lý thủ công.

## Cấu trúc

| Thư mục / file | Nội dung |
|---|---|
| `raw/videos/` | MV gốc, giữ tên file gốc, **không chỉnh sửa**. Bị `.gitignore`. Không di chuyển theo trạng thái |
| `metadata/videos.csv` | Một dòng/MV: kỹ thuật + nguồn + kết quả xác minh (schema v3) |
| `metadata/sources.csv` | Nguồn khai báo (URL, tác giả, kênh, giấy phép) – khai báo chưa phải xác minh |
| `metadata/source_info/*.json` | Metadata gốc từ nguồn (tiêu đề, mô tả, tags, kênh) – căn cứ xác minh |
| `metadata/reviews.csv` | Quyết định duyệt tay (chỉ ghi thêm, gắn sha256, có người duyệt + căn cứ) |
| `metadata/dataset_info.json` | Tổng hợp: số video theo trạng thái, thời lượng accepted, schema_version |
| `metadata/download_candidates.csv` | Kết quả `--dry-run` gần nhất (không commit) |
| `metadata/backups/` | Bản sao lưu trước mỗi lần migration (không commit) |
| `validation/accepted.csv`, `rejected.csv`, `pending_review.csv` | Danh sách theo trạng thái (sinh từ metadata) |
| `validation/review_sheet.csv` | Phiếu duyệt xuất bởi `review_video --export` |
| `reports/dataset_report.{md,json,csv}` | Báo cáo Dataset |
| `reports/validation_report.json` | Chi tiết lần kiểm tra gần nhất, trùng lặp cần xử lý |
| `moderation/<video_id>/*.json` | Kết quả kiểm duyệt nội dung theo bước (không commit – có thể chứa lời bài hát) |
| `processed/frames/`, `detections/`, `temporal_segments/` | Kết quả xử lý – CHỈ cho video accepted (GĐ3–5) |
| `annotations/labels.csv` | 80 lớp COCO; `is_target=yes` là 10 lớp mục tiêu |
| `annotations/ground_truth.csv` | Nhãn gán tay để đánh giá – xem `annotations/annotation_guide.md` |
| `splits/*.csv` | Chia theo video, **chỉ video accepted** |

`processing_status` (độc lập với xác minh): `not_started` → `processing` → `detected` → `aggregated` / `failed`.
