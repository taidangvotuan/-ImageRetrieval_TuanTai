# Báo cáo Dataset – VideoIntelligence-MV v0.3.0

Kiểm tra gần nhất: 2026-11-25T01:59:45+00:00. Mọi số liệu “hợp lệ” chỉ tính video **accepted**.

## Tổng quan

| Chỉ số | Giá trị |
|---|---|
| Tổng số video được kiểm tra | 0 |
| **Accepted** (hợp lệ) | **0** |
| Rejected (bị loại) | 0 |
| Pending review (chờ xác minh) | 0 |
| Bị loại do AI-generated | 0 |
| Bị loại do hậu trường | 0 |
| Bị loại do lyric video | 0 |
| Sai thời lượng (ngoài 150–420 s) | 0 |
| Trùng lặp | 0 |
| Tổng thời lượng Dataset hợp lệ | 00:00:00 |
| MV accepted trong khoảng mục tiêu 180–300 s | 0 |
| Giấy phép (license_status) | - |
| Nguồn gốc (source_status) | - |

## Kiểm duyệt an toàn nội dung

Tự động chỉ gắn cờ hoặc loại khi bằng chứng rõ ràng; `safe` cho toàn video chỉ do người duyệt ghi. Bước chưa triển khai hiện `unknown` – xem `docs/CONTENT_MODERATION.md`.

| Chỉ số | Giá trị |
|---|---|
| Bị loại do ngôn từ tục tĩu / xúc phạm | 0 |
| Bị loại do bạo lực / máu me | 0 |
| Bị loại do kích động bạo lực | 0 |
| Bị loại do khiêu dâm | 0 |
| Bị loại do giới hạn tuổi 18+ | 0 |
| Cần người duyệt xem | 0 |
| `content_safety_status` | - |
| `text_moderation_status` | - |
| `audio_moderation_status` | - |
| `visual_moderation_status` | - |
| `language_detection_status` | - |

## Tiến độ theo mốc (chỉ video accepted)

Mốc hiện tại: **chưa đạt mốc nào**.

| Mốc | Trạng thái | Frame ước tính @1 fps | @2 fps |
|---|---|---|---|
| 5 MV | ⏳ chưa | ~1,200 | ~2,400 |
| 10 MV | ⏳ chưa | ~2,400 | ~4,800 |
| 30 MV | ⏳ chưa | ~7,200 | ~14,400 |
| 50 MV | ⏳ chưa | ~12,000 | ~24,000 |

> Số frame là **ước tính** (giả định MV trung bình 4 phút), không phải số đo.

**Dataset chưa có MV nào.** Xem `dataset/README.md` để đưa MV vào.

