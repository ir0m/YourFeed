"""Web search through SearXNG (the instance in compose.yaml, or SEARXNG_URL)."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

PAGE_TEMPLATES = {None, "default.html", "paper.html"}
# Results that are not readable articles.
SKIP_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".mp3", ".mp4", ".mov", ".zip", ".gz",
                   ".exe", ".dmg", ".apk", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx")
SKIP_HOSTS = ("youtube.com", "youtu.be", "x.com", "twitter.com", "facebook.com", "instagram.com",
              "tiktok.com")


class SearchError(RuntimeError):
    """`code` is short and safe to store. Fatal errors stop searching for the rest of the update."""

    def __init__(self, code: str, fatal: bool = False):
        self.code = code
        self.fatal = fatal
        super().__init__(code)


@dataclass(frozen=True)
class Result:
    url: str
    title: str
    snippet: str = ""
    published_at: datetime | None = None


def canonical_url(value: str | None) -> str | None:
    """http(s) URL without credentials, fragment or utm_ parameters; None when unusable."""
    if not value:
        return None
    try:
        parts = urlsplit(value.strip())
        parts.port
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        return None
    query = urlencode([(key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True)
                       if not key.lower().startswith("utm_")])
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path or "/", query, ""))


def is_page(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if any(host == skip or host.endswith("." + skip) for skip in SKIP_HOSTS):
        return False
    if parts.path in {"", "/"} and not parts.query:
        return False  # a site's top page
    return not parts.path.lower().endswith(SKIP_EXTENSIONS)


def parse_date(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def parse_results(data: object) -> list[Result]:
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise SearchError("search_error")
    results = []
    for entry in data["results"]:
        if not isinstance(entry, dict) or entry.get("template") not in PAGE_TEMPLATES:
            continue
        url = canonical_url(entry.get("url") if isinstance(entry.get("url"), str) else None)
        title = " ".join(str(entry.get("title") or "").split())
        if not url or not title or not is_page(url):
            continue
        results.append(Result(url, title[:300], " ".join(str(entry.get("content") or "").split())[:1000],
                              parse_date(entry.get("publishedDate"))))
    return results


class Searxng:
    def __init__(self, base_url: str, client: httpx.Client | None = None, *, min_interval: float = 1.0):
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=30.0, follow_redirects=False)
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._last = float("-inf")

    def close(self) -> None:
        self.client.close()

    def search(self, query: str, *, page: int = 1, categories: str = "general",
               time_range: str | None = None) -> list[Result]:
        params = {"q": query, "format": "json", "categories": categories, "pageno": page}
        if time_range:
            params["time_range"] = time_range
        with self._lock:
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                response = self.client.get(f"{self.base_url}/search", params=params,
                                           headers={"Accept": "application/json"})
            except httpx.TimeoutException as exc:
                raise SearchError("search_timeout") from exc
            except httpx.TransportError as exc:
                raise SearchError("search_unavailable", fatal=True) from exc
        if response.status_code == 403:
            raise SearchError("search_format", fatal=True)  # json is not in search.formats
        if response.status_code == 429:
            raise SearchError("search_limited", fatal=True)
        if response.status_code != 200:
            raise SearchError("search_error")
        try:
            return parse_results(response.json())
        except ValueError as exc:
            raise SearchError("search_error") from exc
