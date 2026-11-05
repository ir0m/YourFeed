import json

from yourfeed.llm import LLMResult
from yourfeed.pipeline.translate import missing, parse, prompts, targets, translate_terms

GENRE = {"name": "宇宙", "focus_terms": ["探査機", "ロケット"], "exclude_terms": ["広告"],
         "locales": ["ja-JP", "en-US", "en-GB", "zh-TW"], "translations": {}}


def test_targets_skip_japanese_only_and_any_language():
    assert targets(GENRE) == ["ja", "en", "zh-TW"]
    assert targets({**GENRE, "locales": ["ja-JP"]}) == [] and targets({**GENRE, "locales": ["all"]}) == []
    assert missing({**GENRE, "translations": {"ja": {}, "en": {}}}) == ["zh-TW"]


def test_prompt_names_each_language():
    system, user = prompts(GENRE, ["en", "zh-TW"])
    assert "同じ順番、同じ数" in system
    assert json.loads(user) == {"languages": {"en": "英語", "zh-TW": "中国語（繁体字）"}, "name": "宇宙",
                                "focus_terms": ["探査機", "ロケット"], "exclude_terms": ["広告"]}


def test_broken_answers_keep_the_original_words():
    reply = json.dumps({"translations": [
        {"language": "en", "name": " space ", "focus_terms": ["space probe", "rocket"], "exclude_terms": ["ads"]},
        {"language": "zh-TW", "name": "太空", "focus_terms": ["探測器"], "exclude_terms": ["廣告"]}]})
    result = parse(reply, GENRE, ["en", "zh-TW"])
    assert result["en"] == {"name": "space", "focus_terms": ["space probe", "rocket"], "exclude_terms": ["ads"]}
    assert result["zh-TW"] == {"name": "宇宙", "focus_terms": ["探査機", "ロケット"], "exclude_terms": ["広告"]}
    assert parse("no json", GENRE, ["en"])["en"]["name"] == "宇宙"


def test_refused_translations_are_left_for_later():
    class Refuses:
        def complete(self, system, messages, schema):
            return LLMResult("", refused=True)
    assert translate_terms(Refuses(), GENRE, ["en"])[0] == {}
