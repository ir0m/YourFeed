"""One update: search for new candidates, summarize them and post the set count by genre weight."""

from __future__ import annotations

import json
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from yourfeed import config
from yourfeed.db import WORKER_LOCK
from yourfeed.llm import LLMClient, LLMError, make_client
from yourfeed.search.searxng import Searxng
from yourfeed.fetch import SafeHttpClient

from .collect import CANDIDATES, Collector, Searcher
from .pagetext import RETRYABLE, Page, PageFetcher
from .prompt import Style
from .select import by_site, fill_by_weight
from .summarize import summarize
from .topics import PER_UPDATE, make_topics

# Age limits for posts, tried in order until the count is filled. None means any age.
LATEST_WINDOWS = (timedelta(days=7), timedelta(days=30), None)
ANY_AGE_WINDOWS = (None,)
RETRY_AFTER = timedelta(hours=3)
KEEP_ITEMS = timedelta(days=90)
MAX_CONSECUTIVE_ERRORS = 3
CALLS_PER_POST = 8


def load_candidates(connection: Connection, genre_id: Any, now: datetime, window: timedelta | None,
                    dated_first: bool = True) -> list[dict[str, Any]]:
    """Unposted candidates: latest search first, dated before undated if asked, then in search order."""
    order = "i.published_at IS NULL, i.rank" if dated_first else "i.rank"
    return [dict(row) for row in connection.execute(text(f"""
        SELECT i.id, i.title, i.url, i.published_at, (sm.item_id IS NOT NULL) AS has_summary
        FROM items i
        LEFT JOIN summaries sm ON sm.item_id = i.id
        WHERE i.genre_id = :genre_id AND i.posted_at IS NULL
          AND COALESCE(i.published_at, i.found_at) <= :latest
          AND (CAST(:since AS timestamptz) IS NULL OR COALESCE(i.published_at, i.found_at) >= :since)
          AND (sm.item_id IS NOT NULL OR i.attempted_at IS NULL
               OR (i.skip_reason = ANY(:retryable) AND i.attempted_at < :retry_before))
        ORDER BY i.run_id DESC NULLS LAST, {order}
    """), {"genre_id": genre_id, "since": now - window if window else None,
           "latest": now + timedelta(days=1), "retryable": sorted(RETRYABLE),
           "retry_before": now - RETRY_AFTER}).mappings()]


class _Posting:
    def __init__(self, engine: Engine, http: SafeHttpClient, llm: LLMClient | None, now: datetime,
                 run_id: int, call_cap: int, genres: dict[Any, dict[str, Any]], retention_hours: int) -> None:
        self.engine, self.llm, self.now = engine, llm, now
        self.run_id, self.call_cap, self.genres, self.retention_hours = run_id, call_cap, genres, retention_hours
        self.posted = 0
        self.queues: dict[tuple[Any, int], deque[dict[str, Any]]] = {}
        self.fetcher = PageFetcher(http)
        self.calls = self.input_tokens = self.output_tokens = 0
        self.consecutive_errors = 0
        self.error: str | None = None
        self.tried: set[Any] = set()
        self.pages: dict[Any, Page] = {}
        self.accepted: dict[Any, list[Any]] = {genre_id: [] for genre_id in genres}

    def _mark_attempted(self, item_id: Any, reason: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(text("UPDATE items SET attempted_at = now(), skip_reason = :reason WHERE id = :id"),
                               {"id": item_id, "reason": reason})

    def _count(self, calls: int, input_tokens: int, output_tokens: int) -> None:
        self.calls += calls
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        with self.engine.begin() as connection:
            connection.execute(text("""
                UPDATE runs SET llm_calls = :calls, input_tokens = :input, output_tokens = :output WHERE id = :id
            """), {"id": self.run_id, "calls": self.calls, "input": self.input_tokens, "output": self.output_tokens})

    def _summarize(self, genre: dict[str, Any], row: dict[str, Any], since: datetime | None) -> str:
        """'ok', 'later' when the page is older than the window or undated, or 'no'."""
        page = self.pages.get(row["id"])
        if page is None:
            page, reason = self.fetcher.read(row["url"])
            if page is None:
                self._mark_attempted(row["id"], reason or "too_little_text")
                return "no"
            self.pages[row["id"]] = page
        # A page's own date is more exact than the search result's, except a PDF's file date.
        page_date = None if page.is_pdf and row["published_at"] else page.published_at
        published_at = page_date or row["published_at"]
        if page_date and page_date != row["published_at"]:
            with self.engine.begin() as connection:
                connection.execute(text("UPDATE items SET published_at = :at WHERE id = :id"),
                                   {"id": row["id"], "at": page_date})
            if since and page_date < since:
                return "later"
        if since and published_at is None:
            return "later"  # undated pages wait for the widest window
        style = Style(genre["min_chars"], genre["max_chars"], genre["stance"], tuple(genre["focus_terms"]),
                      genre["name"], genre["latest"])
        title = page.title or row["title"]
        site_name = page.site_name or (urlsplit(row["url"]).hostname or "").removeprefix("www.")
        try:
            outcome = summarize(self.llm, style, source_name=site_name, title=title,
                                published_at=published_at, body=page.text)
        except LLMError as exc:
            self.error = exc.code
            self.consecutive_errors += 1
            if exc.fatal or self.consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                self.llm = None
            return "no"
        self.consecutive_errors = 0
        self._count(outcome.calls, outcome.input_tokens, outcome.output_tokens)
        if outcome.status != "ok" or outcome.summary is None:
            self._mark_attempted(row["id"], outcome.status)
            return "no"
        with self.engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO summaries (item_id, title, facts, interpretation, provider, model)
                VALUES (:id, :title, :facts, :interpretation, :provider, :model)
                ON CONFLICT (item_id) DO NOTHING
            """), {"id": row["id"], "title": outcome.summary.title, "facts": outcome.summary.facts,
                   "interpretation": outcome.summary.interpretation,
                   "provider": self.llm.provider, "model": self.llm.model})
            connection.execute(text("""
                UPDATE items SET attempted_at = now(), skip_reason = NULL, title = :title, site_name = :site
                WHERE id = :id
            """), {"id": row["id"], "title": title, "site": site_name})
        return "ok"

    def _queue(self, genre: dict[str, Any], stage: int, window: timedelta | None) -> deque[dict[str, Any]]:
        key = (genre["id"], stage)
        if key not in self.queues:
            with self.engine.connect() as connection:
                rows = {row["id"]: row for row in load_candidates(connection, genre["id"], self.now, window,
                                                                  genre["latest"]) if row["id"] not in self.tried}
            self.queues[key] = deque(rows[item_id] for item_id in by_site([(row["id"], row["url"])
                                                                           for row in rows.values()]))
        return self.queues[key]

    def fill(self, genre_id: Any, target: int, stage: int) -> bool:
        """Post items from the genre's `stage`-th window as they are summarized, until `target`;
        False when nothing is left."""
        genre = self.genres[genre_id]
        windows = LATEST_WINDOWS if genre["latest"] else ANY_AGE_WINDOWS
        if stage >= len(windows):
            return False
        window = windows[stage]
        since = self.now - window if window else None
        queue = self._queue(genre, stage, window)
        accepted = self.accepted[genre_id]
        while len(accepted) < target and queue:
            row = queue.popleft()
            if row["id"] in self.tried:
                continue
            if row["has_summary"]:
                result = "ok"
            elif self.llm is not None and self.calls < self.call_cap:
                result = self._summarize(genre, row, since)
            else:
                continue
            if result != "later":  # an older page is tried again in a wider window
                self.tried.add(row["id"])
            if result == "ok":
                accepted.append(row["id"])
                self.posted += publish(self.engine, [(genre_id, row["id"])], self.retention_hours)
        return len(accepted) >= target

    def search_topics(self, genre: dict[str, Any]) -> list[str]:
        """This update's topics for a genre of any age; the model makes new ones when they run out."""
        if genre["latest"]:
            return []
        topics, index = list(genre["topics"]), genre["topic_index"]
        if index + PER_UPDATE > len(topics) and self.llm is not None:
            try:
                made, result = make_topics(self.llm, genre, topics)
                self._count(1, result.input_tokens, result.output_tokens)
            except LLMError as exc:
                made, self.error = [], exc.code
                if exc.fatal:
                    self.llm = None
            if made:
                topics, index = made, 0
        picked = topics[index:index + PER_UPDATE]
        with self.engine.begin() as connection:
            connection.execute(text("UPDATE genres SET topics = :topics, topic_index = :index WHERE id = :id"),
                               {"id": genre["id"], "topics": topics, "index": index + len(picked)})
        return picked


CLOSE_INTERRUPTED = """
    UPDATE runs SET status = 'error', error = 'interrupted', finished_at = now() WHERE finished_at IS NULL
"""


def close_interrupted(engine: Engine) -> None:
    """Close runs that were stopped midway, unless an update is running now."""
    with engine.connect() as lock:
        if not lock.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": WORKER_LOCK}).scalar_one():
            return
        try:
            with engine.begin() as connection:
                connection.execute(text(CLOSE_INTERRUPTED))
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": WORKER_LOCK})


def run_cycle(engine: Engine, *, http: SafeHttpClient | None = None, llm: LLMClient | None = None,
              searcher: Searcher | None = None, now: datetime | None = None) -> dict[str, Any]:
    with engine.connect() as lock:
        if not lock.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": WORKER_LOCK}).scalar_one():
            return {"status": "busy"}
        try:
            return _cycle(engine, http, llm, searcher, now or datetime.now(timezone.utc))
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": WORKER_LOCK})


def _cycle(engine: Engine, http: SafeHttpClient | None, llm: LLMClient | None, searcher: Searcher | None,
           now: datetime) -> dict[str, Any]:
    with engine.begin() as connection:
        connection.execute(text(CLOSE_INTERRUPTED))  # the lock is held, so no other run is going on
        settings = dict(connection.execute(text("SELECT * FROM settings WHERE id = 1")).mappings().one())
        genres = [dict(row) for row in connection.execute(text("""
            SELECT * FROM genres WHERE weight > 0 ORDER BY position, created_at
        """)).mappings()]
        total = settings["items_per_run"]
        run_id = connection.execute(text("""
            INSERT INTO runs (target, found_target) VALUES (:target, :found_target) RETURNING id
        """), {"target": total, "found_target": CANDIDATES}).scalar_one()
    detail: dict[str, Any] = {"genres": {}}
    own_http, own_searcher = http is None, searcher is None
    http = http or SafeHttpClient()
    searcher = searcher or Searxng(config.searxng_url())
    collector = Collector(engine, searcher, run_id)
    posting: _Posting | None = None
    try:
        error = None
        try:
            client = llm or make_client(settings)
        except LLMError as exc:
            client, error = None, exc.code
        posting = _Posting(engine, http, client, now, run_id, total * CALLS_PER_POST,
                           {genre["id"]: genre for genre in genres}, settings["retention_hours"])
        posting.error = error
        for genre in genres:
            genre["search_topics"] = posting.search_topics(genre)
        found_targets = collector.collect(genres, CANDIDATES)
        found = sum(collector.found.values())
        with engine.begin() as connection:
            connection.execute(text("""
                UPDATE runs SET found = :found, searches = :searches, search_error = :error WHERE id = :id
            """), {"id": run_id, "found": found, "searches": collector.searches, "error": collector.error})
        targets = fill_by_weight({genre["id"]: genre["weight"] for genre in genres}, total,
                                 range(len(LATEST_WINDOWS)),
                                 lambda genre_id: len(posting.accepted[genre_id]), posting.fill, step=1)
        for genre in genres:
            detail["genres"][str(genre["id"])] = {
                "found": collector.found[genre["id"]], "found_target": found_targets[genre["id"]],
                "posted": len(posting.accepted[genre["id"]]), "target": targets[genre["id"]]}
        posted = posting.posted + publish(engine, [], settings["retention_hours"])
        forget_old_items(engine, now)
    except Exception as exc:
        _finish(engine, run_id, "error", posting.posted if posting else 0, posting, detail, (posting and posting.error) or type(exc).__name__)
        raise
    finally:
        if own_http:
            http.close()
        if own_searcher:
            searcher.close()
    _finish(engine, run_id, "ok", posted, posting, detail, posting.error)
    return {"status": "ok", "found": found, "searches": collector.searches, "search_error": collector.error,
            "target": total, "posted": posted, "llm_calls": posting.calls, "error": posting.error, **detail}


def _finish(engine: Engine, run_id: int, status: str, posted: int, posting: _Posting | None,
            detail: dict[str, Any], error: str | None) -> None:
    with engine.begin() as connection:
        connection.execute(text("""
            UPDATE runs SET finished_at = now(), status = :status, posted = :posted, error = :error,
              llm_calls = :calls, input_tokens = :input, output_tokens = :output, detail = CAST(:detail AS jsonb)
            WHERE id = :id
        """), {"id": run_id, "status": status, "posted": posted, "error": error,
               "calls": posting.calls if posting else 0, "input": posting.input_tokens if posting else 0,
               "output": posting.output_tokens if posting else 0,
               "detail": json.dumps(detail, ensure_ascii=False, default=str)})


def publish(engine: Engine, posts: list[tuple[Any, Any]], retention_hours: int) -> int:
    """Insert posts so the first one is newest, then drop posts past the retention time."""
    now = datetime.now(timezone.utc)
    posted = 0
    with engine.begin() as connection:
        for index, (genre_id, item_id) in enumerate(posts):
            posted += connection.execute(text("""
                INSERT INTO timeline_entries (item_id, genre_id, title, facts, interpretation, added_at)
                SELECT item_id, :genre_id, title, facts, interpretation, :added_at
                FROM summaries WHERE item_id = :item_id
                ON CONFLICT (item_id) DO NOTHING
            """), {"item_id": item_id, "genre_id": genre_id,
                   "added_at": now - timedelta(milliseconds=index)}).rowcount
            connection.execute(text("UPDATE items SET posted_at = now() WHERE id = :id"), {"id": item_id})
        connection.execute(text("DELETE FROM timeline_entries WHERE added_at < :cutoff"),
                           {"cutoff": now - timedelta(hours=retention_hours)})
    return posted


def forget_old_items(engine: Engine, now: datetime) -> None:
    """Old candidates are dropped; their URLs may be found again after that."""
    with engine.begin() as connection:
        connection.execute(text("""
            DELETE FROM items i WHERE i.found_at < :cutoff
              AND NOT EXISTS (SELECT 1 FROM timeline_entries e WHERE e.item_id = i.id)
        """), {"cutoff": now - KEEP_ITEMS})
