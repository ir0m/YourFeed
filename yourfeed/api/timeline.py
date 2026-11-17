"""Read the timeline."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.engine import Engine

from yourfeed.db import WORKER_LOCK, get_engine

router = APIRouter(prefix="/v1")
PAGE_SIZE = 50

# An entry is a summary of an article (item and genre) or a persona's post.
ENTRY_QUERY = """
    SELECT e.id, e.title, e.facts, e.interpretation, e.added_at,
           i.url, i.title AS original_title, i.published_at, i.site_name, g.name AS genre,
           p.name AS author, p.profile
    FROM timeline_entries e
    LEFT JOIN items i ON i.id = e.item_id
    LEFT JOIN genres g ON g.id = e.genre_id
    LEFT JOIN personas p ON p.id = e.persona_id
"""

# True while the worker holds its lock, that is, while an update is running.
UPDATING_QUERY = """
    SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' AND granted
                   AND classid = 0 AND objid = :key AND objsubid = 1
                   AND database = (SELECT oid FROM pg_database WHERE datname = current_database()))
"""


def _cutoff(engine: Engine) -> datetime:
    with engine.connect() as connection:
        hours = connection.execute(text("SELECT retention_hours FROM settings WHERE id = 1")).scalar_one()
    return datetime.now(timezone.utc) - timedelta(hours=hours)


def _source_name(row) -> str:
    return row["site_name"] or (urlsplit(row["url"]).hostname or "").removeprefix("www.")


def _parse_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        moment, raw_id = cursor.rsplit("|", 1)
        parsed = datetime.fromisoformat(moment)
        if parsed.tzinfo is None:
            raise ValueError
        return parsed, UUID(raw_id)
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, "invalid cursor") from exc


@router.get("/timeline")
def timeline(cursor: str | None = None, engine: Engine = Depends(get_engine)) -> dict:
    params: dict = {"cutoff": _cutoff(engine), "limit": PAGE_SIZE}
    condition = ""
    if cursor:
        params["before_at"], params["before_id"] = _parse_cursor(cursor)
        condition = " AND (e.added_at, e.id) < (:before_at, :before_id)"
    with engine.connect() as connection:
        rows = connection.execute(text(ENTRY_QUERY + " WHERE e.added_at > :cutoff" + condition
                                       + " ORDER BY e.added_at DESC, e.id DESC LIMIT :limit"),
                                  params).mappings().all()
        updating = connection.execute(text(UPDATING_QUERY), {"key": WORKER_LOCK}).scalar_one()
    items = [{"id": str(row["id"]), "kind": "post" if row["author"] is not None else "article",
              "title": row["title"], "author": row["author"], "body": row["facts"] + row["interpretation"],
              "added_at": row["added_at"].isoformat()} for row in rows]
    next_cursor = (f"{rows[-1]['added_at'].isoformat()}|{rows[-1]['id']}"
                   if len(rows) == PAGE_SIZE else None)
    return {"items": items, "next_cursor": next_cursor, "updating": updating}


@router.get("/timeline/{entry_id}")
def timeline_item(entry_id: UUID, engine: Engine = Depends(get_engine)) -> dict:
    with engine.connect() as connection:
        row = connection.execute(text(ENTRY_QUERY + " WHERE e.id = :id AND e.added_at > :cutoff"),
                                 {"id": entry_id, "cutoff": _cutoff(engine)}).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, "not found")
    if row["author"] is not None:
        return {"id": str(row["id"]), "kind": "post", "author": row["author"], "profile": row["profile"],
                "body": row["facts"], "added_at": row["added_at"].isoformat()}
    return {"id": str(row["id"]), "kind": "article", "title": row["title"], "facts": row["facts"],
            "interpretation": row["interpretation"], "genre": row["genre"],
            "original_title": row["original_title"], "url": row["url"], "source": _source_name(row),
            "published_at": row["published_at"].isoformat() if row["published_at"] else None,
            "added_at": row["added_at"].isoformat()}
