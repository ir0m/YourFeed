"""Readable text from a found page (HTML or PDF), fetched politely and never stored."""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass
from datetime import datetime, time
from urllib import robotparser
from urllib.parse import urlsplit

import trafilatura
from pypdf import PdfReader

from yourfeed import config
from yourfeed.fetch import FetchError, HttpStatusError, RateLimited, SafeHttpClient, TemporaryFetchError

ROBOTS_AGENT = "YourFeed"
TEXT_LIMIT = 6000
MIN_TEXT = 200
PDF_PAGES = 20
RETRYABLE = {"rate_limited", "host_blocked", "robots_unavailable", "temporary_error"}
HTML_TYPES = {"text/html", "application/xhtml+xml"}
ACCEPT = "text/html,application/xhtml+xml,application/pdf;q=0.9"

logging.getLogger("pypdf").setLevel(logging.ERROR)
logging.getLogger("trafilatura").setLevel(logging.ERROR)
logging.getLogger("htmldate").setLevel(logging.ERROR)


@dataclass(frozen=True)
class Page:
    text: str
    title: str | None = None
    site_name: str | None = None
    published_at: datetime | None = None
    is_pdf: bool = False


def decode_html(body: bytes, content_type: str | None) -> str:
    match = (re.search(r"charset=[\"']?([\w-]+)", content_type or "", re.IGNORECASE)
             or re.search(r"charset=[\"']?([\w-]+)", body[:4096].decode("ascii", "ignore"), re.IGNORECASE))
    declared = match.group(1).lower() if match else None
    if declared in {"shift_jis", "shift-jis", "sjis", "x-sjis"}:
        declared = "cp932"
    for encoding in filter(None, (declared, "utf-8", "cp932")):
        try:
            return body.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")


def _local_date(value: str | None) -> datetime | None:
    try:
        day = datetime.strptime(value or "", "%Y-%m-%d").date()
    except ValueError:
        return None
    return datetime.combine(day, time(0), tzinfo=config.timezone())


def _clip(value: str | None, limit: int = 300) -> str | None:
    value = " ".join((value or "").split())
    return value[:limit] or None


def html_page(html: str, url: str) -> Page | None:
    document = trafilatura.bare_extraction(
        html, url=url, with_metadata=True, include_comments=False, include_tables=True,
        favor_precision=True, date_extraction_params={"extensive_search": False, "original_date": True})
    if document is None or not document.text:
        return None
    return Page(document.text[:TEXT_LIMIT], _clip(document.title), _clip(document.sitename, 100),
                _local_date(document.date))


def pdf_page(body: bytes) -> Page | None:
    reader = PdfReader(io.BytesIO(body))
    if reader.is_encrypted and not reader.decrypt(""):
        return None
    parts: list[str] = []
    size = 0
    for page in reader.pages[:PDF_PAGES]:
        part = page.extract_text() or ""
        parts.append(part)
        size += len(part)
        if size >= TEXT_LIMIT:
            break
    text = "\n".join(" ".join(line.split()) for line in "\n".join(parts).splitlines() if line.strip())
    metadata = reader.metadata
    created = metadata.creation_date if metadata else None
    return Page(text[:TEXT_LIMIT], _clip(metadata.title if metadata else None),
                published_at=created if isinstance(created, datetime) and created.tzinfo else None, is_pdf=True)


class PageFetcher:
    """Fetch pages, following robots.txt and backing off per host."""

    def __init__(self, http: SafeHttpClient) -> None:
        self.http = http
        self.robots: dict[str, robotparser.RobotFileParser | None] = {}
        self.blocked: set[str] = set()

    def _robots(self, scheme: str, host: str) -> robotparser.RobotFileParser | None:
        if host not in self.robots:
            parser: robotparser.RobotFileParser | None = robotparser.RobotFileParser()
            try:
                response = self.http.fetch(f"{scheme}://{host}/robots.txt", accept="text/plain")
                parser.parse(decode_html(response.body, response.content_type).splitlines())
            except HttpStatusError as exc:
                if exc.status_code in {401, 403} or exc.status_code >= 500:
                    parser = None
                else:
                    parser.parse([])
            except FetchError:
                parser = None
            self.robots[host] = parser
        return self.robots[host]

    def read(self, url: str) -> tuple[Page | None, str | None]:
        """(page, None) or (None, reason)."""
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if host in self.blocked:
            return None, "host_blocked"
        robots = self._robots(parts.scheme, host)
        if robots is None:
            self.blocked.add(host)
            return None, "robots_unavailable"
        if not robots.can_fetch(ROBOTS_AGENT, url):
            return None, "robots_disallow"
        delay = max(1.0, float(robots.crawl_delay(ROBOTS_AGENT) or 0))
        try:
            response = self.http.fetch(url, min_interval=delay, accept=ACCEPT)
        except RateLimited:
            self.blocked.add(host)
            return None, "rate_limited"
        except HttpStatusError as exc:
            if exc.status_code in {401, 403}:
                self.blocked.add(host)
            return None, f"http_{exc.status_code}"
        except TemporaryFetchError:
            return None, "temporary_error"
        except FetchError as exc:
            return None, exc.code
        try:
            if response.media_type == "application/pdf" or response.body.startswith(b"%PDF-"):
                page = pdf_page(response.body)
            elif response.media_type in HTML_TYPES:
                page = html_page(decode_html(response.body, response.content_type), response.url)
            else:
                return None, "not_a_page"
        except Exception:  # parsers of untrusted documents may fail in many ways
            return None, "unreadable"
        if page is None or len(page.text) < MIN_TEXT:
            return None, "too_little_text"
        return page, None
