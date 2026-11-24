"""Định nghĩa cột của các file metadata / annotation / split.

Giữ ở một nơi duy nhất để mọi công cụ (scan, validate, report, download) và các
giai đoạn sau (Spark) dùng cùng một schema.
"""
from __future__ import annotations

VIDEO_COLUMNS = [
    "video_id",            # video_001, video_002 ... gán một lần, không đổi
    "filename",
    "filepath",            # tương đối so với thư mục dataset, dạng POSIX
    "duration_seconds",
    "fps",
    "total_frames",
    "width",
    "height",
    "file_size",           # byte
    "source",              # youtube / user / ... (lấy từ sources.csv)
    "license",
    "processing_status",   # xem PROCESSING_STATUSES
    "category",            # domain: music_show / music_video / concert (xem VI_ALLOWED_CATEGORIES)
    "series",              # tên chương trình, vd. "Anh Trai Vượt Ngàn Chông Gai"
    # Cột bổ sung phục vụ validation / resume:
    "codec",
    "container_duration_seconds",
    "sha256",
    "file_mtime",
    "scanned_at",
    "notes",
]

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
    "license_verified",    # yes / no
    "retrieved_at",
    "notes",
]

LABEL_COLUMNS = ["class_id", "object_name", "name_vi", "supported_by"]

GROUND_TRUTH_COLUMNS = [
    "video_id", "object_name", "start_seconds", "end_seconds", "annotator", "notes",
]

SPLIT_COLUMNS = ["video_id", "filename", "split"]

PROCESSING_STATUSES = (
    "pending",      # đã quét, chưa kiểm tra sâu
    "validated",    # qua validation
    "invalid",      # lỗi: không đọc được / hỏng / trùng
    "processing",   # (GĐ 4) đang chạy detection
    "detected",     # (GĐ 4) đã có detection
    "aggregated",   # (GĐ 5) đã có temporal segments
)

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
