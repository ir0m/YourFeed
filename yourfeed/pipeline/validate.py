"""Mechanical checks on a model's summary. Failed checks become fix requests."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass

from .prompt import STANCE_SHARES, TITLE_MAX, Style

KEYS = {"skip", "title", "facts", "interpretation"}
NUMBER = re.compile(r"\d+(?:\.\d+)?")
MARKUP = re.compile(r"https?://|www\.|[<>`\r\n]")
SHARE_TOLERANCE = 0.05


@dataclass(frozen=True)
class Summary:
    title: str
    facts: str
    interpretation: str


@dataclass(frozen=True)
class Checked:
    summary: Summary | None
    skip: bool
    problems: tuple[str, ...]


def parse_json(text: str) -> object:
    """JSON from a reply that may be wrapped in a code fence or extra words."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None


def _numbers_form(value: str) -> str:
    return re.sub(r"(?<=\d)[,，](?=\d)", "", unicodedata.normalize("NFKC", value))


def unsupported_numbers(written: str, source: str) -> list[str]:
    haystack = _numbers_form(source)
    return list(dict.fromkeys(number for number in NUMBER.findall(_numbers_form(written))
                              if number not in haystack))


def check(output: object, source_text: str, style: Style) -> Checked:
    if not isinstance(output, dict) or set(output) != KEYS or not isinstance(output.get("skip"), bool) \
            or not all(isinstance(output[key], str) for key in KEYS - {"skip"}):
        return Checked(None, False, ("JSONのキーは skip, title, facts, interpretation の4つにしてください。",))
    if output["skip"]:
        return Checked(None, True, ())
    title, facts, interpretation = (output[key].strip() for key in ("title", "facts", "interpretation"))
    problems: list[str] = []
    if not 1 <= len(title) <= TITLE_MAX:
        problems.append(f"title を1〜{TITLE_MAX}字にしてください（今は{len(title)}字）。")
    if not facts:
        problems.append("facts を書いてください。")
    if any(MARKUP.search(value) for value in (title, facts, interpretation)):
        problems.append("URL、HTML、改行を使わないでください。")
    total = len(facts) + len(interpretation)
    if not style.min_chars <= total <= style.max_chars:
        problems.append(f"facts と interpretation の合計を{style.min_chars}〜{style.max_chars}字に"
                        f"してください（今は{total}字）。")
    if style.stance == 0 and interpretation:
        problems.append("interpretation は空文字にしてください。")
    elif style.stance > 0:
        if not interpretation:
            problems.append("interpretation を書いてください。")
        elif total:
            low, high = STANCE_SHARES[style.stance]
            if not low - SHARE_TOLERANCE <= len(interpretation) / total <= high + SHARE_TOLERANCE:
                range_low, range_high = style.interpretation_range()
                problems.append(f"interpretation を{range_low}〜{range_high}字にしてください"
                                f"（今は{len(interpretation)}字）。")
    missing = unsupported_numbers(f"{title} {facts} {interpretation}", source_text)
    if missing:
        problems.append(f"本文に無い数字を使わないでください: {'、'.join(missing)}")
    if problems:
        return Checked(None, False, tuple(problems))
    return Checked(Summary(title, facts, interpretation), False, ())
