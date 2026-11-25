"""Kiểm duyệt an toàn nội dung (M1): khử biến thể, lexicon, nhận diện ngôn ngữ, chính sách quyết định.

Lexicon trong test dùng TỪ GIỮ CHỖ vô nghĩa (zorbix, glimpf, …) – test kiểm tra cơ chế, không chứa
từ nhạy cảm thật. Kết quả của các bước chưa triển khai (âm thanh, hình ảnh) được giả lập bằng
``CheckResult`` để kiểm tra LOGIC QUYẾT ĐỊNH; điều này không chứng minh độ chính xác của mô hình.
"""
from __future__ import annotations

import pytest

from config.settings import load_criteria
from src.dataset.validation.content_safety import summarize
from src.dataset.validation.content_safety import language as lang
from src.dataset.validation.content_safety.lexicon import Lexicon, LexiconError, mask
from src.dataset.validation.content_safety.providers import CheckResult, Flag, not_implemented
from src.dataset.validation.content_safety.text_moderator import TextModerator, check_age_restriction
from src.dataset.validation.content_safety.text_variants import variants
from src.dataset.validation.dataset_validator import decide
from src.dataset.validation.exclusion_rules import RuleSet
from src.dataset.validation.review_store import ReviewState
from src.dataset.validation.video_filter import assess_content
from tests.conftest import FULL_REVIEW

SAFETY = load_criteria()["content_safety"]
EN_TITLE = "Walking home through the quiet city streets tonight"
VI_TITLE = "Bài hát về những ngày mùa hè rực rỡ của chúng ta"


def write_lexicon(directory, rows_by_lang):
    directory.mkdir(parents=True, exist_ok=True)
    for code, rows in rows_by_lang.items():
        lines = ["term,category,level,notes,added_by"] + [f"{t},{c},{l},test,pytest" for t, c, l in rows]
        (directory / f"{code}.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return directory


@pytest.fixture
def lexicon_dir(tmp_path):
    return write_lexicon(tmp_path / "lexicons", {
        "en": [("zorbix", "profanity_or_abusive_language", "strong"),
               ("glimpf", "profanity_or_abusive_language", "ambiguous"),
               ("krallen attack", "violence_incitement", "strong"),
               ("velvex", "explicit_or_obscene_content", "strong")],
        "vi": [("đồ quạch", "profanity_or_abusive_language", "strong")],
    })


@pytest.fixture
def moderator(lexicon_dir):
    return TextModerator(Lexicon.load(lexicon_dir), SAFETY)


# ---------------------------------------------------------------- khử biến thể
@pytest.mark.parametrize("text", ["zorbix", "Z0RB1X", "zorrrrbix", "z.o.r.b.i.x", "z o r b i x", "ZORBIX!!!"])
def test_variants_catch_evasions(lexicon_dir, text):
    lex = Lexicon.load(lexicon_dir)
    assert [h.entry.term for h in lex.match(f"song {text} tonight", SAFETY["text_variants"])] == ["zorbix"]


def test_variants_do_not_merge_double_letters():
    assert "god" not in variants("good morning", SAFETY["text_variants"])


def test_whole_word_only(lexicon_dir):
    lex = Lexicon.load(lexicon_dir)
    assert lex.match("the zorbixian empire", SAFETY["text_variants"]) == []


def test_vietnamese_diacritics_optional(lexicon_dir):
    lex = Lexicon.load(lexicon_dir)
    assert lex.match("mày là DO QUACH", SAFETY["text_variants"])


# ---------------------------------------------------------------- lexicon
def test_lexicon_loading_and_template_skipped(tmp_path):
    d = tmp_path / "lex"
    write_lexicon(d, {"en": [("zorbix", "profanity_or_abusive_language", "strong")]})
    (d / "_TEMPLATE.csv").write_text("term,category,level\nignored,profanity_or_abusive_language,strong\n",
                                     encoding="utf-8")
    lex = Lexicon.load(d)
    assert lex.languages == ["en"] and [e.term for e in lex.entries] == ["zorbix"]
    assert Lexicon.load(tmp_path / "missing").languages == []


@pytest.mark.parametrize("row", [("x", "bad_category", "strong"), ("x", "profanity_or_abusive_language", "maybe")])
def test_lexicon_rejects_invalid_rows(tmp_path, row):
    write_lexicon(tmp_path / "lex", {"en": [row]})
    with pytest.raises(LexiconError):
        Lexicon.load(tmp_path / "lex")


def test_mask():
    assert mask("zorbix") == "z*****" and mask("a") == "a"


def test_repository_lexicons_are_valid():
    from config.settings import get_settings
    Lexicon.load(get_settings().lexicon_dir)          # không lỗi định dạng


# ---------------------------------------------------------------- nhận diện ngôn ngữ
def test_language_detection_does_not_guess():
    assert lang.detect("ok").status == "unknown"                       # quá ngắn
    assert lang.detect("크래시2 OST Smash It").status == "unknown"      # trộn ngôn ngữ, độ tin cậy thấp
    assert lang.detect("").status == "unknown"


def test_language_detection_detects_clear_text():
    assert lang.detect(VI_TITLE).languages == ["vi"]
    assert lang.detect(EN_TITLE).languages == ["en"]


# ---------------------------------------------------------------- text moderator
def test_strong_term_in_title_auto_rejects(moderator):
    r = moderator.check("metadata", {"title": f"{EN_TITLE} zorbix"})
    assert r.status == "flagged" and r.flags[0].auto_reject and r.flags[0].evidence == "z*****"


def test_strong_term_in_description_only_flags(moderator):
    r = moderator.check("metadata", {"title": EN_TITLE, "description": f"{EN_TITLE}. Lyrics: zorbix zorbix"})
    assert r.status == "flagged" and not any(f.auto_reject for f in r.flags)


def test_ambiguous_term_never_auto_rejects(moderator):
    r = moderator.check("metadata", {"title": f"{EN_TITLE} glimpf"})
    assert r.status == "flagged" and r.flags[0].level == "ambiguous" and not r.flags[0].auto_reject


def test_term_from_other_language_is_downgraded(moderator):
    # Cùng chuỗi có thể tục ở tiếng Anh nhưng trung tính ở ngôn ngữ khác -> chỉ gắn cờ.
    r = moderator.check("metadata", {"title": f"{VI_TITLE} zorbix"})
    assert r.flags and not r.flags[0].auto_reject and "≠ lexicon en" in r.flags[0].note


def test_tags_use_title_language_as_context(moderator):
    r = moderator.check("metadata", {"title": EN_TITLE, "tags": ["pop", "zorbix"]})
    assert r.flags[0].field == "tags" and r.flags[0].auto_reject


def test_no_match_with_supported_language_is_safe_for_that_step(moderator):
    r = moderator.check("metadata", {"title": EN_TITLE, "description": f"{EN_TITLE}. Thanks for watching."})
    assert r.status == "safe" and r.language_status == "detected"


def test_language_without_lexicon_is_unknown_not_safe(moderator):
    r = moderator.check("metadata", {"title": "Bonjour à tous les amis, voici notre nouvelle chanson d'été"})
    assert r.status == "unknown" and r.language_status == "unsupported"


def test_short_title_is_unknown(moderator):
    r = moderator.check("metadata", {"title": "TIM ĐAU"})
    assert r.status == "unknown"


def test_empty_repository_lexicon_never_says_safe():
    r = TextModerator(Lexicon([], "empty"), SAFETY).check("metadata", {"title": EN_TITLE})
    assert r.status == "unknown"


def test_age_restriction():
    assert check_age_restriction({"age_limit": 18}, SAFETY).flags[0].auto_reject
    assert check_age_restriction({"age_restricted": True}, SAFETY).status == "flagged"
    assert check_age_restriction({"age_limit": 0}, SAFETY).status == "safe"
    assert check_age_restriction({}, SAFETY).status == "unknown"
    review_only = {**SAFETY, "auto_reject": {**SAFETY["auto_reject"], "age_restricted": "review"}}
    assert not check_age_restriction({"age_limit": 18}, review_only).flags[0].auto_reject


# ---------------------------------------------------------------- quyết định (bảng §8)
RULES = RuleSet.from_settings()
METADATA_SAFE = CheckResult("metadata", "safe", "lexicon@test", covered_categories=[
    "profanity_or_abusive_language", "violent_or_gory_content", "violence_incitement", "explicit_or_obscene_content"],
    languages=["en"], language_status="detected")


def flagged(scope, category, level="model", auto=False, **kw):
    reason = SAFETY["categories"].get(category, {}).get("reason", category)
    return CheckResult(scope, "flagged", f"{scope}@test", flags=[Flag(category, reason, level, auto, **kw)])


def verdict(checks, review=None):
    review = dict(FULL_REVIEW if review is None else review)
    safety = summarize(checks, SAFETY, review)
    assessment = assess_content(RULES, title="Song X (Official MV)")
    return decide(assessment, [], ReviewState(decisions=review), RULES, safety)


def pending_checks():
    return [not_implemented(s) for s in ("subtitles", "ocr", "audio_transcript", "visual")]


def test_verified_mv_without_violations_accepted():
    d = verdict([METADATA_SAFE, CheckResult("age_restriction", "safe", "src")] + pending_checks())
    assert d.validation_status == "accepted"
    assert d.safety_fields["content_safety_status"] == "safe"
    assert d.safety_fields["profanity_detected"] == "false"          # người duyệt đã xem toàn video


def test_not_reviewed_is_pending_even_if_tools_found_nothing():
    review = {k: v for k, v in FULL_REVIEW.items() if k != "content_safety_status"}
    d = verdict([METADATA_SAFE] + pending_checks(), review)
    assert d.validation_status == "pending_review"
    assert d.safety_fields["content_safety_status"] == "unknown"
    assert d.safety_fields["profanity_detected"] == "unknown"         # không mặc định false
    assert d.safety_fields["review_required"] == "true"


@pytest.mark.parametrize("scope", ["audio_transcript", "subtitles"])
def test_explicit_profane_lyrics_flagged_then_rejected_by_reviewer(scope):
    check = flagged(scope, "profanity_or_abusive_language", timestamp=42.0, evidence="z*****")
    no_safety_review = {k: v for k, v in FULL_REVIEW.items() if k != "content_safety_status"}
    assert verdict([check], no_safety_review).validation_status == "pending_review"   # STT chỉ gắn cờ
    review = {**FULL_REVIEW, "content_safety_status": "flagged",
              "content_safety_categories": "profanity_or_abusive_language"}
    d = verdict([check], review)
    assert d.validation_status == "rejected" and d.reasons == ["profanity_or_abusive_language"]


def test_profanity_in_ocr_on_several_frames_auto_rejected():
    check = flagged("ocr", "profanity_or_abusive_language", level="strong", auto=True)
    d = verdict([check], {k: v for k, v in FULL_REVIEW.items() if k != "content_safety_status"})
    assert d.validation_status == "rejected" and "profanity_or_abusive_language" in d.reasons


def test_abusive_dialogue_rejected_when_reviewer_confirms():
    review = {**FULL_REVIEW, "content_safety_status": "flagged",
              "content_safety_categories": "profanity_or_abusive_language"}
    assert verdict([flagged("audio_transcript", "profanity_or_abusive_language")], review).validation_status == "rejected"


def test_unsupported_language_is_pending():
    unsupported = CheckResult("audio_transcript", "unknown", "whisper@test", languages=["fa"],
                              language_status="unsupported", notes=["chưa có lexicon cho fa"])
    review = {k: v for k, v in FULL_REVIEW.items() if k != "content_safety_status"}
    d = verdict([METADATA_SAFE, unsupported], review)
    assert d.validation_status == "pending_review"
    assert d.safety_fields["language_detection_status"] == "unsupported"


@pytest.mark.parametrize("category", ["violent_or_gory_content", "explicit_or_obscene_content"])
def test_gore_or_explicit_visual_flag_pending_then_rejected(category):
    check = flagged("visual", category, timestamp=95.5, score=0.91)
    review = {k: v for k, v in FULL_REVIEW.items() if k != "content_safety_status"}
    assert verdict([check], review).validation_status == "pending_review"        # mô hình chỉ gắn cờ
    d = verdict([check], {**FULL_REVIEW, "content_safety_status": "flagged", "content_safety_categories": category})
    assert d.validation_status == "rejected" and d.reasons == [category]


@pytest.mark.parametrize("category", ["violence_incitement", "explicit_or_obscene_content"])
def test_unambiguous_metadata_term_auto_rejected(category):
    check = flagged("metadata", category, level="strong", auto=True, field="title")
    d = verdict([check], {k: v for k, v in FULL_REVIEW.items() if k != "content_safety_status"})
    assert d.validation_status == "rejected" and d.reasons == [category]


@pytest.mark.parametrize("category,note", [
    ("explicit_or_obscene_content", "vũ đạo quyến rũ thông thường"),
    ("violent_or_gory_content", "cảnh hành động điện ảnh"),
    ("violent_or_gory_content", "MV phản chiến"),
])
def test_context_cases_not_auto_rejected(category, note):
    check = flagged("visual", category, score=0.4, note=note)
    review = {k: v for k, v in FULL_REVIEW.items() if k != "content_safety_status"}
    d = verdict([check], review)
    assert d.validation_status == "pending_review" and not d.reasons           # không tự loại
    assert verdict([check]).validation_status == "accepted"                     # người duyệt xem: an toàn


def test_anti_war_metadata_ambiguous_term_not_auto_rejected(moderator):
    r = moderator.check("metadata", {"title": f"{EN_TITLE} glimpf"})
    review = {k: v for k, v in FULL_REVIEW.items() if k != "content_safety_status"}
    assert verdict([r], review).validation_status == "pending_review"


@pytest.mark.parametrize("scope,field", [("audio_transcript", "audio_moderation_status"),
                                         ("visual", "visual_moderation_status")])
def test_tool_failure_is_pending(scope, field):
    failed = CheckResult(scope, "unknown", f"{scope}@test", notes=[f"{scope}: không phân tích được"])
    review = {k: v for k, v in FULL_REVIEW.items() if k != "content_safety_status"}
    d = verdict([METADATA_SAFE, failed], review)
    assert d.validation_status == "pending_review" and d.safety_fields[field] == "unknown"


def test_conflicting_tools_pending():
    a = flagged("visual", "violent_or_gory_content", score=0.9)
    b = CheckResult("visual", "safe", "other@test", covered_categories=["violent_or_gory_content"])
    d = verdict([a, b])
    assert d.validation_status == "pending_review"
    assert "content_safety_conflict" in d.safety_fields["content_safety_reason"]


def test_reviewer_safe_against_auto_reject_is_conflict():
    auto = flagged("metadata", "profanity_or_abusive_language", level="strong", auto=True)
    d = verdict([auto])                                                         # người duyệt ghi safe
    assert d.validation_status == "pending_review" and not d.reasons
    assert d.safety_fields["content_safety_status"] == "unknown"


def test_multiple_violations_record_all_reasons():
    checks = [flagged("metadata", "violence_incitement", level="strong", auto=True),
              CheckResult("age_restriction", "flagged", "src", flags=[Flag("age_restricted", "age_restricted",
                                                                           "strong", True)])]
    review = {**FULL_REVIEW, "content_safety_status": "flagged",
              "content_safety_categories": "explicit_or_obscene_content"}
    d = verdict(checks, review)
    assert d.validation_status == "rejected"
    assert set(d.reasons) == {"explicit_or_obscene_content", "violence_incitement", "age_restricted"}


def test_score_semantics_recorded():
    check = flagged("visual", "explicit_or_obscene_content", score=0.37)
    check.score_semantics = "nudenet@3.x: điểm detector của lớp EXPOSED_*, ngưỡng 0.5 – KHÔNG phải xác suất vi phạm"
    fields = summarize([check], SAFETY, {}).fields
    assert fields["content_safety_score"] == "0.3700" and "KHÔNG phải xác suất" in fields["moderation_method"]


# ---------------------------------------------------------------- match=exact (giữ dấu) và lexicon nháp
def test_exact_match_avoids_vietnamese_collisions(tmp_path):
    d = tmp_path / "lex"
    d.mkdir()
    (d / "vi.csv").write_text("term,category,level,match\nquạch,profanity_or_abusive_language,ambiguous,exact\n"
                              "quách bừa,profanity_or_abusive_language,ambiguous,normalized\n", encoding="utf-8")
    lex = Lexicon.load(d)
    cfg = SAFETY["text_variants"]
    assert [h.entry.term for h in lex.match("đồ QUẠCH", cfg)] == ["quạch"]
    assert lex.match("Quách tĩnh và quặch", cfg) == []           # bỏ dấu đều là 'quach' – exact không khớp
    assert [h.entry.term for h in lex.match("quach bua", cfg)] == ["quách bừa"]   # normalized khớp không dấu


def test_invalid_match_mode_rejected(tmp_path):
    d = tmp_path / "lex"
    d.mkdir()
    (d / "en.csv").write_text("term,category,level,match\nx,profanity_or_abusive_language,strong,fuzzy\n",
                              encoding="utf-8")
    with pytest.raises(LexiconError):
        Lexicon.load(d)


def test_draft_lexicons_are_valid_but_not_loaded():
    from config.settings import get_settings
    lexicons = get_settings().lexicon_dir
    drafts = Lexicon.load(lexicons / "drafts")
    assert set(drafts.languages) == {"en", "vi"} and drafts.entries
    assert sum(e.level == "strong" for e in drafts.entries) < len(drafts.entries) / 2   # phần lớn ambiguous
    vi_short = [e for e in drafts.entries if e.language == "vi" and len(e.term.split()) == 1 and e.match_mode != "exact"]
    # Từ đơn tiếng Việt có dấu thanh / dấu mũ phải dùng exact (bỏ dấu sẽ trùng từ thường); viết tắt chỉ có 'đ'
    # (đm, đcm…) được phép normalized để bắt cả cách viết không dấu – chúng đều là ambiguous.
    assert all(set(e.term) <= set("abcdefghijklmnopqrstuvwxyzđ") and e.level == "ambiguous" for e in vi_short)
    assert "drafts" not in [e.language for e in Lexicon.load(lexicons).entries]
    assert not set(Lexicon.load(lexicons).languages) & {"drafts"}
