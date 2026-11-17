from datetime import datetime

from yourfeed.pipeline.persona import check, system_prompt

PERSONA = {"name": "山田", "profile": "東京の会社員", "topics": "", "tone": "", "min_chars": 5, "max_chars": 20}


def test_posts_out_of_range_repeated_or_with_links_are_dropped():
    posts, problems = check({"posts": ["短い", "今日はいい天気だった。", "今日は  いい天気だった。", "www.example.com を見た",
                                       "昨日の続きを書いた。", "あ" * 21, 3]}, PERSONA, 5, ["昨日の続きを書いた。"])
    assert posts == ["今日はいい天気だった。"]
    assert problems == ["1件を5〜20字にしてください（2字の投稿がありました）。", "URL、HTMLを使わないでください。",
                        "1件を5〜20字にしてください（21字の投稿がありました）。"]
    assert check({"posts": ["今日はいい天気だった。", "夕方は雨が降ってきた。"]}, PERSONA, 1, [])[0] == ["今日はいい天気だった。"]
    assert check({"text": "x"}, PERSONA, 1, [])[0] == []


def test_the_prompt_leaves_out_empty_fields_and_gives_the_time():
    prompt = system_prompt(PERSONA, 3, datetime(2026, 11, 17, 21, 5))
    assert "プロフィール:\n東京の会社員" in prompt and "投稿する内容" not in prompt
    assert "投稿を3件" in prompt and "2026年11月17日（火）21時ごろ" in prompt and "5〜20字" in prompt
