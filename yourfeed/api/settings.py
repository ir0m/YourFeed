"""Settings and genres."""

from __future__ import annotations

from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import AfterValidator, BaseModel, Field, StringConstraints, model_validator
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError

from yourfeed import config
from yourfeed.db import get_engine
from yourfeed.llm import DEFAULT_MODELS
from yourfeed.search.language import LOCALES

router = APIRouter(prefix="/v1")


def _site(value: str) -> str:
    """A site to search first: `example.com` or `https://example.com/path`, stored as scheme://host/path."""
    if "://" not in value:
        value = "https://" + value
    try:
        parts = urlsplit(value)
        parts.port
    except ValueError as exc:
        raise ValueError("invalid site") from exc
    host = (parts.hostname or "").lower()
    if (parts.scheme not in {"http", "https"} or "." not in host or parts.username or parts.password
            or any(char.isspace() for char in value)):
        raise ValueError("invalid site")
    return f"{parts.scheme}://{host}{parts.path.rstrip('/')}"


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))


def _locales(values: list[str]) -> list[str]:
    """Chosen locales in order; "all" stands alone."""
    values = list(dict.fromkeys(values))
    return ["all"] if "all" in values else values


Site = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
                 AfterValidator(_site)]
Term = Annotated[str, StringConstraints(max_length=60)]
Terms = Annotated[list[Term], AfterValidator(_unique), Field(max_length=50)]


class SettingsIn(BaseModel):
    interval_minutes: Literal[15, 30, 60, 120, 180, 360, 720, 1440]
    items_per_run: int = Field(ge=1, le=50)
    retention_hours: int = Field(ge=1, le=168)
    llm_provider: Literal["openai", "anthropic", "claude_cli", "codex_cli", "gemini_cli"]
    llm_model: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] = ""


class GenreIn(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    latest: bool = True
    locales: Annotated[list[Literal[LOCALES]], AfterValidator(_locales),
                       Field(min_length=1, max_length=len(LOCALES))] = Field(default_factory=lambda: ["ja-JP"])
    translate: bool = True
    sites: Annotated[list[Site], AfterValidator(_unique), Field(max_length=20)] = Field(default_factory=list)
    focus_terms: Terms = Field(default_factory=list)
    exclude_terms: Terms = Field(default_factory=list)
    discovery_percent: int = Field(default=20, ge=0, le=100)
    min_chars: int = Field(default=100, ge=40, le=800)
    max_chars: int = Field(default=160, ge=40, le=800)
    stance: int = Field(default=1, ge=0, le=3)

    @model_validator(mode="after")
    def _ranges(self) -> GenreIn:
        if self.max_chars < self.min_chars + 20:
            raise ValueError("max_chars must be at least min_chars + 20")
        return self


class WeightIn(BaseModel):
    weight: int = Field(ge=0, le=100)


SEARCH_FIELDS = ("name", "latest", "locales", "sites", "focus_terms", "exclude_terms", "discovery_percent")
STYLE_FIELDS = ("min_chars", "max_chars", "stance", "focus_terms", "translate")


def _iso(value):
    return value.isoformat() if value is not None else None


def _genre_rows(connection: Connection) -> list[dict]:
    return [{"id": str(row["id"]), "name": row["name"], "weight": row["weight"]}
            for row in connection.execute(text("SELECT id, name, weight FROM genres ORDER BY position, created_at"))
            .mappings()]


@router.get("/settings")
def get_settings(engine: Engine = Depends(get_engine)) -> dict:
    with engine.connect() as connection:
        row = dict(connection.execute(text("SELECT * FROM settings WHERE id = 1")).mappings().one())
        run = connection.execute(text("""
            SELECT started_at, finished_at, status, target, posted, found, found_target, error, search_error
            FROM runs ORDER BY started_at DESC LIMIT 1
        """)).mappings().one_or_none()
        genres = _genre_rows(connection)
    return {
        "interval_minutes": row["interval_minutes"], "items_per_run": row["items_per_run"],
        "retention_hours": row["retention_hours"], "llm_provider": row["llm_provider"],
        "llm_model": row["llm_model"], "default_models": DEFAULT_MODELS,
        "openai_base_url": config.openai_base_url(),
        "keys": {"openai": config.api_key("openai") is not None,
                 "anthropic": config.api_key("anthropic") is not None},
        "refresh_requested_at": _iso(row["refresh_requested_at"]),
        "last_run": ({**run, "started_at": _iso(run["started_at"]), "finished_at": _iso(run["finished_at"])}
                     if run else None),
        "genres": genres,
    }


@router.put("/settings")
def put_settings(body: SettingsIn, engine: Engine = Depends(get_engine)) -> dict:
    with engine.begin() as connection:
        connection.execute(text("""
            UPDATE settings SET interval_minutes = :interval_minutes, items_per_run = :items_per_run,
              retention_hours = :retention_hours, llm_provider = :llm_provider, llm_model = :llm_model,
              updated_at = now()
            WHERE id = 1
        """), body.model_dump())
    return get_settings(engine)


@router.post("/refresh", status_code=204)
def request_refresh(engine: Engine = Depends(get_engine)) -> Response:
    with engine.begin() as connection:
        connection.execute(text("UPDATE settings SET refresh_requested_at = now() WHERE id = 1"))
    return Response(status_code=204)


def _genre(connection: Connection, genre_id: UUID) -> dict:
    row = connection.execute(text("SELECT * FROM genres WHERE id = :id"), {"id": genre_id}).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, "not found")
    return {"id": str(row["id"]), "name": row["name"], "weight": row["weight"], "latest": row["latest"],
            "locales": row["locales"], "translate": row["translate"], "sites": row["sites"],
            "focus_terms": row["focus_terms"], "exclude_terms": row["exclude_terms"],
            "discovery_percent": row["discovery_percent"], "min_chars": row["min_chars"],
            "max_chars": row["max_chars"], "stance": row["stance"]}


@router.get("/genres/{genre_id}")
def get_genre(genre_id: UUID, engine: Engine = Depends(get_engine)) -> dict:
    with engine.connect() as connection:
        return _genre(connection, genre_id)


@router.post("/genres", status_code=201)
def create_genre(body: GenreIn, engine: Engine = Depends(get_engine)) -> dict:
    try:
        with engine.begin() as connection:
            genre_id = connection.execute(text("""
                INSERT INTO genres (name, latest, locales, translate, sites, focus_terms, exclude_terms, discovery_percent,
                                    min_chars, max_chars, stance, position)
                VALUES (:name, :latest, :locales, :translate, :sites, :focus_terms, :exclude_terms, :discovery_percent,
                        :min_chars, :max_chars, :stance, (SELECT COALESCE(max(position) + 1, 0) FROM genres))
                RETURNING id
            """), body.model_dump()).scalar_one()
            return _genre(connection, genre_id)
    except IntegrityError as exc:
        raise HTTPException(409, "name already used") from exc


@router.put("/genres/{genre_id}")
def update_genre(genre_id: UUID, body: GenreIn, engine: Engine = Depends(get_engine)) -> dict:
    try:
        with engine.begin() as connection:
            before = _genre(connection, genre_id)
            connection.execute(text("""
                UPDATE genres SET name = :name, latest = :latest, locales = :locales, translate = :translate,
                  sites = :sites, focus_terms = :focus_terms,
                  exclude_terms = :exclude_terms, discovery_percent = :discovery_percent,
                  min_chars = :min_chars, max_chars = :max_chars, stance = :stance, updated_at = now()
                WHERE id = :id
            """), {**body.model_dump(), "id": genre_id})
            if any(before[key] != getattr(body, key) for key in SEARCH_FIELDS):
                # Candidates and topics from the old search are dropped; the next update searches again.
                connection.execute(text("DELETE FROM items WHERE genre_id = :id AND posted_at IS NULL"),
                                   {"id": genre_id})
                connection.execute(text("""
                    UPDATE genres SET topics = '{}', topic_index = 0, translations = '{}' WHERE id = :id
                """), {"id": genre_id})
            elif any(before[key] != getattr(body, key) for key in STYLE_FIELDS):
                # Summaries not yet posted were written for the old style.
                connection.execute(text("""
                    DELETE FROM summaries sm USING items i
                    WHERE sm.item_id = i.id AND i.genre_id = :id AND i.posted_at IS NULL
                """), {"id": genre_id})
                connection.execute(text("""
                    UPDATE items SET attempted_at = NULL, skip_reason = NULL
                    WHERE genre_id = :id AND posted_at IS NULL AND skip_reason = 'rejected'
                """), {"id": genre_id})
            return _genre(connection, genre_id)
    except IntegrityError as exc:
        raise HTTPException(409, "name already used") from exc


@router.patch("/genres/{genre_id}")
def set_weight(genre_id: UUID, body: WeightIn, engine: Engine = Depends(get_engine)) -> dict:
    with engine.begin() as connection:
        if not connection.execute(text("UPDATE genres SET weight = :weight, updated_at = now() WHERE id = :id"),
                                  {"id": genre_id, "weight": body.weight}).rowcount:
            raise HTTPException(404, "not found")
        return {"genres": _genre_rows(connection)}


@router.delete("/genres/{genre_id}", status_code=204)
def delete_genre(genre_id: UUID, engine: Engine = Depends(get_engine)) -> Response:
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM genres WHERE id = :id"), {"id": genre_id})
    return Response(status_code=204)
