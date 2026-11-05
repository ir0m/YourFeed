"""Which searches to run for a genre: listed sites first, then the open web."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlsplit

# (SearXNG categories, time range) in the order tried. None means no time limit.
Window = tuple[str, str | None]
WINDOWS: tuple[Window, ...] = (
    ("news", "day"), ("news", "week"), ("news", "month"),
    ("general", "week"), ("general,science", "month"), ("general,science", "year"), ("general,science", None))
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


class GenreSearch:
    """Search state for one genre during one update."""

    def __init__(self, genre: dict, offset: int = 0) -> None:
        self.sites = [Site.parse(url) for url in genre.get("sites") or []]
        self.focus, self.discovery = queries(genre["name"], list(genre.get("focus_terms") or []),
                                             list(genre.get("exclude_terms") or []))
        self.share = (genre.get("discovery_percent", 20) / 100) if self.focus else 1.0
        self.offset = offset
        self.open_taken = 0
        self.discovery_taken = 0
        self._streams: dict[Window, tuple[list[Stream], list[Stream], list[Stream]]] = {}

    def streams(self, window: Window) -> tuple[list[Stream], list[Stream], list[Stream]]:
        if window not in self._streams:
            bases = self.focus or self.discovery
            site = [Stream(f"{base} {s.operator}", site=s) for s in self.sites for base in bases]
            focus = [Stream(query) for query in self.focus]
            discovery = [Stream(query, discovery=True) for query in self.discovery]
            self._streams[window] = (rotate(site, self.offset), rotate(focus, self.offset), discovery)
        return self._streams[window]

    def next_stream(self, window: Window, need: int) -> tuple[Stream, int] | None:
        """The stream to read next and how many new results to take from it."""
        site, focus, discovery = (
            [stream for stream in group if not stream.done] for group in self.streams(window))
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
        if stream.site is None:
            self.open_taken += count
            if stream.discovery:
                self.discovery_taken += count
