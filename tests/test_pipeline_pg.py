import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from yourfeed.db import get_engine
from yourfeed.fetch import SafeHttpClient
from yourfeed.llm import LLMResult
from yourfeed.main import app
from yourfeed.pipeline import run as run_module
from yourfeed.pipeline.run import publish, run_cycle
from yourfeed.search.searxng import Result, SearchError

NOW = datetime.now(timezone.utc)
SAME_ORIGIN = {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}


class FakeSearch:
    """Three results per page and two pages per query and time range."""

    def __init__(self, down=False):
        self.down = down
        self.queries = []

    def search(self, query, *, page=1, categories="general", time_range=None):
        if self.down:
            raise SearchError("search_unavailable", fatal=True)
        self.queries.append((query, page, categories, time_range))
        if query.startswith("空") or page > 2:
            return []
        if "site:lab.example" in query:
            host = "lab.example"
        elif query.startswith("科学"):
            host = "sci.example"
        else:
            host = "gov.example"
        slug = "q" if "量子" in query else "n"
        results = [Result(f"https://{host}/{slug}/{time_range}/{page}/{index}", f"{host} {slug} {page}-{index}",
                          "説明") for index in range(3)]
        if host == "sci.example" and page == 1:
            results[0] = Result("https://sci.example/hr", "人事のお知らせ", "")
        return results


def page(title, published):
    body = f"<p>{title}について、機構は計画を発表した。費用は300億円の見込み。</p>" * 6
    date = f"<meta property='article:published_time' content='{published}'>" if published else ""
    return (f"<html><head><title>{title}</title><meta property='og:site_name' content='Example'>{date}</head>"
            f"<body><article><h1>{title}</h1>{body}</article></body></html>").encode()


def handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/robots.txt":
        return httpx.Response(404)
    # gov.example's first result is an old page and its second has no date.
    published = NOW.date().isoformat()
    if request.url.host == "gov.example":
        published = {"/n/day/1/0": "2026-01-01", "/n/day/2/0": ""}.get(request.url.path, published)
    return httpx.Response(200, content=page(f"記事{request.url.path}", published),
                          headers={"Content-Type": "text/html; charset=utf-8"})


def fake_http():
    return SafeHttpClient(httpx.Client(transport=httpx.MockTransport(handler)),
                          resolver=lambda host: ["93.184.216.34"], allow_private=False)


class FakeLLM:
    provider, model = "fake", "fake-1"

    def __init__(self):
        self.calls = 0

    def complete(self, system, messages, schema):
        self.calls += 1
        if self.calls == 1:  # the first answer is too short and gets one fix request
            return LLMResult(json.dumps({"skip": False, "title": "短い", "facts": "短い。", "interpretation": ""}))
        fact_only = "interpretation: 常に空文字" in system
        facts = "あ" * (80 if fact_only else 75)
        interpretation = "" if fact_only else "い" * 25
        return LLMResult(json.dumps({"skip": False, "title": "見出し", "facts": facts,
                                     "interpretation": interpretation}), 100, 20)


@pytest.fixture
def client(engine):
    app.dependency_overrides[get_engine] = lambda: engine
    yield TestClient(app)
    app.dependency_overrides.clear()


def genre(name, **extra):
    return {"name": name, "min_chars": 60, "max_chars": 120, **extra}


def create(client, name, weight, **extra):
    created = client.post("/v1/genres", json=genre(name, **extra), headers=SAME_ORIGIN)
    assert created.status_code == 201
    assert client.patch(f"/v1/genres/{created.json()['id']}", json={"weight": weight},
                        headers=SAME_ORIGIN).status_code == 200
    return created.json()


def settings_body(client, **changes):
    settings = client.get("/v1/settings").json()
    keys = ("interval_minutes", "items_per_run", "retention_hours", "llm_provider", "llm_model")
    return {**{key: settings[key] for key in keys}, **changes}


def test_settings_api_validates_and_guards_requests(client, engine):
    settings = client.get("/v1/settings").json()
    assert settings["interval_minutes"] == 60 and settings["genres"] == [] and settings["last_run"] is None
    body = settings_body(client)
    assert client.put("/v1/settings", json={**body, "interval_minutes": 7}, headers=SAME_ORIGIN).status_code == 422
    assert client.put("/v1/settings", json={**body, "items_per_run": 51}, headers=SAME_ORIGIN).status_code == 422
    assert client.put("/v1/settings", json={**body, "items_per_run": 4}, headers=SAME_ORIGIN).json()["items_per_run"] == 4
    assert client.put("/v1/settings", json=body, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/v1/timeline", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.get("/v1/settings", headers={"Host": "evil.example"}).status_code == 400

    only_name = client.post("/v1/genres", json={"name": "宇宙"}, headers=SAME_ORIGIN)
    assert only_name.status_code == 201
    assert only_name.json()["sites"] == [] and only_name.json()["focus_terms"] == []
    created = client.post("/v1/genres", json=genre("科学", sites=["Example.com/Tech/", "https://lab.example"]),
                          headers=SAME_ORIGIN)
    assert created.json()["sites"] == ["https://example.com/Tech", "https://lab.example"]
    for bad in (["localhost"], ["ftp://example.com"], ["https://user:pw@example.com"], ["exa mple.com"]):
        assert client.post("/v1/genres", json=genre("x", sites=bad), headers=SAME_ORIGIN).status_code == 422
    assert client.post("/v1/genres", json=genre("科学"), headers=SAME_ORIGIN).status_code == 409
    assert client.post("/v1/genres", json=genre("x", min_chars=100, max_chars=110),
                       headers=SAME_ORIGIN).status_code == 422
    genre_id = created.json()["id"]
    assert client.patch(f"/v1/genres/{genre_id}", json={"weight": 101}, headers=SAME_ORIGIN).status_code == 422
    rows = client.patch(f"/v1/genres/{genre_id}", json={"weight": 7}, headers=SAME_ORIGIN).json()["genres"]
    assert rows[1] == {"id": genre_id, "name": "科学", "weight": 7}

    # Changing what to search drops candidates that were not posted yet.
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO items (genre_id, url, title) VALUES (:id, 'https://a.example/', 'a')"),
                           {"id": genre_id})
    assert client.put(f"/v1/genres/{genre_id}", json=genre("科学", sites=["lab.example"]),
                      headers=SAME_ORIGIN).json()["weight"] == 7
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM items")).scalar_one() == 0
    assert client.delete(f"/v1/genres/{genre_id}", headers=SAME_ORIGIN).status_code == 204
    assert client.get(f"/v1/genres/{genre_id}").status_code == 404


def test_every_update_searches_the_candidates_and_fills_the_count(client, engine, monkeypatch):
    monkeypatch.setattr(run_module, "CANDIDATES", 12)
    client.put("/v1/settings", json=settings_body(client, items_per_run=6), headers=SAME_ORIGIN)
    science = create(client, "科学", 3, sites=["lab.example"], focus_terms=["量子"], exclude_terms=["人事"],
                     discovery_percent=34)
    policy = create(client, "政策", 1, stance=0)
    create(client, "空", 5)

    search, llm = FakeSearch(), FakeLLM()
    result = run_cycle(engine, http=fake_http(), llm=llm, searcher=search)
    # 空 finds nothing, so its share of the 12 candidates goes to 科学 and 政策 by weight.
    assert result["found"] == 12 and result["search_error"] is None
    assert {key: value["found"] for key, value in result["genres"].items()} == {
        science["id"]: 9, policy["id"]: 3, next(key for key in result["genres"]
                                                  if key not in (science["id"], policy["id"])): 0}
    assert search.queries[0] == ("科学 量子 -人事 site:lab.example", 1, "news", "day")
    with engine.connect() as connection:
        rows = connection.execute(text("""
            SELECT i.url, i.title FROM items i JOIN genres g ON g.id = i.genre_id
            WHERE g.name = '科学' ORDER BY i.rank
        """)).all()
    assert [url for url, _ in rows[:3]] == [f"https://lab.example/q/day/1/{index}" for index in range(3)]
    assert not any("人事" in title for _, title in rows)

    # 政策's old and undated pages wait for a wider window, so 科学 takes its share.
    assert (result["target"], result["posted"]) == (6, 6)
    assert llm.calls == 7
    page = client.get("/v1/timeline").json()
    assert len(page["items"]) == 6
    with engine.connect() as connection:
        posted = connection.execute(text("""
            SELECT g.name, i.url FROM timeline_entries e JOIN items i ON i.id = e.item_id
            JOIN genres g ON g.id = e.genre_id ORDER BY e.added_at DESC
        """)).all()
        run = connection.execute(text("SELECT status, target, posted, found, llm_calls, input_tokens FROM runs")).one()
    assert sorted(name for name, _ in posted) == ["政策", "科学", "科学", "科学", "科学", "科学"]
    assert [url for name, url in posted if name == "政策"] == ["https://gov.example/n/day/2/1"]
    assert run == ("ok", 6, 6, 12, 7, 600)
    detail = client.get(f"/v1/timeline/{page['items'][0]['id']}").json()
    assert detail["source"] == "Example" and detail["original_title"].startswith("記事/")
    assert page["items"][0]["body"] == detail["facts"] + detail["interpretation"]

    # With search down, the update still posts from earlier candidates, the old and undated ones last.
    again = run_cycle(engine, http=fake_http(), llm=FakeLLM(), searcher=FakeSearch(down=True))
    assert (again["found"], again["search_error"], again["posted"]) == (0, "search_unavailable", 6)
    status = client.get("/v1/settings").json()["last_run"]
    assert (status["found"], status["found_target"], status["search_error"]) == (0, 12, "search_unavailable")
    with engine.connect() as connection:
        assert connection.execute(text("""
            SELECT bool_and(posted_at IS NOT NULL) FROM items WHERE genre_id = :id
        """), {"id": policy["id"]}).scalar_one()

    third = run_cycle(engine, http=fake_http(), llm=FakeLLM(), searcher=FakeSearch(down=True))
    assert third["posted"] == 0

    with engine.begin() as connection:
        connection.execute(text("UPDATE timeline_entries SET added_at = now() - interval '25 hours'"))
    publish(engine, [], 24)
    assert client.get("/v1/timeline").json()["items"] == []


def test_genres_without_weight_are_not_searched(client, engine, monkeypatch):
    monkeypatch.setattr(run_module, "CANDIDATES", 4)
    off = create(client, "政策", 0)
    search = FakeSearch()
    result = run_cycle(engine, http=fake_http(), llm=FakeLLM(), searcher=search)
    assert search.queries == [] and result["found"] == 0 and off["id"] not in result["genres"]
