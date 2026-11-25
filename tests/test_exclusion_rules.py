"""Unit test bộ máy luật loại trừ (config/dataset_criteria.json)."""
from __future__ import annotations

import pytest

from src.dataset.validation.exclusion_rules import STRONG, WEAK, RuleSet, normalize


@pytest.fixture(scope="module")
def rules() -> RuleSet:
    return RuleSet.from_settings()


def strong(rules, title="", description="", uploader="", tags=None):
    return {s.category for s in rules.evaluate({"title": title, "description": description, "tags": tags or []},
                                               uploader=uploader) if s.level == STRONG}


def weak(rules, title="", description="", uploader=""):
    return {s.category for s in rules.evaluate({"title": title, "description": description},
                                               uploader=uploader) if s.level == WEAK}


# ---------------------------------------------------------------- chuẩn hóa
@pytest.mark.parametrize("raw,expected", [
    ("Phía Sau Hậu-Trường!", "phia sau hau truong"),
    ("BEHIND-THE-SCENES", "behind the scenes"),
    ("  Making   of\t(MV) ", "making of mv"),
    ("Lời Bài Hát", "loi bai hat"),
    ("Đêm Nhạc", "dem nhac"),
    ("100% AI", "100 ai"),
    ("BTS (방탄소년단)", "bts 방탄소년단"),
])
def test_normalize(raw, expected):
    assert normalize(raw) == expected


def test_every_rule_category_loaded(rules):
    assert {r.category for r in rules.rules} >= {"ai_generated", "behind_the_scenes", "lyrics_video", "teaser",
                                                 "reaction", "compilation", "live_performance", "audio_only",
                                                 "unofficial"}


# ---------------------------------------------------------------- hậu trường
@pytest.mark.parametrize("title", [
    "Song X – Behind The Scenes", "Song X behind-the-scenes", "Song X | Making Of", "Song X making-of",
    "Hậu trường MV Song X", "Phía sau hậu trường MV Song X", "hau truong mv song x",
    "Song X Behind The Music Video", "Song X MV Behind the Scenes", "[BTS] Song X MV shooting",
])
def test_behind_the_scenes_rejected(rules, title):
    assert "behind_the_scenes" in strong(rules, title)


@pytest.mark.parametrize("title,uploader", [
    ("BTS (방탄소년단) 'Dynamite' Official MV", ""),
    ("BTS 'Butter' Official MV", "HYBE LABELS"),
    ("Butter - BTS | Official MV", "BANGTANTV"),
])
def test_bts_artist_name_not_rejected(rules, title, uploader):
    assert "behind_the_scenes" not in strong(rules, title, uploader=uploader)
    assert "behind_the_scenes" not in weak(rules, title, uploader=uploader)


def test_bare_bts_is_only_weak(rules):
    # "BTS" đứng một mình (không rõ nghệ sĩ hay hậu trường) -> chỉ đưa vào hàng chờ duyệt.
    assert "behind_the_scenes" not in strong(rules, "Song X (Official MV) BTS")
    assert "behind_the_scenes" in weak(rules, "Song X (Official MV) BTS")


# ---------------------------------------------------------------- lyrics
@pytest.mark.parametrize("title", [
    "Song X (Lyric Video)", "Song X | Lyrics", "Song X Official Lyric Video", "Song X – Lời Bài Hát",
    "Song X Karaoke Lyrics", "Song X (Song Lyrics)", "Song X lyrics video", "Song X Official Lyrics",
])
def test_lyrics_rejected(rules, title):
    assert "lyrics_video" in strong(rules, title)


def test_official_mv_with_subtitles_not_auto_rejected(rules):
    assert "lyrics_video" not in strong(rules, "Song X (Official MV) [Vietsub + Lời bài hát]")
    assert "lyrics_video" in weak(rules, "Song X (Official MV) [Vietsub + Lời bài hát]")


def test_lyrics_in_description_ignored(rules):
    assert strong(rules, "Song X (Official MV)", "Lời bài hát:\nEm ơi em ... lyrics bên dưới") == set()
    assert weak(rules, "Song X (Official MV)", "Lời bài hát:\nEm ơi em ... lyrics bên dưới") == set()


def test_whole_word_matching(rules):
    assert strong(rules, "Lyrical Dreams (Official MV)") == set()
    assert strong(rules, "Audiovisual (Official MV)") == set()
    assert strong(rules, "Lively (Official Music Video)") == set()


# ---------------------------------------------------------------- AI
@pytest.mark.parametrize("title", [
    "Song X (AI Generated Music Video)", "Song X – AI-generated video", "Song X | made with AI",
    "Song X | Video tạo bằng AI", "Song X – tạo bởi trí tuệ nhân tạo", "ANTHEM - an AI Star Trek music video",
    "Song X | AI Indie Folk Song", "MV AI Song X",
])
def test_ai_generated_rejected(rules, title):
    assert "ai_generated" in strong(rules, title)


def test_bare_ai_word_is_not_a_rule(rules):
    for title in ("AI (Official MV)", "Ai Biết (Official MV)", "Song by Ai Otsuka (Official Music Video)",
                  "Tình Ái (Official MV)"):
        assert "ai_generated" not in strong(rules, title)


@pytest.mark.parametrize("title", [
    "Ai Biết Official MV",                       # 'ai' = who, không có ngoặc ngăn cách
    "Người Ấy Là Ai | Official MV",
    "MV Ai Là Người Thương Em",
    "AI LÀ NGƯỜI THƯƠNG EM OFFICIAL MV",         # viết hoa toàn bộ: không phân biệt được -> chỉ weak
    "Tình Ái Music Video",
])
def test_vietnamese_ai_word_not_auto_rejected(rules, title):
    assert "ai_generated" not in strong(rules, title)


def test_rules_do_not_cross_segment_boundaries(rules):
    assert "behind_the_scenes" not in strong(rules, "Song X (Official MV) BTS")
    assert "lyrics_video" in strong(rules, "Song X – Official Lyric Video")


def test_ai_tool_name_is_weak(rules):
    assert "ai_generated" not in strong(rules, "Papers and Pens – Official Music Video | Dukamer x Suno")
    assert "ai_generated" in weak(rules, "Papers and Pens – Official Music Video | Dukamer x Suno")


def test_ai_post_production_not_rejected(rules):
    assert "ai_generated" not in strong(rules, "Song X (Official MV) – AI-assisted color grading")
    assert "ai_generated" not in strong(rules, "Song X (Official Music Video) [AI Upscaled 4K]")


def test_ai_in_description_only_weak(rules):
    title = "Song X (Official MV)"
    assert strong(rules, title, "This is an AI generated music video") == set()
    assert "ai_generated" in weak(rules, title, "This is an AI generated music video")


def test_vfx_cgi_not_flagged(rules):
    assert strong(rules, "Song X (Official MV) – VFX & CGI by Studio Y") == set()


# ---------------------------------------------------------------- nhóm khác
@pytest.mark.parametrize("title,category", [
    ("Song X Official Teaser", "teaser"), ("Song X MV Trailer", "teaser"),
    ("Reaction to Song X MV", "reaction"), ("Song X MV Review", "reaction"),
    ("Song X MV – Phân tích ý nghĩa", "reaction"),
    ("Top hits compilation 2024", "compilation"), ("Liên khúc nhạc trẻ", "compilation"),
    ("Song X (Live)", "live_performance"), ("Song X Live Performance", "live_performance"),
    ("Song X – Sân khấu", "live_performance"), ("Song X Fancam", "live_performance"),
    ("Song X (Official Audio)", "audio_only"), ("Song X [Visualizer]", "audio_only"),
    ("Song X – Fan made MV", "unofficial"), ("KIRA – Tribute music video", "unofficial"),
])
def test_other_exclusions(rules, title, category):
    assert category in strong(rules, title)


def test_live_word_alone_not_rejected(rules):
    assert "live_performance" not in strong(rules, "Live Your Life (Official MV)")


def test_tags_matches_downgraded(rules):
    assert strong(rules, "Song X (Official MV)", tags=["behind the scenes", "lyrics"]) == set()


def test_official_marker(rules):
    assert rules.has_official_marker("Song X | OFFICIAL MV")
    assert rules.has_official_marker("Song X (Official Music Video)")
    assert not rules.has_official_marker("Unlocking Me | Atmospheric Indie Rock")


# ---------------------------------------------------------------- cover
@pytest.mark.parametrize("title", [
    "Song X (Cover)", "Song X – Acoustic Cover", "Song X | Piano Cover", "Song X covered by Y",
    "Song X Dance Cover", "Song X – Bản Cover", "Song X | cover lại bởi Y", "Hát lại Song X",
    "Song X (Official MV) – cover by Y",
])
def test_cover_rejected(rules, title):
    assert "cover_version" in strong(rules, title)


@pytest.mark.parametrize("title", [
    "Discover Yourself (Official MV)", "Undercover (Official Music Video)", "Recovery (Official MV)",
])
def test_cover_whole_word_only(rules, title):
    assert "cover_version" not in strong(rules, title)


def test_cover_in_description_ignored(rules):
    assert strong(rules, "Song X (Official MV)", "Thumbnail cover art by Y") == set()
