"""Search the web for new candidates: CANDIDATES new URLs per update, split by genre weight."""

from __future__ import annotations

from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.engine import Engine

from yourfeed.search.plan import MAX_PAGES, GenreSearch, Stream
from yourfeed.search.searxng import Result, SearchError

from .select import fill_by_weight, matches

CANDIDATES = 50
MAX_SEARCHES = 60
MAX_SEARCH_ERRORS = 3


class Searcher(Protocol):
    """What the collector needs from a search backend."""

    def search(self, query: str, *, page: int = 1, categories: str = "general",
               time_range: str | None = None, language: str = "all") -> list[Result]: ...


class Collector:
    def __init__(self, engine: Engine, searcher: Searcher, run_id: int) -> None:
        self.engine, self.searcher, self.run_id = engine, searcher, run_id
        self.searches = 0
        self.errors = 0
        self.error: str | None = None
        self.stopped = False
        self.rank = 0
        self.found: dict[Any, int] = {}
        self._genres: dict[Any, dict] = {}
        self._plans: dict[Any, GenreSearch] = {}

    def collect(self, genres: list[dict], total: int = CANDIDATES) -> dict[Any, int]:
        """Search until `total` new URLs are stored; returns each genre's target."""
        for index, genre in enumerate(genres):
            self._genres[genre["id"]] = genre
            self._plans[genre["id"]] = GenreSearch(genre, offset=self.run_id + index)
            self.found[genre["id"]] = 0
        stages = range(max((len(plan.windows) for plan in self._plans.values()), default=0))
        return fill_by_weight({genre["id"]: genre["weight"] for genre in genres}, total, stages,
                              lambda genre_id: self.found[genre_id], self._fill)

    def _fill(self, genre_id: Any, target: int, stage: int) -> bool:
        """Search the genre's `stage`-th window until `target`; False when it has nothing more."""
        plan = self._plans[genre_id]
        if stage >= len(plan.windows):
            return False
        window = plan.windows[stage]
        while self.found[genre_id] < target:
            if self.stopped or self.searches >= MAX_SEARCHES:
                return False
            picked = plan.next_stream(window, target - self.found[genre_id])
            if picked is None:
                return False
            stream, limit = picked
            added = self._read(genre_id, stream, window, limit)
            plan.taken(stream, added)
            self.found[genre_id] += added
        return True

    def _read(self, genre_id: Any, stream: Stream, window: tuple[str, str | None], limit: int) -> int:
        self.searches += 1
        try:
            categories, time_range = window
            results = self.searcher.search(stream.query, page=stream.page, categories=categories,
                                           time_range=time_range, language=stream.locale)
        except SearchError as exc:
            self.error = exc.code
            self.errors += 1
            stream.done = True
            if exc.fatal or self.errors >= MAX_SEARCH_ERRORS:
                self.stopped = True
            return 0
        self.errors = 0
        stream.page += 1
        fresh = [result for result in results if result.url not in stream.seen]
        stream.seen.update(result.url for result in results)
        if not fresh or stream.page > MAX_PAGES:
            stream.done = True  # no more pages, or the engine repeats the same page
        genre = self._genres[genre_id]
        exclude = list(genre.get("exclude_terms") or [])
        added = 0
        with self.engine.begin() as connection:
            for result in fresh:
                if added >= limit:
                    break
                if stream.site is not None and not stream.site.contains(result.url):
                    continue
                if exclude and matches(f"{result.title} {result.snippet}", exclude):
                    continue
                self.rank += 1
                added += connection.execute(text("""
                    INSERT INTO items (genre_id, url, title, snippet, published_at, run_id, rank)
                    VALUES (:genre_id, :url, :title, :snippet, :published_at, :run_id, :rank)
                    ON CONFLICT (url) DO NOTHING
                """), {"genre_id": genre_id, "url": result.url, "title": result.title,
                       "snippet": result.snippet or None, "published_at": result.published_at,
                       "run_id": self.run_id, "rank": self.rank}).rowcount
        return added
