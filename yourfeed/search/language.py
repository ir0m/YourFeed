"""Search locales, and cheap checks of the language a page is written in."""

from __future__ import annotations

import re

# SearXNG locales a genre can choose. "all" searches every language and region.
LOCALES = ("ja-JP", "en-US", "en-GB", "en", "zh-CN", "zh-TW", "ko-KR", "fr-FR", "de-DE", "all")
NAMES = {"ja": "日本語", "en": "英語", "zh": "中国語", "ko": "韓国語", "fr": "フランス語", "de": "ドイツ語"}
# Characters needed for about as much content as in Japanese.
LENGTH_SCALE = {"en": 2.5, "fr": 2.5, "de": 2.5, "ko": 1.3}

KANA = re.compile(r"[぀-ヿ]")
HANGUL = re.compile(r"[가-힯]")
HAN = re.compile(r"[一-鿿]")
LATIN = re.compile(r"[A-Za-zÀ-ɏ]")


def language_of(locale: str) -> str | None:
    return None if locale == "all" else locale.split("-")[0]


def languages_of(locales: list[str]) -> list[str] | None:
    """The languages to accept, or None for any."""
    languages = [language_of(locale) for locale in locales]
    return None if not languages or None in languages else list(dict.fromkeys(languages))


def _letters(text: str) -> tuple[int, int, int, int]:
    sample = text[:3000]
    return tuple(len(pattern.findall(sample)) for pattern in (KANA, HANGUL, HAN, LATIN))  # type: ignore[return-value]


def in_language(language: str | None, text: str, declared: str | None = None) -> bool:
    """Decided by the letters used; for languages written in Latin letters, also by the page's
    own `lang` when it has one."""
    if language is None:
        return True
    kana, hangul, han, latin = _letters(text)
    letters = kana + hangul + han + latin
    if not letters:
        return True
    if language == "ja":
        return kana >= 0.1 * letters
    if language == "zh":
        return han >= 0.3 * letters and kana < 0.02 * letters
    if language == "ko":
        return hangul >= 0.3 * letters
    if latin < 0.6 * letters:
        return False
    declared_language = (declared or "").split("-")[0].strip().lower()
    return not declared_language or declared_language == language


def page_language(languages: list[str] | None, text: str, declared: str | None = None) -> str | None:
    """Which of `languages` the page is in (None when none fits); with `languages` None, a guess."""
    if languages is not None:
        return next((language for language in languages if in_language(language, text, declared)), None)
    kana, hangul, han, latin = _letters(text)
    if kana >= 0.1 * max(1, kana + hangul + han + latin):
        return "ja"
    if hangul > han and hangul > latin:
        return "ko"
    if han > latin:
        return "zh"
    return (declared or "en").split("-")[0].strip().lower() or "en"
