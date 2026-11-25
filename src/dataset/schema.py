"""Định nghĩa cột của các file metadata / annotation / split.

Giữ ở một nơi duy nhất để mọi công cụ (scan, validate, report, download) và các
giai đoạn sau (Spark) dùng cùng một schema.
"""
from __future__ import annotations

# Phiên bản schema metadata. Tăng khi đổi cột; migrate_metadata nâng cấp dữ liệu cũ (có sao lưu).
SCHEMA_VERSION = 3

VIDEO_COLUMNS = [
    # --- Định danh & kỹ thuật
    "video_id",            # MV_001, MV_002 ... gán một lần, không đổi
    "filename",            # giữ tên gốc (vd. yt_<youtube_id>.mp4) để truy được nguồn
    "filepath",            # tương đối so với thư mục dataset, dạng POSIX
    "title",               # tiêu đề MV (từ sources.csv / source_info)
    "duration_seconds",
    "fps",
    "total_frames",
    "width",
    "height",
    "file_size",           # byte
    # --- Nguồn & giấy phép
    "source",              # youtube / user / ...
    "source_url",
    "license",             # giấy phép KHAI BÁO (vd. "CC BY") – chưa phải kết quả xác minh
    # --- Kết quả xác minh (xem VERIFICATION_VALUES / CONTENT_TYPES / VALIDATION_STATUSES)
    "content_type",
    "ai_generated_status",
    "behind_the_scenes_status",
    "lyrics_status",
    "license_status",      # giấy phép đã được người duyệt xác minh?
    "source_status",       # nguồn gốc (kênh chính chủ / quyền sử dụng) đã được xác minh?
    "validation_status",   # accepted / rejected / pending_review – CHỈ accepted vào pipeline
    "rejection_reason",    # mã lý do, ngăn bằng ';' (xem REJECTION_REASONS)
    "verification_notes",  # mục còn thiếu, dấu hiệu weak, ghi chú duyệt
    "reviewed_by",
    "reviewed_at",
    # --- An toàn nội dung (schema v3, xem docs/CONTENT_MODERATION.md)
    "content_safety_status",        # safe CHỈ do người duyệt; tự động: flagged / unknown
    "language_detection_status",    # detected / unsupported / unknown / not_applicable
    "detected_languages",           # mã ISO 639-1, ngăn bằng ';'
    "text_moderation_status",       # safe / flagged / unknown / not_applicable (theo từng bước)
    "audio_moderation_status",
    "visual_moderation_status",
    "profanity_detected",           # true / false / unknown – KHÔNG mặc định false
    "violent_content_detected",
    "violence_incitement_detected",
    "explicit_content_detected",
    "content_safety_score",         # chỉ ghi khi công cụ có điểm có nghĩa (xem moderation_method)
    "content_safety_reason",        # mã lý do, ngăn bằng ';'
    "moderation_confidence",        # high / low / unknown
    "moderation_method",            # công cụ@phiên bản, ngưỡng, ý nghĩa điểm – theo từng bước
    "review_required",              # true / false
    "review_notes",                 # dấu hiệu + timestamp cần người duyệt xem
    "processing_status",   # xem PROCESSING_STATUSES (độc lập với validation_status)
    # --- Cột phụ (tương thích / resume)
    "category",            # (cũ) nhãn do công cụ gán, KHÔNG phải kết quả xác minh
    "series",
    "codec",
    "container_duration_seconds",
    "probe_ok",            # yes/no: giải mã thử frame đầu/giữa/cuối (scan / validate sâu)
    "sha256",
    "file_mtime",
    "scanned_at",
    "notes",
]

# --- Giá trị hợp lệ
VALIDATION_STATUSES = ("accepted", "rejected", "pending_review")

# Kết quả kiểm tra TỪNG tiêu chí: confirmed = đã xác minh video ĐẠT tiêu chí (vd. không phải AI);
# rejected = vi phạm; unknown = chưa xác minh; not_applicable = không áp dụng (không đủ để accepted).
# Luật tự động chỉ được ghi "rejected", không bao giờ ghi "confirmed".
VERIFICATION_VALUES = ("confirmed", "rejected", "unknown", "not_applicable")
VERIFICATION_FIELDS = ("ai_generated_status", "behind_the_scenes_status", "lyrics_status",
                       "license_status", "source_status")

# --- An toàn nội dung
SAFETY_VALUES = ("safe", "flagged", "unknown", "not_applicable")
LANGUAGE_STATUSES = ("detected", "unsupported", "unknown", "not_applicable")
TRI_STATE = ("true", "false", "unknown")
SAFETY_CATEGORIES = ("profanity_or_abusive_language", "violent_or_gory_content", "violence_incitement",
                     "explicit_or_obscene_content")
SAFETY_FLAG_FIELDS = {"profanity_or_abusive_language": "profanity_detected",
                      "violent_or_gory_content": "violent_content_detected",
                      "violence_incitement": "violence_incitement_detected",
                      "explicit_or_obscene_content": "explicit_content_detected"}

# "unknown" là mở rộng: chưa xác định được loại nội dung (không tự gán official_music_video).
CONTENT_TYPES = ("official_music_video", "behind_the_scenes", "lyrics_video", "teaser", "reaction",
                 "other", "unknown")

REJECTION_REASONS = {
    # kỹ thuật – không thể ghi đè bằng duyệt tay
    "missing_file": "không tìm thấy file tại filepath",
    "unreadable": "không mở/giải mã được video",
    "missing_metadata": "thiếu trường metadata kỹ thuật (fps, frame, kích thước…)",
    "duration_unknown": "không xác định được thời lượng",
    "duration_out_of_range": "thời lượng ngoài khoảng cho phép",
    "not_mp4": "không phải định dạng MP4",
    "low_resolution": "độ phân giải dưới 720p",
    "duplicate_content": "trùng nội dung (sha256) với video khác",
    "duplicate_filename": "trùng tên file với video khác",
    "duplicate_source": "trùng nguồn (cùng URL) với video khác",
    # nội dung – từ luật strong hoặc từ người duyệt
    "ai_generated": "video được tạo bằng AI",
    "behind_the_scenes": "video hậu trường",
    "lyrics_video": "video lời bài hát",
    "teaser": "teaser / trailer / preview",
    "reaction_or_review": "reaction / review / phân tích",
    "compilation": "tổng hợp / medley / playlist",
    "live_performance": "sân khấu / biểu diễn trực tiếp",
    "audio_only": "audio / visualizer / ảnh tĩnh",
    "unofficial_or_fan_made": "fan made / tribute / reupload",
    "cover_version": "bản cover (không phải MV chính thức của bài hát)",
    "not_official_music_video": "người duyệt xác định không phải MV chính thức",
    "license_rejected": "giấy phép không hợp lệ",
    "source_rejected": "nguồn gốc không hợp lệ (reup / không có quyền)",
    # an toàn nội dung – từ lexicon strong trên metadata/OCR, giới hạn tuổi, hoặc người duyệt
    "profanity_or_abusive_language": "ngôn từ tục tĩu / xúc phạm / quấy rối / thù hằn",
    "violent_or_gory_content": "bạo lực / máu me / gây sốc",
    "violence_incitement": "kích động / cổ súy bạo lực",
    "explicit_or_obscene_content": "khiêu dâm / tình dục phản cảm",
    "age_restricted": "nguồn giới hạn độ tuổi (18+)",
}
TECHNICAL_REASONS = frozenset({
    "missing_file", "unreadable", "missing_metadata", "duration_unknown", "duration_out_of_range",
    "not_mp4", "low_resolution", "duplicate_content", "duplicate_filename", "duplicate_source",
})

# Giá trị mặc định khi đọc dữ liệu cũ thiếu cột (migration): KHÔNG có giá trị nào là "confirmed".
VIDEO_DEFAULTS = {
    "content_type": "unknown",
    "ai_generated_status": "unknown",
    "behind_the_scenes_status": "unknown",
    "lyrics_status": "unknown",
    "license_status": "unknown",
    "source_status": "unknown",
    "validation_status": "pending_review",
    "processing_status": "not_started",
    "content_safety_status": "unknown",
    "language_detection_status": "unknown",
    "text_moderation_status": "unknown",
    "audio_moderation_status": "unknown",
    "visual_moderation_status": "unknown",
    "profanity_detected": "unknown",
    "violent_content_detected": "unknown",
    "violence_incitement_detected": "unknown",
    "explicit_content_detected": "unknown",
    "moderation_confidence": "unknown",
    "review_required": "true",
}

# Kiểu dữ liệu cho Parquet / Spark (GĐ5) – sinh schema từ đây để thống nhất với CSV.
VIDEO_NUMERIC_COLUMNS = {
    "duration_seconds": "double", "fps": "double", "total_frames": "long", "width": "int",
    "height": "int", "file_size": "long", "container_duration_seconds": "double", "file_mtime": "long",
}


def normalize_video_row(row: dict) -> dict:
    """Bổ sung cột thiếu (dữ liệu schema cũ) bằng giá trị mặc định an toàn, giữ nguyên cột thừa."""
    out = {column: row.get(column, "") for column in VIDEO_COLUMNS}
    for key, default in VIDEO_DEFAULTS.items():
        if not str(out.get(key) or "").strip():
            out[key] = default
    for key, value in row.items():
        if key not in out:
            out[key] = value
    return out


SOURCE_COLUMNS = [
    "filename",
    "source",              # youtube / user / wikimedia / archive / other
    "source_url",
    "title",
    "author",
    "channel_id",          # kênh đăng tải – để kiểm chứng chủ sở hữu giấy phép
    "category",
    "series",
    "license",
    "license_url",
    "license_verified",    # (cũ) yes/no – chỉ là thông tin khai báo; xác minh thật nằm ở reviews.csv
    "retrieved_at",
    "notes",
]

LABEL_COLUMNS = ["class_id", "object_name", "name_vi", "supported_by", "is_target"]

GROUND_TRUTH_COLUMNS = [
    "video_id", "object_name", "start_seconds", "end_seconds", "annotator", "notes",
]

SPLIT_COLUMNS = ["video_id", "filename", "split"]

# Quyết định duyệt tay (metadata/reviews.csv): chỉ ghi thêm; ô trống = không thay đổi tiêu chí đó.
# Gắn với sha256: file bị thay đổi thì quyết định cũ hết hiệu lực.
REVIEW_COLUMNS = [
    "reviewed_at", "video_id", "filename", "sha256", "reviewer", "content_type",
    "ai_generated_status", "behind_the_scenes_status", "lyrics_status", "license_status",
    "source_status", "content_safety_status", "content_safety_categories", "evidence", "notes",
]

# Danh sách theo trạng thái (dataset/validation/*.csv) và báo cáo (dataset/reports/dataset_report.csv).
VALIDATION_LIST_COLUMNS = [
    "video_id", "filename", "title", "duration_seconds", "width", "height", "source", "source_url",
    "license", "license_status", "source_status", "content_type", "ai_generated_status",
    "behind_the_scenes_status", "lyrics_status", "content_safety_status", "text_moderation_status",
    "audio_moderation_status", "visual_moderation_status", "detected_languages", "content_safety_reason",
    "review_notes", "validation_status", "rejection_reason", "verification_notes", "reviewed_by", "reviewed_at",
]

# Video đã gỡ khỏi dataset (chuyển vào archive/): giữ lại nguồn + giấy phép để truy vết.
ARCHIVE_COLUMNS = [
    "video_id", "filename", "archived_path", "duration_seconds", "width", "height", "file_size",
    "sha256", "source", "source_url", "title", "author", "license", "category", "series",
    "removed_at", "reason",
]

# Trạng thái xử lý pipeline – KHÔNG mang nghĩa hợp lệ (đó là validation_status).
PROCESSING_STATUSES = (
    "not_started",  # chưa xử lý
    "processing",   # (GĐ3–4) đang trích frame / detection
    "detected",     # (GĐ4) đã có detection
    "aggregated",   # (GĐ5) đã có temporal segments
    "failed",       # lỗi khi xử lý
)
# Giá trị processing_status của schema v1 (lẫn với trạng thái hợp lệ) -> giá trị mới.
LEGACY_PROCESSING_STATUS = {"pending": "not_started", "validated": "not_started", "invalid": "not_started"}

# 80 lớp COCO mà YOLO pretrained (Ultralytics) nhận diện, theo đúng thứ tự class_id.
COCO_CLASSES = [
    ("person", "người"), ("bicycle", "xe đạp"), ("car", "ô tô"), ("motorcycle", "xe máy"),
    ("airplane", "máy bay"), ("bus", "xe buýt"), ("train", "tàu hỏa"), ("truck", "xe tải"),
    ("boat", "thuyền"), ("traffic light", "đèn giao thông"), ("fire hydrant", "trụ cứu hỏa"),
    ("stop sign", "biển dừng"), ("parking meter", "đồng hồ đỗ xe"), ("bench", "ghế băng"),
    ("bird", "chim"), ("cat", "mèo"), ("dog", "chó"), ("horse", "ngựa"), ("sheep", "cừu"),
    ("cow", "bò"), ("elephant", "voi"), ("bear", "gấu"), ("zebra", "ngựa vằn"),
    ("giraffe", "hươu cao cổ"), ("backpack", "ba lô"), ("umbrella", "ô (dù)"),
    ("handbag", "túi xách"), ("tie", "cà vạt"), ("suitcase", "va li"), ("frisbee", "đĩa ném"),
    ("skis", "ván trượt tuyết"), ("snowboard", "ván trượt tuyết đơn"), ("sports ball", "bóng"),
    ("kite", "diều"), ("baseball bat", "gậy bóng chày"), ("baseball glove", "găng bóng chày"),
    ("skateboard", "ván trượt"), ("surfboard", "ván lướt sóng"), ("tennis racket", "vợt tennis"),
    ("bottle", "chai"), ("wine glass", "ly rượu"), ("cup", "cốc"), ("fork", "nĩa"),
    ("knife", "dao"), ("spoon", "thìa"), ("bowl", "bát"), ("banana", "chuối"), ("apple", "táo"),
    ("sandwich", "bánh mì kẹp"), ("orange", "cam"), ("broccoli", "bông cải xanh"),
    ("carrot", "cà rốt"), ("hot dog", "xúc xích"), ("pizza", "pizza"), ("donut", "bánh donut"),
    ("cake", "bánh ngọt"), ("chair", "ghế"), ("couch", "ghế sofa"), ("potted plant", "chậu cây"),
    ("bed", "giường"), ("dining table", "bàn ăn"), ("toilet", "bồn cầu"), ("tv", "tivi"),
    ("laptop", "máy tính xách tay"), ("mouse", "chuột máy tính"), ("remote", "điều khiển"),
    ("keyboard", "bàn phím"), ("cell phone", "điện thoại"), ("microwave", "lò vi sóng"),
    ("oven", "lò nướng"), ("toaster", "máy nướng bánh"), ("sink", "bồn rửa"),
    ("refrigerator", "tủ lạnh"), ("book", "sách"), ("clock", "đồng hồ"), ("vase", "bình hoa"),
    ("scissors", "kéo"), ("teddy bear", "gấu bông"), ("hair drier", "máy sấy tóc"),
    ("toothbrush", "bàn chải đánh răng"),
]
assert len(COCO_CLASSES) == 80

# 10 lớp mục tiêu của đề tài (đều thuộc COCO). Các lớp COCO khác vẫn truy vấn được.
TARGET_CLASSES = ("person", "car", "motorcycle", "bus", "bicycle", "dog", "cat", "chair",
                  "bottle", "backpack")
assert set(TARGET_CLASSES) <= {name for name, _ in COCO_CLASSES}
