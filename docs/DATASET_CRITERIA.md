# Tiêu chí Dataset và quy trình xác minh

Tài liệu này mô tả **một tiêu chuẩn Dataset duy nhất** mà mọi thành phần của hệ thống tuân theo.
Giá trị cụ thể (ngưỡng, từ khóa) nằm trong [config/dataset_criteria.json](../config/dataset_criteria.json).
Muốn đổi tiêu chí thì sửa file đó, không sửa trong code.

## 1. Video được chấp nhận

Dataset chỉ gồm **Music Video chính thức**, hoàn chỉnh, quay thực tế, có nguồn hợp pháp:

| Tiêu chí | Luật | Khi vi phạm |
|---|---|---|
| Thời lượng | 150 ≤ `duration_seconds` ≤ 420 (2:30–7:00) | `rejected: duration_out_of_range` |
| Thời lượng mục tiêu | 180–300 s (3–5 phút) | chỉ thống kê, không loại |
| Định dạng | MP4 | `rejected: not_mp4`, không tự chuyển đổi |
| Độ phân giải | cạnh dài ≥ 1280 px (720p trở lên) | `rejected: low_resolution` |
| Đọc được | giải mã thử frame đầu/giữa/cuối | `rejected: unreadable` |
| Không trùng | sha256, tên file, nguồn (URL / YouTube id) | `rejected: duplicate_*`, file KHÔNG bị xóa |
| Nội dung | MV chính thức, không phải các nhóm ở mục 2 | `rejected: <nhóm>` |
| Giấy phép, nguồn | người duyệt xác minh | `pending_review` cho tới khi xác minh |
| **An toàn nội dung** | không tục tĩu/xúc phạm, bạo lực/máu me, kích động bạo lực, khiêu dâm; không giới hạn tuổi 18+ – xem [CONTENT_MODERATION.md](CONTENT_MODERATION.md) | `rejected: <nhóm>` hoặc `pending_review` |

Không cắt video dài, không lặp video ngắn, không tạo video hay metadata giả.

## 2. Nhóm bị loại trừ

| Nhóm (`rejection_reason`) | Ví dụ khớp (tiêu đề) | Không bị loại nhầm |
|---|---|---|
| `ai_generated` | "AI Generated Music Video", "made with AI", "tạo bằng AI", "an AI Star Trek music video" | "Ai Biết (Official MV)", "Tình Ái", "AI-assisted color grading", "AI Upscaled", AI nhắc trong mô tả |
| `behind_the_scenes` | "Behind the Scenes", "behind-the-scenes", "Making of", "Hậu trường", "Phía sau hậu trường", "[BTS] … MV shooting" | "BTS (방탄소년단) 'Dynamite' Official MV", video do kênh BANGTANTV / HYBE LABELS đăng |
| `lyrics_video` | "Lyric Video", "Lyrics", "Lời bài hát", "Karaoke", "Official Lyric Video" | "Official MV [Vietsub + Lời bài hát]", lời bài hát nằm trong mô tả, "Lyrical Dreams" |
| `teaser` | Teaser, Trailer, Preview, Snippet | |
| `reaction_or_review` | Reaction, Review, Phân tích, Giải mã | |
| `compilation` | Compilation, Tổng hợp, Medley, Liên khúc, Playlist, 1 Hour | |
| `live_performance` | "(Live)", Live Performance, Sân khấu, Fancam, Dance Practice | "Live Your Life (Official MV)" |
| `audio_only` | Official Audio, Visualizer, Slowed, Sped up | "Audiovisual (Official MV)" |
| `unofficial_or_fan_made` | Fan made, Tribute, FMV, AMV, Reupload | |
| `cover_version` | Cover, Acoustic / Piano / Dance Cover, "covered by", Bản cover, Cover lại, Hát lại | "Discover", "Undercover", "Recovery"; chữ "cover" trong mô tả (vd. cover art) |

### Cách so khớp

1. **Chuẩn hóa:** bỏ dấu tiếng Việt, chuyển chữ thường, đổi dấu câu và gạch nối giữa chữ thành khoảng trắng. Nhờ vậy "Behind-The-Scenes", "behind the scenes" và "hau truong" đều được nhận ra.
2. **Nguyên cụm theo ranh giới từ:** "lyric" không khớp "lyrical", "audio" không khớp "audiovisual".
3. **Không khớp xuyên đoạn:** ngoặc, `|`, `:`, dấu gạch đứng riêng ngăn cách các đoạn. Vì vậy "Song (Official MV) BTS" không bị coi là "mv bts".
4. **Mức độ:**
   - `strong` trên tiêu đề hoặc tên file: tự động `rejected`.
   - `weak`: chỉ ghi chú cho người duyệt, video vẫn ở `pending_review`.
   - Mọi khớp trong **mô tả và tags** đều bị hạ xuống `weak`.
   - Nhóm lyrics **bỏ qua mô tả hoàn toàn**, vì mô tả MV thường chứa sẵn lời bài hát.
5. **Không có luật nào dùng chữ "AI" đứng một mình.** Luật khoảng cách ("AI" cách "music video/MV/song/video" tối đa 3 từ) và cụm "MV AI"/"AI MV" chỉ là `strong` khi tiêu đề viết đúng **"AI" in hoa** và tiêu đề không viết hoa toàn bộ. Lý do: trong tiếng Việt, "ai" (ai đó) và "ái" (sau khi bỏ dấu) là từ thông dụng.
6. **Hạ mức theo ngữ cảnh:**
   - Có dấu hiệu AI chỉ hỗ trợ hậu kỳ (assisted, upscale, remaster, phục chế): hạ xuống `weak`.
   - MV chính thức có phụ đề (Vietsub, phụ đề) chứa chữ "lời bài hát": hạ xuống `weak`.
   - Chữ "BTS" khi ngữ cảnh cho thấy đó là tên nghệ sĩ: bỏ qua.
   - VFX và CGI không bị nhắm tới.

Luật tự động chỉ có thể **loại**. Nó không bao giờ xác nhận một video là hợp lệ.

## 3. Trạng thái

| Trường | Giá trị | Ý nghĩa |
|---|---|---|
| `validation_status` | `accepted` / `rejected` / `pending_review` | Chỉ `accepted` vào pipeline |
| `content_type` | `official_music_video`, `behind_the_scenes`, `lyrics_video`, `teaser`, `reaction`, `other`, `unknown` | `unknown` = chưa xác định (mở rộng so với đề bài, vì không được tự gán MV chính thức) |
| `ai_generated_status`, `behind_the_scenes_status`, `lyrics_status`, `license_status`, `source_status` | `confirmed` / `rejected` / `unknown` / `not_applicable` | `confirmed` = **đã xác minh video ĐẠT** tiêu chí đó. `not_applicable` không đủ để `accepted` |
| `processing_status` | `not_started` → `processing` → `detected` → `aggregated` / `failed` | Độc lập với xác minh |

Video chỉ được `accepted` khi: không có lý do loại nào, `content_type = official_music_video`, cả 5 trường trạng thái đều `confirmed`, **`content_safety_status = safe` (người duyệt đã xem toàn video, không có mâu thuẫn kiểm duyệt)**, metadata khớp file hiện tại (sha256), và file đã được đọc thử thành công.

Người duyệt có thể ghi đè kết luận của **luật nội dung** khi có căn cứ. Ví dụ: bài hát có tên "Lyrics" bị luật bắt nhầm. Người duyệt **không thể** ghi đè lỗi kỹ thuật (thời lượng, MP4, độ phân giải, trùng lặp, file lỗi).

## 4. Quy trình duyệt tay

```powershell
python -m src.dataset.review_video --export --reviewer "Tên người duyệt"
# Mở dataset/validation/review_sheet.csv, điền cho từng video:
#   content_type, ai_generated_status, behind_the_scenes_status, lyrics_status,
#   license_status, source_status, evidence (bắt buộc), notes
python -m src.dataset.review_video --import dataset/validation/review_sheet.csv --reviewer "Tên người duyệt"
```

Gợi ý kiểm tra từng tiêu chí:

| Tiêu chí | Cần kiểm tra |
|---|---|
| MV chính thức | Có hình quay, cốt truyện hoặc biểu diễn dựng cho bài hát. Không phải lyric, audio tĩnh, sân khấu hay bản cắt ghép |
| Không phải AI-generated | Xem video: người thật, bối cảnh thật, chuyển động tự nhiên? Kiểm tra mô tả, tag và kênh có nhắc Sora, Runway, Kling, Suno… không. Nếu chỉ dùng AI để chỉnh màu hay upscale thì vẫn đạt |
| Không phải hậu trường / lyrics | Xem nội dung, không chỉ dựa vào tiêu đề |
| Giấy phép | Nhãn giấy phép trên trang nguồn (CC BY…) hoặc văn bản cho phép sử dụng |
| Nguồn gốc | Kênh đăng có phải của nghệ sĩ, hãng phát hành hay đơn vị sản xuất không (kênh xác minh, đường dẫn từ trang chính thức). Kênh reup thì ghi `rejected` |
| An toàn nội dung | Xem **toàn bộ** video và các timestamp trong `moderation_flags`; ghi `--content-safety safe` hoặc `flagged --safety-categories …` (xem [CONTENT_MODERATION.md](CONTENT_MODERATION.md) §6) |

Mỗi quyết định được **ghi thêm** vào `dataset/metadata/reviews.csv`, gồm người duyệt, thời điểm, căn cứ và sha256 của file. Nếu file bị thay đổi, quyết định cũ tự hết hiệu lực.

## 5. Hợp đồng cho các bước xử lý (GĐ3–7)

Mọi module phía sau chỉ được dùng `src/dataset/validation/accepted_registry.py`:

| Hàm | Dùng ở |
|---|---|
| `accepted_videos()` | Danh sách đầu vào cho frame extraction / YOLO / benchmark. Hàm kiểm tra lại trạng thái, file, dung lượng, MP4 và thời lượng ngay lúc gọi |
| `assert_accepted(video_id)` | Trước khi xử lý hoặc phát sự kiện Kafka cho một video |
| `accepted_ids()` + `filter_records()` | Khi đọc detection, segment hay sự kiện đã lưu. Spark dùng inner join với bảng accepted_ids |
| `check_time_range(video, start, end)` | Timestamp và segment phải nằm trong thời lượng video |

Nếu một video bị loại sau khi đã xử lý, kết quả cũ của nó **tự động bị lọc khỏi** mọi truy vấn và benchmark mà không cần xóa dữ liệu.

## 6. Migration

`python -m src.dataset.migrate_metadata` (scan, validate và create cũng tự gọi khi cần) nâng metadata schema cũ lên schema hiện hành:
- Sao lưu toàn bộ `metadata/` vào `metadata/backups/<thời điểm>_v<cũ>/`.
- Thêm cột mới với giá trị `unknown` / `pending_review`. Không sinh ra giá trị `confirmed` nào.
- Không xóa dòng nào.
- Chạy lại nhiều lần vẫn an toàn.
