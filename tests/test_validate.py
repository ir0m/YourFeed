import json

from yourfeed.pipeline.prompt import Style, system_prompt
from yourfeed.pipeline.validate import check, parse_json

SOURCE = "日本銀行は2026年11月4日、政策金利を0.75%に据え置くと発表した。物価は2.1%上昇した。"


def output(facts, interpretation="", title="日銀が政策金利を据え置き", skip=False):
    return {"skip": skip, "title": title, "facts": facts, "interpretation": interpretation}


def test_fact_only_summary_passes_within_length():
    style = Style(20, 80, 0)
    result = check(output("日本銀行は政策金利を0.75%に据え置くと発表した。物価は2.1%上昇した。"), SOURCE, style)
    assert result.summary is not None and result.problems == ()


def test_fact_only_rejects_interpretation_and_reports_length():
    style = Style(60, 80, 0)
    result = check(output("日本銀行は金利を据え置いた。", "影響が大きい。"), SOURCE, style)
    assert result.summary is None
    assert any("空文字" in problem for problem in result.problems)
    assert any("合計を60〜80字" in problem and "今は" in problem for problem in result.problems)


def test_interpretation_share_is_checked_per_stance():
    facts = "日本銀行は政策金利を0.75%に据え置くと発表した。"
    style = Style(30, 120, 2)
    too_little = check(output(facts, "注目される。"), SOURCE, style)
    assert any("interpretation を" in problem for problem in too_little.problems)
    balanced = check(output(facts, "物価の上昇が続くなかで、当面は様子を見る姿勢と読める。"), SOURCE, style)
    assert balanced.summary is not None


def test_numbers_must_come_from_the_source_or_date():
    style = Style(10, 120, 0)
    result = check(output("日本銀行は政策金利を1.0%に引き上げた。"), SOURCE, style)
    assert any("1.0" in problem for problem in result.problems)
    assert check(output("日本銀行は2026年11月4日に政策金利を0.75%に据え置いた。"), SOURCE, style).summary


def test_markup_and_bad_shapes_are_rejected():
    style = Style(10, 120, 0)
    assert check(output("詳しくは https://example.com を参照。"), SOURCE, style).summary is None
    assert check({"title": "x"}, SOURCE, style).problems
    assert check(None, SOURCE, style).problems


def test_skip_is_reported_without_problems():
    result = check(output("", skip=True, title=""), SOURCE, Style())
    assert result.skip and result.summary is None and result.problems == ()


def test_parse_json_accepts_fenced_replies():
    body = {"skip": False, "title": "t", "facts": "f", "interpretation": ""}
    assert parse_json("```json\n" + json.dumps(body) + "\n```") == body
    assert parse_json("not json") is None


def test_prompt_states_ranges_and_focus_terms():
    prompt = system_prompt(Style(100, 160, 1, ("量子", "AI")))
    assert "合計を100〜160字" in prompt
    assert "量子、AI" in prompt
    assert "interpretation: 常に空文字" in system_prompt(Style(100, 160, 0))
    assert "主題が「宇宙」と関係ない" in system_prompt(Style(topic="宇宙"))
    assert "その時だけの話題" in system_prompt(Style(latest=False))
    assert "その時だけの話題" not in system_prompt(Style())
    assert "関係ない" not in system_prompt(Style())
