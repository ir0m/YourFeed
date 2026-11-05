from yourfeed.search.language import in_language, language_of, languages_of, page_language

JA = "政府は来年度の予算案を閣議決定した。総額は過去最大となる見通しだ。"
ZH = "国家航天局宣布了新的月球探测计划，预计费用为三百亿元。"
KO = "정부는 내년도 예산안을 의결했다. 총액은 사상 최대가 될 전망이다."
EN = "The agency announced a new plan to land on the Moon in 2027, at a cost of 30 billion yen."


def test_locales_name_a_language_or_none():
    assert (language_of("ja-JP"), language_of("en"), language_of("all")) == ("ja", "en", None)


def test_pages_are_checked_by_their_letters():
    assert in_language("ja", JA) and not in_language("ja", ZH) and not in_language("ja", EN)
    assert in_language("zh", ZH) and not in_language("zh", JA)
    assert in_language("ko", KO) and not in_language("ko", JA)
    assert in_language("en", EN) and not in_language("en", JA)
    assert in_language(None, ZH)


def test_latin_languages_follow_the_declared_language():
    assert in_language("en", EN, "en-US") and not in_language("en", EN, "fr")
    assert in_language("ja", "AI エージェントの評価方法について OpenAI と Google が発表した。")


def test_page_language_picks_a_chosen_language_or_guesses():
    assert languages_of(["ja-JP", "en-US", "en"]) == ["ja", "en"] and languages_of(["all"]) is None
    assert page_language(["ja", "en"], EN) == "en" and page_language(["ja"], ZH) is None
    assert [page_language(None, text) for text in (JA, ZH, KO, EN)] == ["ja", "zh", "ko", "en"]
    assert page_language(None, EN, "fr-FR") == "fr"
