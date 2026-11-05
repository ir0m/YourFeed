"""A genre's name and terms in each chosen language, translated once by the model."""

from __future__ import annotations

import json
from typing import Any

from yourfeed.llm import LLMClient, LLMResult
from yourfeed.search.language import TARGET_NAMES, translation_target

from .validate import parse_json

TERM_MAX = 80

_TERMS = {"type": "array", "items": {"type": "string"}}
SCHEMA = {
    "type": "object",
    "properties": {"translations": {"type": "array", "items": {
        "type": "object",
        "properties": {"language": {"type": "string"}, "name": {"type": "string"},
                       "focus_terms": _TERMS, "exclude_terms": _TERMS},
        "required": ["language", "name", "focus_terms", "exclude_terms"],
        "additionalProperties": False}}},
    "required": ["translations"],
    "additionalProperties": False,
}


def targets(genre: dict[str, Any]) -> list[str]:
    """Languages to translate into; none when the genre searches only in Japanese or in any language."""
    found = [translation_target(locale) for locale in genre.get("locales") or ["all"]]
    if None in found:
        return []
    languages = list(dict.fromkeys(language for language in found if language))
    return [] if languages == ["ja"] else languages


def missing(genre: dict[str, Any]) -> list[str]:
    have = genre.get("translations") or {}
    return [language for language in targets(genre) if language not in have]


def _originals(genre: dict[str, Any]) -> dict[str, Any]:
    return {"name": genre["name"], "focus_terms": list(genre.get("focus_terms") or []),
            "exclude_terms": list(genre.get("exclude_terms") or [])}


def prompts(genre: dict[str, Any], languages: list[str]) -> tuple[str, str]:
    system = "\n".join([
        "あなたは、ニュースフィードの記事を探すための検索語を訳す翻訳者です。",
        "ジャンルの名前と語を、指定された言語ごとに、その言語の記事を検索するのに自然な語に訳します。",
        "",
        "規則:",
        "- 固有名詞は、その言語で一般的な表記にする。",
        "- すでにその言語で書かれた語は、そのまま返す。",
        f"- 1つは{TERM_MAX}字以内。",
        "- focus_terms と exclude_terms は、入力と同じ順番、同じ数にする。",
        "- JSONの translations に、言語ごとに language（指定されたコードのまま）、name、focus_terms、"
        "exclude_terms を返す。",
    ])
    user = json.dumps({"languages": {language: TARGET_NAMES[language] for language in languages},
                       **_originals(genre)}, ensure_ascii=False)
    return system, user


def _term(value: object) -> str | None:
    term = " ".join(value.split()) if isinstance(value, str) else ""
    return term if 0 < len(term) <= TERM_MAX else None


def parse(text: str, genre: dict[str, Any], languages: list[str]) -> dict[str, dict[str, Any]]:
    """Each language's translation; a language with a broken answer keeps the original words."""
    data = parse_json(text)
    entries = data.get("translations") if isinstance(data, dict) else None
    by_language = {entry.get("language"): entry for entry in entries if isinstance(entry, dict)} \
        if isinstance(entries, list) else {}
    originals = _originals(genre)
    result = {}
    for language in languages:
        entry = by_language.get(language) or {}
        name = _term(entry.get("name"))
        lists = {key: entry.get(key) for key in ("focus_terms", "exclude_terms")}
        terms = {key: [_term(value) for value in values] if isinstance(values, list) else None
                 for key, values in lists.items()}
        if name and all(terms[key] is not None and len(terms[key]) == len(originals[key])
                        and None not in terms[key] for key in terms):
            result[language] = {"name": name, **terms}
        else:
            result[language] = originals
    return result


def translate_terms(llm: LLMClient, genre: dict[str, Any],
                    languages: list[str]) -> tuple[dict[str, dict[str, Any]], LLMResult]:
    system, user = prompts(genre, languages)
    result = llm.complete(system, [{"role": "user", "content": user}], SCHEMA)
    return ({} if result.refused else parse(result.text, genre, languages)), result
