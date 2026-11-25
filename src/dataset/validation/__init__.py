"""Cơ chế kiểm tra Dataset tập trung (tiêu chí trong ``config/dataset_criteria.json``).

* ``exclusion_rules``    – chuẩn hóa văn bản, so khớp luật loại trừ (strong / weak / ngữ cảnh)
* ``video_filter``       – suy ra content_type và trạng thái nội dung từ metadata nguồn
* ``metadata_validator`` – kiểm tra kỹ thuật: file, MP4, thời lượng, độ phân giải, trùng lặp
* ``review_store``       – quyết định duyệt tay (nguồn duy nhất của ``confirmed``)
* ``dataset_validator``  – kết luận accepted / rejected / pending_review, ghi metadata + danh sách
* ``accepted_registry``  – cổng kiểm soát: mọi bước xử lý phía sau chỉ nhận video accepted
"""
