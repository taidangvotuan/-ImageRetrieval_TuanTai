# Lexicon kiểm duyệt nội dung

Thư mục `lexicons/` chứa danh sách từ ngữ vi phạm, **một file CSV cho mỗi ngôn ngữ**. Tên file là
mã ISO 639-1, ví dụ `vi.csv`, `en.csv`, `th.csv`. Đây là nơi **duy nhất** chứa từ nhạy cảm: source
code và test không chép từ nào trong đó.

File `_TEMPLATE.csv` là mẫu và không được nạp (file bắt đầu bằng `_` bị bỏ qua).

## Lexicon nháp (`lexicons/drafts/`)

`drafts/vi.csv` và `drafts/en.csv` là **danh sách khởi đầu do Claude soạn, chưa được duyệt**:
112 mục, trong đó 12 mục `strong` và 100 mục `ambiguous`. Thư mục con **không được nạp**, nên nháp
không ảnh hưởng tới kết quả. Quy trình đưa vào dùng:

1. Nhóm duyệt từng dòng: giữ, sửa `level` / `match`, xóa hoặc bổ sung. Ghi tên người duyệt vào `added_by`.
2. Thử trên dữ liệu thật mà không ảnh hưởng tới kho:
   `$env:VI_LEXICON_DIR="config/moderation/lexicons/drafts"; python -m src.dataset.validate_dataset --no-update`
3. Chép file đã duyệt ra `lexicons/` (ví dụ `lexicons/vi.csv`) rồi commit. Từ lúc đó lexicon có hiệu lực.

## Định dạng

| Cột | Bắt buộc | Giá trị |
|---|---|---|
| `term` | có | Từ hoặc cụm từ. Viết bình thường, có dấu hay không dấu đều được (hệ thống tự chuẩn hóa) |
| `category` | có | `profanity_or_abusive_language` / `violent_or_gory_content` / `violence_incitement` / `explicit_or_obscene_content` |
| `level` | có | `strong`: từ **không mơ hồ**, khớp trong tiêu đề/tags/tên file thì tự động `rejected`.<br>`ambiguous`: có nghĩa trung tính, là tên riêng hoặc tùy ngữ cảnh, nên chỉ gắn cờ và đưa vào hàng chờ duyệt |
| `match` | không | `normalized` (mặc định): bỏ dấu, nên "phia sau" khớp "phía sau".<br>`exact`: **giữ dấu**. **Bắt buộc với từ tiếng Việt ngắn** mà khi bỏ dấu sẽ trùng từ thường (bỏ dấu thì "lồn", "lớn" và "lon bia" đều thành `lon`; "cặc" và "các" thành `cac`; "đĩ" và "đi" thành `di`). Đổi lại, cách viết không dấu của những từ này sẽ không bị bắt |
| `notes` | không | Vì sao chọn mức này, nguồn tham khảo |
| `added_by` | nên có | Người thêm, để biết ai chịu trách nhiệm cho mỗi mục |

Nếu có nghi ngờ, hãy chọn `ambiguous`. Gắn cờ sai chỉ tốn thời gian duyệt, nhưng tự loại sai thì
mất MV hợp lệ.

## Cách hệ thống dùng lexicon

1. Nhận diện ngôn ngữ của từng đoạn văn bản (`lingua`). Đoạn có độ tin cậy dưới ngưỡng được ghi
   `language_detection_status=unknown`.
2. Ngôn ngữ **không có file lexicon** được ghi `unsupported`. Kiểm tra văn bản cho đoạn đó là
   `unknown` và video ở `pending_review`. Hệ thống không bao giờ coi "không khớp từ nào" là "an toàn"
   khi không có lexicon.
3. Văn bản được khử biến thể trước khi so khớp: leetspeak (`0→o`, `@→a`, …), ký tự lặp
   (`aaaa→aa`), chữ bị tách bằng dấu cách hoặc dấu chấm (`b.a.d` → `bad`). Xem `text_variants`
   trong `config/dataset_criteria.json`.
4. So khớp nguyên từ theo ranh giới, giống luật loại trừ Dataset.

Khi một từ gây loại nhầm, **sửa lexicon** (đổi sang `ambiguous` hoặc xóa) thay vì ghi đè từng
video. Mọi thay đổi sẽ có trong lịch sử git.

## Giới hạn

Danh sách từ khóa không hiểu được ý đồ, mỉa mai, tiếng lóng mới hay ngôn ngữ chưa có file. Vì vậy
kết luận `content_safety_status=safe` cho toàn video luôn do người duyệt ghi sau khi xem hết video
(xem `docs/CONTENT_MODERATION.md`).
