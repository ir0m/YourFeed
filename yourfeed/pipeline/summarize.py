"""One summary: a model call, the checks, and one retry with the problems found."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from yourfeed.llm import LLMClient

from .prompt import SCHEMA, Style, fix_prompt, system_prompt, user_prompt
from .validate import Summary, check, parse_json

ATTEMPTS = 3


@dataclass
class Outcome:
    status: str  # ok, skipped, refused, rejected
    summary: Summary | None = None
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    problems: tuple[str, ...] = field(default_factory=tuple)


def source_text(title: str, published_at: datetime | None, body: str) -> str:
    """What numbers in a summary may come from."""
    date = ""
    if published_at:
        date = f"{published_at:%Y-%m-%d} {published_at.year}年{published_at.month}月{published_at.day}日"
    return f"{title}\n{date}\n{body}"


def summarize(llm: LLMClient, style: Style, *, source_name: str, title: str,
              published_at: datetime | None, body: str) -> Outcome:
    system = system_prompt(style)
    messages = [{"role": "user", "content": user_prompt(source_name=source_name, title=title,
                                                       published_at=published_at, body=body)}]
    allowed = source_text(title, published_at, body)
    outcome = Outcome("rejected")
    for _ in range(ATTEMPTS):
        result = llm.complete(system, messages, SCHEMA)
        outcome.calls += 1
        outcome.input_tokens += result.input_tokens
        outcome.output_tokens += result.output_tokens
        if result.refused:
            outcome.status = "refused"
            return outcome
        checked = check(parse_json(result.text), allowed, style)
        if checked.skip:
            outcome.status = "skipped"
            return outcome
        if checked.summary:
            outcome.status, outcome.summary, outcome.problems = "ok", checked.summary, ()
            return outcome
        outcome.problems = checked.problems
        messages += [{"role": "assistant", "content": result.text},
                     {"role": "user", "content": fix_prompt(list(checked.problems))}]
    return outcome
