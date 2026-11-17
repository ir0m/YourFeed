"""Posts by a fictional person: the model writes several at once from the person's profile."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from yourfeed.llm import LLMClient

from .validate import parse_json

BATCH_MAX = 10  # posts asked for in one call
RECENT = 20  # earlier posts shown to the model so it does not repeat them
ATTEMPTS = 2
MARKUP = re.compile(r"https?://|www\.|[<>`]")
WEEKDAYS = "月火水木金土日"

SCHEMA = {
    "type": "object",
    "properties": {"posts": {"type": "array", "items": {"type": "string"}}},
    "required": ["posts"],
    "additionalProperties": False,
}


@dataclass
class Written:
    status: str  # ok, refused, rejected
    posts: list[str] = field(default_factory=list)
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


def system_prompt(persona: dict[str, Any], count: int, now: datetime) -> str:
    lines = [
        "あなたは、SNSに投稿する架空の人物になりきって投稿を書きます。",
        f"JSONの posts に、互いに別の話題の投稿を{count}件、文字列の配列で返します。",
        "",
        f"人物の名前: {persona['name']}",
    ]
    for label, key in (("プロフィール", "profile"), ("投稿する内容", "topics"), ("口調・書き方", "tone")):
        value = (persona.get(key) or "").strip()
        if value:
            lines.append(f"{label}:\n{value}")
    lines += [
        "",
        "規則:",
        f"- 1件は{persona['min_chars']}〜{persona['max_chars']}字。",
        "- プロフィールで言語の指定がなければ日本語で書く。",
        f"- 今は{now:%Y年%-m月%-d日}（{WEEKDAYS[now.weekday()]}）{now:%-H}時ごろ。季節や時間帯に合った内容にしてよい。",
        "- この人物は架空。実在の人物や団体について、事実のように見える具体的な出来事、発言、数字を作らない。",
        "- ニュースを報じる書き方をしない。人物の日常、考え、感想として書く。",
        "- URL、HTML、メンションを使わない。",
        "- 前の投稿と同じ話題や言い回しを繰り返さない。",
    ]
    return "\n".join(lines)


def user_prompt(recent: list[str]) -> str:
    if not recent:
        return "投稿を書いてください。"
    return "前の投稿（これとは別の話題にする）:\n" + "\n".join(f"- {post}" for post in recent)


def _key(post: str) -> str:
    """What two posts are compared by: the same post, spaced differently, counts as a repeat."""
    return "".join(post.split())


def check(output: object, persona: dict[str, Any], count: int, recent: list[str]) -> tuple[list[str], list[str]]:
    """The usable posts, at most `count`, and the problems with the others."""
    values = output.get("posts") if isinstance(output, dict) else None
    if not isinstance(values, list):
        return [], ["JSONの posts に文字列の配列を返してください。"]
    seen = {_key(post) for post in recent}
    posts: list[str] = []
    problems: list[str] = []
    for value in values:
        post = value.strip() if isinstance(value, str) else ""
        key = _key(post)
        if not post or key in seen:
            continue
        if MARKUP.search(post):
            problems.append("URL、HTMLを使わないでください。")
        elif not persona["min_chars"] <= len(post) <= persona["max_chars"]:
            problems.append(f"1件を{persona['min_chars']}〜{persona['max_chars']}字にしてください"
                            f"（{len(post)}字の投稿がありました）。")
        else:
            seen.add(key)
            posts.append(post)
    return posts[:count], list(dict.fromkeys(problems))


def write_posts(llm: LLMClient, persona: dict[str, Any], count: int, recent: list[str], now: datetime) -> Written:
    """Up to `count` new posts; asks once more with the problems when none can be used."""
    messages = [{"role": "user", "content": user_prompt(recent)}]
    system = system_prompt(persona, count, now)
    written = Written("rejected")
    for _ in range(ATTEMPTS):
        result = llm.complete(system, messages, SCHEMA)
        written.calls += 1
        written.input_tokens += result.input_tokens
        written.output_tokens += result.output_tokens
        if result.refused:
            written.status = "refused"
            return written
        posts, problems = check(parse_json(result.text), persona, count, recent)
        if posts:
            written.status, written.posts = "ok", posts
            return written
        messages += [{"role": "assistant", "content": result.text},
                     {"role": "user", "content": "次の点を直して、JSONをもう一度返してください。\n"
                      + "\n".join(f"- {problem}" for problem in problems or ["投稿を1件以上返してください。"])}]
    return written
