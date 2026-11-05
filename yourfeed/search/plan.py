"""Which searches to run for a genre: listed sites first, then the open web."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .language import in_language, language_of, translation_target

# (SearXNG categories, time range) in the order tried. None means no time limit.
Window = tuple[str, str | None]
LATEST_WINDOWS: tuple[Window, ...] = (
    ("news", "day"), ("news", "week"), ("news", "month"),
    ("general", "week"), ("general,science", "month"), ("general,science", "year"), ("general,science", None))
ANY_AGE_WINDOWS: tuple[Window, ...] = (("general", None), ("science", None))
MAX_PAGES = 3


@dataclass(frozen=True)
class Site:
    host: str
    path: str = ""

    @classmethod
    def parse(cls, url: str) -> Site:
        parts = urlsplit(url)
        path = parts.path.rstrip("/")
        return cls((parts.hostname or "").lower(), path)

    @property
    def operator(self) -> str:
        return f"site:{self.host}{self.path}"

    def contains(self, url: str) -> bool:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if host != self.host and not host.endswith("." + self.host):
            return False
        return not self.path or parts.path == self.path or parts.path.startswith(self.path + "/")


@dataclass
class Stream:
    """One query, read page by page."""
    query: str
    site: Site | None = None
    discovery: bool = False
    locale: str = "all"
    page: int = 1
    done: bool = False
    seen: set[str] = field(default_factory=set)


def _exclusions(terms: list[str]) -> str:
    return " ".join(f'-"{term}"' if " " in term else f"-{term}" for term in terms if term.strip())


def queries(name: str, focus_terms: list[str], exclude_terms: list[str]) -> tuple[list[str], list[str]]:
    """(focus queries, discovery queries). Without focus terms, the genre name is the only query."""
    suffix = _exclusions(exclude_terms)
    join = lambda *parts: " ".join(part for part in parts if part)  # noqa: E731
    focus = [join(name, term, suffix) for term in focus_terms if term.strip()]
    return focus, [join(name, suffix)]


def rotate(values: list, offset: int) -> list:
    return values[offset % len(values):] + values[:offset % len(values)] if values else values


def terms_for(genre: dict, locale: str) -> tuple[str, list[str], list[str]]:
    """The genre's name, focus terms and exclude terms for one locale, translated when available."""
    source = (genre.get("translations") or {}).get(translation_target(locale) or "") or genre
    return source["name"], list(source.get("focus_terms") or []), list(source.get("exclude_terms") or [])


class GenreSearch:
    """Search state for one genre during one update. Each chosen locale gets an even share of the
    results and searches with the terms in its language; genres of any age search this update's topics."""

    def __init__(self, genre: dict, offset: int = 0) -> None:
        latest = genre.get("latest", True)
        self.windows = LATEST_WINDOWS if latest else ANY_AGE_WINDOWS
        self.locales = list(genre.get("locales") or ["all"])
        self.sites = [Site.parse(url) for url in genre.get("sites") or []]
        topics = [] if latest else [topic for topic in genre.get("search_topics") or [] if topic.strip()]
        self.queries: dict[str, tuple[list[str], list[str]]] = {}
        self.excludes: dict[str, list[str]] = {}
        for locale in self.locales:
            name, focus_terms, exclude_terms = terms_for(genre, locale)
            # Results are filtered by both the written and the translated words.
            self.excludes[locale] = list(dict.fromkeys([*(genre.get("exclude_terms") or []), *exclude_terms]))
            if latest:
                self.queries[locale] = queries(name, focus_terms, exclude_terms)
            else:
                # A topic is searched in the locales of its own language, or in all of them.
                own = [topic for topic in topics if locale in self._topic_locales(topic)]
                self.queries[locale] = ([], [queries(topic, [], exclude_terms)[1][0] for topic in own]
                                        or queries(name, [], exclude_terms)[1])
        has_focus = any(focus for focus, _ in self.queries.values())
        self.share = (genre.get("discovery_percent", 20) / 100) if has_focus else 1.0
        self.offset = offset
        self.open_taken = 0
        self.discovery_taken = 0
        self.locale_taken = {locale: 0 for locale in self.locales}
        self._streams: dict[tuple[Window, str], tuple[list[Stream], list[Stream], list[Stream]]] = {}

    def _topic_locales(self, topic: str) -> list[str]:
        return [locale for locale in self.locales if in_language(language_of(locale), topic)] or self.locales

    def streams(self, window: Window, locale: str | None = None) -> tuple[list[Stream], list[Stream], list[Stream]]:
        locale = locale or self.locales[0]
        if (window, locale) not in self._streams:
            focus, discovery = self.queries[locale]
            bases = focus or discovery
            site = [Stream(f"{base} {s.operator}", site=s, locale=locale) for s in self.sites for base in bases]
            self._streams[(window, locale)] = (
                rotate(site, self.offset), rotate([Stream(query, locale=locale) for query in focus], self.offset),
                [Stream(query, discovery=True, locale=locale) for query in discovery])
        return self._streams[(window, locale)]

    def next_stream(self, window: Window, need: int) -> tuple[Stream, int] | None:
        """The stream to read next and how many new results to take from it."""
        open_locales = [locale for locale in self.locales
                        if any(not stream.done for group in self.streams(window, locale) for stream in group)]
        if not open_locales:
            return None
        locale = min(open_locales, key=lambda locale: self.locale_taken[locale])
        return self._next_in(window, locale, -(-need // len(open_locales)))

    def _next_in(self, window: Window, locale: str, need: int) -> tuple[Stream, int] | None:
        site, focus, discovery = (
            [stream for stream in group if not stream.done] for group in self.streams(window, locale))
        first = lambda streams: min(streams, key=lambda stream: stream.page)  # noqa: E731
        if site:
            return first(site), need
        if not focus and not discovery:
            return None
        if not focus or not discovery:
            return first(focus or discovery), need
        total = self.open_taken + need
        want_discovery = round(self.share * total)
        discovery_limit = min(need, max(0, want_discovery - self.discovery_taken))
        focus_limit = min(need, max(0, total - want_discovery - (self.open_taken - self.discovery_taken)))
        if discovery_limit and (not focus_limit or self.discovery_taken < self.share * (self.open_taken + 1)):
            return first(discovery), discovery_limit
        return first(focus), focus_limit

    def taken(self, stream: Stream, count: int) -> None:
        self.locale_taken[stream.locale] = self.locale_taken.get(stream.locale, 0) + count
        if stream.site is None:
            self.open_taken += count
            if stream.discovery:
                self.discovery_taken += count
