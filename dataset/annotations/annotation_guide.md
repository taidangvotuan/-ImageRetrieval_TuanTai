# Hướng dẫn gán nhãn ground truth

Mục đích: đánh giá precision / recall / F1 và sai số thời gian của các khoảng xuất hiện mà hệ
thống tìm được. **Không bao giờ** sinh nhãn từ kết quả YOLO rồi dùng chính nhãn đó để đánh giá YOLO.

## Phạm vi

- Gán nhãn các MV thuộc `splits/test.csv` (bắt đầu với 5 MV).
- Chỉ gán các lớp đã chọn trước khi xem kết quả hệ thống (gợi ý: `person`, `car`, `chair`,
  `bottle`, `motorcycle`). Ghi danh sách lớp vào cột `notes` của dòng đầu tiên mỗi MV.

## Quy tắc

1. Xem MV bằng trình phát cho phép tua từng giây (VLC: phím `E` để tiến từng frame).
2. Một **lần xuất hiện** là khoảng liên tục mà đối tượng nhìn thấy được, dù chỉ một phần. Nếu
   đối tượng biến mất dưới 1 giây (chớp cắt cảnh) thì vẫn coi là liên tục.
3. Ghi `start_seconds` / `end_seconds` với độ chính xác 0,5 s.
4. Đối tượng quá nhỏ (dưới khoảng 2% chiều cao khung hình) hoặc bị mờ không nhận ra: không gán.
   Ghi chú lại nếu phân vân.
5. Mỗi người gán ghi tên vào cột `annotator`. Nếu có 2 người, người thứ hai kiểm tra lại ít nhất
   1 MV để ước lượng mức đồng thuận.

## Định dạng `ground_truth.csv`

| video_id | object_name | start_seconds | end_seconds | annotator | notes |
|---|---|---|---|---|---|

`object_name` phải đúng tên lớp trong `labels.csv`.

## Chỉ số sẽ tính (Giai đoạn 8)

- Mức giây: precision / recall / F1 trên lưới 1 giây.
- Mức khoảng: ghép khoảng dự đoán với nhãn theo tIoU ≥ 0,5, tính precision / recall / F1 và
  sai số |Δstart|, |Δend|. Sai số này luôn bị chặn dưới bởi chu kỳ lấy mẫu (1 s hoặc 0,5 s).
