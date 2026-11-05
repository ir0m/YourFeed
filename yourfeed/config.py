"""Environment settings. Everything else lives in the database."""

from __future__ import annotations

import os
from collections.abc import Mapping
from zoneinfo import ZoneInfo

DEFAULT_ALLOWED_HOSTS = ("127.0.0.1", "localhost")


def database_url(environ: Mapping[str, str] = os.environ) -> str:
    url = environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def extra_hosts(environ: Mapping[str, str] = os.environ) -> list[str]:
    hosts = [host.strip().lower() for host in environ.get("YOURFEED_ALLOWED_HOSTS", "").split(",")
             if host.strip()]
    if any("*" in host for host in hosts):
        raise ValueError("YOURFEED_ALLOWED_HOSTS accepts exact host names only")
    return hosts


def allowed_hosts(environ: Mapping[str, str] = os.environ) -> list[str]:
    return list(dict.fromkeys([*DEFAULT_ALLOWED_HOSTS, *extra_hosts(environ)]))


def timezone(environ: Mapping[str, str] = os.environ) -> ZoneInfo:
    return ZoneInfo(environ.get("YOURFEED_TIMEZONE") or "Asia/Tokyo")


def allow_private_urls(environ: Mapping[str, str] = os.environ) -> bool:
    return environ.get("YOURFEED_ALLOW_PRIVATE_URLS") == "1"


def openai_base_url(environ: Mapping[str, str] = os.environ) -> str:
    return environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"


def searxng_url(environ: Mapping[str, str] = os.environ) -> str:
    return environ.get("SEARXNG_URL") or "http://127.0.0.1:8888"


def api_key(provider: str, environ: Mapping[str, str] = os.environ) -> str | None:
    name = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}.get(provider)
    return (environ.get(name) or None) if name else None
