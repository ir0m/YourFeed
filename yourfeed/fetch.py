"""HTTP GET limited to public hosts, with size, time and redirect limits."""

from __future__ import annotations

import ipaddress
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit

import httpx

from yourfeed import config

USER_AGENT = "YourFeed/0.1"
REDIRECTS = {301, 302, 303, 307, 308}


class FetchError(RuntimeError):
    """A fetch that failed; `code` is short and safe to store."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class TemporaryFetchError(FetchError):
    pass


class HttpStatusError(FetchError):
    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__(f"http_{status_code}")


class RateLimited(TemporaryFetchError):
    def __init__(self, retry_after_seconds: float):
        self.retry_after_seconds = retry_after_seconds
        super().__init__("rate_limited")


@dataclass(frozen=True)
class Response:
    status_code: int
    body: bytes
    url: str
    content_type: str | None = None
    etag: str | None = None
    last_modified: str | None = None

    @property
    def media_type(self) -> str:
        return (self.content_type or "").split(";")[0].strip().lower()


Resolver = Callable[[str], list[str]]


def system_resolver(host: str) -> list[str]:
    try:
        return [info[4][0] for info in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)]
    except socket.gaierror as exc:
        raise FetchError("host_not_found") from exc


def _public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def check_url(url: str, *, resolver: Resolver = system_resolver, allow_private: bool = False) -> None:
    try:
        parts = urlsplit(url)
        parts.port
    except ValueError as exc:
        raise FetchError("invalid_url") from exc
    host = (parts.hostname or "").lower()
    if parts.scheme not in {"http", "https"} or not host or parts.username or parts.password:
        raise FetchError("invalid_url")
    if allow_private:
        return
    try:
        addresses = [str(ipaddress.ip_address(host))]
    except ValueError:
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            raise FetchError("private_address") from None
        addresses = resolver(host)
    if not addresses or not all(_public(address) for address in addresses):
        raise FetchError("private_address")


def _retry_after(value: str | None) -> float:
    if not value:
        return 60.0
    try:
        return min(max(float(value), 0.0), 3600.0)
    except ValueError:
        try:
            moment = parsedate_to_datetime(value)
            return min(max((moment - datetime.now(timezone.utc)).total_seconds(), 0.0), 3600.0)
        except (TypeError, ValueError, OverflowError):
            return 60.0


class SafeHttpClient:
    def __init__(self, client: httpx.Client | None = None, *, timeout: float = 12.0,
                 max_bytes: int = 3_000_000, resolver: Resolver = system_resolver,
                 allow_private: bool | None = None):
        self.client = client or httpx.Client(timeout=timeout, follow_redirects=False)
        self.max_bytes = max_bytes
        self.resolver = resolver
        self.allow_private = config.allow_private_urls() if allow_private is None else allow_private
        self._lock = threading.Lock()
        self._last_request: dict[str, float] = {}

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> SafeHttpClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _check_peer(self, response: httpx.Response) -> None:
        """Re-check the address actually connected to, in case DNS changed after check_url."""
        if self.allow_private:
            return
        stream = response.extensions.get("network_stream")
        address = stream.get_extra_info("server_addr") if stream is not None else None
        if address and not _public(str(address[0])):
            raise FetchError("private_address")

    def fetch(self, url: str, *, accept: str | None = None, etag: str | None = None,
              last_modified: str | None = None, min_interval: float = 0.0,
              max_redirects: int = 5) -> Response:
        headers = {"User-Agent": USER_AGENT, "Accept": accept or "*/*"}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        current = url
        for _ in range(max_redirects + 1):
            check_url(current, resolver=self.resolver, allow_private=self.allow_private)
            host = urlsplit(current).hostname or ""
            with self._lock:
                wait = min_interval - (time.monotonic() - self._last_request.get(host, float("-inf")))
                if wait > 0:
                    time.sleep(wait)
                self._last_request[host] = time.monotonic()
                try:
                    with self.client.stream("GET", current, headers=headers) as response:
                        status = response.status_code
                        if status in REDIRECTS:
                            location = response.headers.get("Location")
                            if not location:
                                raise FetchError("bad_redirect")
                            current = urljoin(current, location)
                            continue
                        self._check_peer(response)
                        meta = dict(url=current, content_type=response.headers.get("Content-Type"),
                                    etag=response.headers.get("ETag"),
                                    last_modified=response.headers.get("Last-Modified"))
                        if status == 304:
                            return Response(304, b"", **meta)
                        if status == 429:
                            raise RateLimited(_retry_after(response.headers.get("Retry-After")))
                        if status >= 500:
                            raise TemporaryFetchError(f"http_{status}")
                        if status >= 400:
                            raise HttpStatusError(status)
                        chunks: list[bytes] = []
                        total = 0
                        for chunk in response.iter_bytes():
                            total += len(chunk)
                            if total > self.max_bytes:
                                raise FetchError("too_large")
                            chunks.append(chunk)
                        return Response(status, b"".join(chunks), **meta)
                except httpx.TimeoutException as exc:
                    raise TemporaryFetchError("timeout") from exc
                except httpx.TransportError as exc:
                    raise TemporaryFetchError("connection_error") from exc
        raise FetchError("too_many_redirects")
