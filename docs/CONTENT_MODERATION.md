# Kiểm duyệt an toàn nội dung

Đây là tiêu chí **bắt buộc**. MV đạt mọi điều kiện khác vẫn bị loại nếu chứa nội dung vi phạm.
Cấu hình nằm trong mục `content_safety` của [config/dataset_criteria.json](../config/dataset_criteria.json).
Từ nhạy cảm chỉ nằm trong [config/moderation/lexicons/](../config/moderation/README.md).

> **Giới hạn quan trọng.** Hệ thống **không** phát hiện được mọi ngôn ngữ và mọi dạng nội dung vi
> phạm. Kiểm tra tự động chỉ **gắn cờ** hoặc **loại khi bằng chứng rõ ràng**. Kết luận "an toàn"
> cho toàn video luôn do người duyệt ghi sau khi xem hết video.

## 1. Nhóm vi phạm

| Nhóm (`content_safety_reason`) | Nội dung | Không tự động loại |
|---|---|---|
| `profanity_or_abusive_language` | Tục tĩu, chửi thề, xúc phạm, miệt thị, quấy rối, đe dọa, kích động thù hằn hoặc phân biệt đối xử; kể cả viết tắt, biến thể, tiếng lóng | Từ đa nghĩa trong ngữ cảnh trung tính |
| `violent_or_gory_content` | Đánh đập gây thương tích nghiêm trọng, giết người, máu me, thi thể, tra tấn, dùng vũ khí tấn công, tự gây thương tích | Cảnh hành động, võ thuật, đấu kiếm, hiệu ứng điện ảnh không nghiêm trọng |
| `violence_incitement` | Kêu gọi hoặc cổ súy bạo lực, khủng bố, bạo loạn, dùng vũ khí giải quyết xung đột, tự sát | MV phản ánh chiến tranh, xung đột, thông điệp phản chiến |
| `explicit_or_obscene_content` | Khiêu dâm, khỏa thân khiêu dâm, hành vi tình dục rõ ràng, nội dung liên quan trẻ vị thành niên, bóc lột hoặc xâm hại tình dục | Trang phục gợi cảm, vũ đạo quyến rũ, thời trang thông thường |
| `age_restricted` | Nguồn (YouTube) giới hạn độ tuổi 18+ | Có thể đổi thành chỉ gắn cờ bằng `auto_reject.age_restricted = "review"` |

## 2. Các bước kiểm tra và trạng thái triển khai

| Bước (`scope`) | Phương pháp | Trạng thái | Tự động loại? |
|---|---|---|---|
| `metadata`: tiêu đề, tên file, tags | Lexicon đa ngôn ngữ + nhận diện ngôn ngữ (`lingua`) + khử biến thể | ✅ M1 | Có, chỉ với từ `strong` và khi ngôn ngữ của trường **khớp** ngôn ngữ của lexicon |
| `metadata`: mô tả | như trên | ✅ M1 | **Không**, chỉ gắn cờ (mô tả có thể là trích dẫn hoặc ngữ cảnh trung tính) |
| `age_restriction` | `age_limit` của nguồn; lỗi "confirm your age" khi tải | ✅ M1 | Có (cấu hình được) |
| `subtitles` | Phụ đề YouTube: mọi phụ đề do người đăng cung cấp + phụ đề tự động **chỉ của ngôn ngữ gốc** (bỏ bản dịch máy); kiểm từng câu, có timestamp | ✅ M2 | Không, chỉ gắn cờ |
| `ocr` | EasyOCR (vi, en) trên frame lấy mẫu mỗi 2 s, rộng 960 px; bỏ qua frame không đổi cảnh | ✅ M2 | Có nếu từ `strong` cùng ngôn ngữ xuất hiện ở ≥ 2 frame |
| `audio_transcript` | faster-whisper rồi kiểm duyệt văn bản | ⏳ M3 | Không, chỉ gắn cờ |
| `visual` | NudeNet (khỏa thân) + CLIP zero-shot (máu me, bạo lực, vũ khí) | ⏳ M4 | Không, chỉ gắn cờ cho tới khi hiệu chỉnh ở M5 |

Bước chưa triển khai luôn hiện `unknown` với `moderation_method = <scope>:not_implemented`. Bước đã
có nhưng chưa chạy cho video đó hiện `<scope>:not_run`. Hệ thống không giả lập kết quả.

Chạy các bước nặng (đọc video `pending_review`, chỉ ghi vào `dataset/moderation/`):

```powershell
python -m pip install -r requirements-moderation.txt     # một lần: torch CPU + EasyOCR
python -m src.dataset.moderate_videos                     # phụ đề + OCR cho mọi video cần kiểm
python -m src.dataset.moderate_videos --stages ocr --video MV_003 --ocr-every 1.0 --force
```

Có checkpoint theo (video, bước, sha256, phương pháp). Video bị loại vì lỗi kỹ thuật được bỏ qua.

## 3. Ngôn ngữ được hỗ trợ

- **Nhận diện ngôn ngữ:** `lingua` (khoảng 75 ngôn ngữ, offline). Văn bản ngắn hoặc trộn ngôn ngữ với
  độ tin cậy dưới `min_confidence` (mặc định 0,5) được ghi `unknown`. Ví dụ đo thực tế: "크래시2
  OST Smash It" ra Swahili 0,12; "ok" ra Zulu 0,06.
- **OCR:** EasyOCR nạp `vi` và `en` (cấu hình `content_safety.ocr.languages`); chữ ngoài các ngôn ngữ này không đọc được.
- **Phụ đề:** theo mã ngôn ngữ của file phụ đề.
- **Kiểm duyệt văn bản:** chỉ những ngôn ngữ **có file lexicon** trong `config/moderation/lexicons/`.
  **Hiện chưa có lexicon nào có hiệu lực.** Lexicon nháp tiếng Việt và tiếng Anh (112 mục, 12 `strong`)
  nằm ở `lexicons/drafts/` và chờ nhóm duyệt. Cho tới khi đó, mọi video đều ở
  `text_moderation_status=unknown` hoặc `unsupported`. Ngôn ngữ không có lexicon → `unsupported` →
  `pending_review`.

## 4. Quy tắc quyết định

| Tình huống | Kết quả |
|---|---|
| Từ `strong` trên tiêu đề, tags hoặc tên file, cùng ngôn ngữ với lexicon | `rejected` |
| Giới hạn tuổi 18+ | `rejected` (mặc định) |
| Từ `ambiguous`, từ trong mô tả, từ của lexicon khác ngôn ngữ, cờ từ STT hoặc mô hình hình ảnh | `pending_review`, cờ hiện trong `review_notes` |
| Ngôn ngữ chưa xác định hoặc không hỗ trợ; công cụ không phân tích được | `pending_review` |
| Hai công cụ cùng một bước kết luận ngược nhau | `pending_review`, `content_safety_conflict` |
| Người duyệt ghi `safe` nhưng có cờ tự động loại | `pending_review`, `content_safety_conflict`. Nếu đó là nhận diện sai thì **sửa lexicon**; nếu không thì ghi `flagged` |
| Người duyệt ghi `flagged` kèm nhóm vi phạm | `rejected` với lý do tương ứng |
| Người duyệt ghi `safe` sau khi xem toàn video, không có mâu thuẫn | Đạt tiêu chí an toàn nội dung |
| Vi phạm nhiều nhóm | `rejected`, ghi **tất cả** lý do |

Video chỉ `accepted` khi `content_safety_status = safe`. Giá trị này chỉ do người duyệt ghi.

## 5. Trường metadata

| Trường | Giá trị | Ghi chú |
|---|---|---|
| `content_safety_status` | `safe` / `flagged` / `unknown` / `not_applicable` | `safe` chỉ do người duyệt |
| `text_`, `audio_`, `visual_moderation_status` | `safe` / `flagged` / `unknown` / `not_applicable` | `safe` = phương pháp đã chạy trên toàn bộ dữ liệu của bước và không thấy dấu hiệu nào; **không** phải kết luận video an toàn |
| `language_detection_status` | `detected` / `unsupported` / `unknown` / `not_applicable` | Bộ giá trị riêng: đây không phải trạng thái an toàn |
| `detected_languages` | mã ISO 639-1, ngăn bằng `;` | Trường bổ sung |
| `profanity_detected`, `violent_content_detected`, `violence_incitement_detected`, `explicit_content_detected` | `true` / `false` / `unknown` | Không bao giờ mặc định `false`. `false` chỉ khi người duyệt ghi `safe` hoặc mọi bước trong phạm vi của nhóm đã chạy và `safe` |
| `content_safety_score` | số hoặc trống | Chỉ ghi khi công cụ có điểm. Ý nghĩa, ngưỡng và phiên bản nằm trong `moderation_method`. **Không tự coi là xác suất vi phạm.** Lexicon không có điểm nên để trống |
| `content_safety_reason` | mã, ngăn bằng `;` | Cả lý do loại lẫn lý do gắn cờ |
| `moderation_confidence` | `high` / `low` / `unknown` | `high`: từ strong, giới hạn tuổi, hoặc kết luận của người duyệt; `low`: chỉ có cờ mơ hồ hoặc từ mô hình |
| `moderation_method` | `scope:công cụ@phiên bản(tham số)` cho từng bước | |
| `review_required` | `true` / `false` | |
| `review_notes` | cờ (đã che từ), timestamp, bước chưa kiểm tra | |

Chi tiết từng bước được lưu trong `dataset/moderation/<video_id>/<scope>.json`, gắn với sha256 của
file. Thư mục này **không commit**: transcript và OCR có thể chứa lời bài hát có bản quyền.

## 6. Hướng dẫn xác minh thủ công

1. Xuất phiếu duyệt: `python -m src.dataset.review_video --export --reviewer "Tên"`. Cột
   `moderation_flags` liệt kê cờ và timestamp.
2. **Xem toàn bộ video**, không chỉ các timestamp bị gắn cờ, vì lấy mẫu có thể bỏ sót cảnh ngắn.
   Bật phụ đề nếu có.
3. Với lời hát bằng ngôn ngữ bạn không hiểu, đọc lời chính thức (mô tả, trang của nghệ sĩ) hoặc
   bản dịch đáng tin. Nếu vẫn không đánh giá được thì để `unknown` (video ở `pending_review`).
4. Phân biệt mô tả hoặc phản ánh với cổ súy; hành động điện ảnh với bạo lực nghiêm trọng; gợi cảm
   với khiêu dâm (xem cột "Không tự động loại" ở mục 1).
5. Ghi kết luận:
   ```powershell
   python -m src.dataset.review_video MV_003 --reviewer "Tên" --content-safety safe `
       --evidence "Đã xem toàn video 4:12, không có nội dung vi phạm"
   python -m src.dataset.review_video MV_004 --reviewer "Tên" --content-safety flagged `
       --safety-categories "violent_or_gory_content" --evidence "Cảnh máu me 02:10–02:18"
   ```

## 7. False positive / false negative đã biết

| Loại | Ví dụ | Cách giảm |
|---|---|---|
| FP | Từ đa nghĩa, tên riêng; cùng chuỗi nhưng khác nghĩa ở ngôn ngữ khác | Mức `ambiguous`; từ `strong` chỉ tự loại khi ngôn ngữ khớp; sửa lexicon |
| FP | Lời bài hát hoặc trích dẫn trong mô tả | Mô tả chỉ gắn cờ |
| FP (M3) | Whisper bịa chữ khi gặp nhạc nền | STT chỉ gắn cờ |
| FP (M4) | CLIP nhầm ánh sáng đỏ, đạo cụ, cảnh võ thuật | Mô hình chỉ gắn cờ |
| FN | Ngôn ngữ chưa có lexicon; tiếng lóng hoặc cách viết né bộ lọc mới | `unsupported` → `pending_review`; cập nhật lexicon |
| FN | Tiêu đề quá ngắn để nhận diện ngôn ngữ | `unknown` → người duyệt |
| FN | Cảnh ngắn nằm giữa hai frame lấy mẫu; lời bị nhạc che | Người duyệt xem toàn video |
| FN (M2) | Chữ cách điệu / logo: thử trên video thật, 71/97 hộp chữ không đọc được | Tỷ lệ không đọc được cao → `unknown`, không kết luận an toàn |
| FN (M2) | Chữ Thái, Hàn, Nhật, Trung… (EasyOCR chỉ nạp vi + en) | Hộp chữ độ tin cậy thấp → `unknown` |
| FP (M2) | OCR đọc sai thành một từ trong lexicon | Chỉ tự loại khi khớp ở ≥ 2 frame và đúng ngôn ngữ |

## 8. Hiệu năng và tài nguyên

| Bước | Tài nguyên | Thời gian |
|---|---|---|
| M1: metadata + nhận diện ngôn ngữ | `lingua` khoảng 0,1–1 GB RAM khi đã nạp mô hình; nạp lần đầu khoảng 7 giây (đo trên máy dự án) | Dưới 1 giây mỗi video |
| M2: OCR (đo thật trên 60 s video quay thật, CPU máy dự án) | torch CPU + EasyOCR, RAM khoảng 0,9 GB; tải mô hình lần đầu mất 4,7 phút | **2,4 s mỗi frame** (rộng 960 px), tức 71 s cho 30 frame. Với lấy mẫu 2 s, một MV 4 phút cần khoảng 4–5 phút. Bước bỏ frame giống nhau chỉ giúp khi cảnh ít thay đổi (đo được: bỏ 1/30 frame) |
| M2: phụ đề | yt-dlp, không tải video | vài giây mỗi video; YouTube có thể giới hạn tốc độ (HTTP 429) → `unknown` |
| M3–M4 | faster-whisper, NudeNet, open_clip | chưa đo |
