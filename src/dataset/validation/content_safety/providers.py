"""Kiểu dữ liệu chung cho mọi bước kiểm duyệt (metadata, phụ đề, OCR, âm thanh, hình ảnh, giới hạn tuổi).

Mỗi bước (``scope``) trả về một ``CheckResult``. Các bước chưa triển khai trả về
``not_implemented()`` – trạng thái ``unknown``, KHÔNG giả lập kết quả "an toàn".

Ý nghĩa ``status`` của một bước:
* ``flagged``  – có ít nhất một dấu hiệu (xem ``flags``);
* ``safe``     – phương pháp đã chạy trên TOÀN BỘ dữ liệu của bước và không thấy dấu hiệu nào. Đây
  KHÔNG phải kết luận video an toàn (chỉ người duyệt kết luận được điều đó);
* ``unknown``  – không đủ dữ liệu / ngôn ngữ không hỗ trợ / công cụ lỗi / chưa triển khai;
* ``not_applicable`` – bước không áp dụng (vd. video không có phụ đề).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

SCOPES = ("metadata", "subtitles", "ocr", "audio_transcript", "visual", "age_restriction")


@dataclass
class Flag:
    category: str               # một trong SAFETY_CATEGORIES, hoặc "age_restricted"
    reason: str                 # mã lý do (schema.REJECTION_REASONS)
    level: str                  # strong / ambiguous / model
    auto_reject: bool           # được phép tự động rejected theo chính sách
    field: str = ""             # title / description / frame / …
    evidence: str = ""          # đoạn khớp ĐÃ CHE (an toàn để ghi vào metadata)
    language: str = ""
    timestamp: float | None = None
    score: float | None = None
    note: str = ""

    def describe(self) -> str:
        where = f"@{self.timestamp:.1f}s" if self.timestamp is not None else ""
        lang = f"[{self.language}]" if self.language else ""
        extra = f" – {self.note}" if self.note else ""
        return f"{self.category}:{self.level}:{self.field}{where}{lang}='{self.evidence}'{extra}"


@dataclass
class CheckResult:
    scope: str
    status: str
    method: str
    flags: list[Flag] = field(default_factory=list)
    covered_categories: list[str] = field(default_factory=list)  # nhóm mà bước này đánh giá được
    languages: list[str] = field(default_factory=list)
    language_status: str = "not_applicable"
    score: float | None = None
    score_semantics: str = ""     # ý nghĩa điểm, ngưỡng, phiên bản – bắt buộc nếu có score
    threshold: float | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CheckResult":
        data = dict(data)
        data["flags"] = [Flag(**f) for f in data.get("flags", [])]
        return cls(**data)


def not_implemented(scope: str, milestone: str = "") -> CheckResult:
    later = f" (dự kiến {milestone})" if milestone else ""
    return CheckResult(scope, "unknown", "not_implemented",
                       notes=[f"{scope}: chưa triển khai{later} – không có kết quả kiểm tra"])
