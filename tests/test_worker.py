from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from starlette.requests import Request

from yourfeed.config import allowed_hosts, database_url
from yourfeed.main import same_origin
from yourfeed.worker import is_due, slot_start

TOKYO = ZoneInfo("Asia/Tokyo")


def test_slots_start_at_local_midnight():
    now = datetime(2026, 11, 4, 4, 50, tzinfo=timezone.utc)  # 13:50 in Tokyo
    assert slot_start(now, 60, TOKYO) == datetime(2026, 11, 4, 4, 0, tzinfo=timezone.utc)
    assert slot_start(now, 180, TOKYO).astimezone(TOKYO).hour == 12
    assert slot_start(now, 1440, TOKYO).astimezone(TOKYO).hour == 0


def test_due_after_the_slot_changes_or_on_request():
    now = datetime(2026, 11, 4, 4, 50, tzinfo=timezone.utc)
    assert is_due(now, 60, TOKYO, None, None)
    assert not is_due(now, 60, TOKYO, now - timedelta(minutes=30), None)
    assert is_due(now, 60, TOKYO, now - timedelta(minutes=55), None)
    assert is_due(now, 60, TOKYO, now - timedelta(minutes=30), now - timedelta(minutes=1))
    assert not is_due(now, 60, TOKYO, now - timedelta(minutes=1), now - timedelta(minutes=30))


def request(method, headers):
    scope = {"type": "http", "method": method, "path": "/v1/settings",
             "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()]}
    return Request(scope)


def test_writes_need_the_same_origin(monkeypatch):
    monkeypatch.setenv("YOURFEED_ALLOWED_HOSTS", "feed.example.org")
    host = {"Host": "feed.example:8000"}
    assert same_origin(request("GET", {**host, "Origin": "https://evil.example"}))
    assert same_origin(request("PUT", {**host, "Origin": "http://feed.example:8000",
                                             "Sec-Fetch-Site": "same-origin"}))
    assert same_origin(request("POST", host))
    assert not same_origin(request("POST", {**host, "Origin": "https://evil.example"}))
    assert not same_origin(request("DELETE", {**host, "Sec-Fetch-Site": "cross-site"}))
    assert not same_origin(request("PUT", {**host, "Sec-Fetch-Site": "same-site",
                                                 "Origin": "http://feed.example:8000"}))
    assert not same_origin(request("PUT", {**host, "Origin": "null"}))
    assert not same_origin(request("GET", {**host, "Sec-Fetch-Site": "cross-site"}))
    # Behind a proxy that rewrites Host, a configured name is still accepted.
    assert same_origin(request("PUT", {"Host": "127.0.0.1:8000", "Origin": "https://feed.example.org"}))
    assert not same_origin(request("PUT", {"Host": "127.0.0.1:8000", "Origin": "https://other.example.org"}))


def test_environment_helpers():
    assert database_url({"DATABASE_URL": "postgresql://u@h/db"}) == "postgresql+psycopg://u@h/db"
    assert database_url({"DATABASE_URL": "postgresql+psycopg://u@h/db"}) == "postgresql+psycopg://u@h/db"
    assert "feed.example" in allowed_hosts({"YOURFEED_ALLOWED_HOSTS": " Feed.example ,"})
