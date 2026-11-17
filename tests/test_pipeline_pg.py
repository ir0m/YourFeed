import json
import re
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from yourfeed.db import WORKER_LOCK, get_engine
from yourfeed.fetch import SafeHttpClient
from yourfeed.llm import LLMError, LLMResult
from yourfeed.main import app
from yourfeed.pipeline import run as run_module
from yourfeed.pipeline.run import close_interrupted, forget_old, publish, run_cycle
from yourfeed.search.searxng import Result, SearchError

NOW = datetime.now(timezone.utc)
SAME_ORIGIN = {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}


class FakeSearch:
    """Three results per page and two pages per query and time range."""

    def __init__(self, down=False):
        self.down = down
        self.queries = []
        self.languages = set()

    def search(self, query, *, page=1, categories="general", time_range=None, language="all"):
        self.languages.add(language)
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
        window = time_range or categories
        results = [Result(f"https://{host}/{slug}/{window}/{page}/{index}", f"{host} {slug} {page}-{index}",
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
    if request.url.host == "deep.example":
        published = ""
    if request.url.host == "en.example":
        body = "".join(f"<p>Part {n}: the agency announced a new plan to land on the Moon, at a cost of 300 "
                       f"billion yen.</p>" for n in range(6))
        return httpx.Response(200, content=f"<html lang='en'><body><article>{body}</article></body></html>".encode(),
                              headers={"Content-Type": "text/html; charset=utf-8"})
    if request.url.host == "cn.example":
        body = "<p>国家航天局宣布了新的月球探测计划，预计费用为三百亿元。</p>" * 12
        return httpx.Response(200, content=f"<html lang='zh-CN'><body><article>{body}</article></body></html>".encode(),
                              headers={"Content-Type": "text/html; charset=utf-8"})
    if request.url.host == "gov.example":
        published = {"/n/news/1/0": "2026-01-01", "/n/news/1/1": ""}.get(request.url.path, published)
    return httpx.Response(200, content=page(f"記事{request.url.path}", published),
                          headers={"Content-Type": "text/html; charset=utf-8"})


def fake_http():
    return SafeHttpClient(httpx.Client(transport=httpx.MockTransport(handler)),
                          resolver=lambda host: ["93.184.216.34"], allow_private=False)


class FakeLLM:
    provider, model = "fake", "fake-1"

    def __init__(self):
        self.calls = 0
        self.topic_calls = 0
        self.translate_calls = 0

    def complete(self, system, messages, schema):
        if "translations" in schema["properties"]:
            self.translate_calls += 1
            asked = json.loads(messages[0]["content"])
            mark = lambda words, language: [f"{word}-{language}" for word in words]  # noqa: E731
            return LLMResult(json.dumps({"translations": [
                {"language": language, "name": f"{asked['name']}-{language}",
                 "focus_terms": mark(asked["focus_terms"], language),
                 "exclude_terms": mark(asked["exclude_terms"], language)} for language in asked["languages"]]}))
        if "topics" in schema["properties"]:
            self.topic_calls += 1
            return LLMResult(json.dumps({"topics": ["広告 入門"] + [f"stoa {n}" for n in range(30)]}), 50, 10)
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
    # Providers to switch to: one listed twice, or the same as the main one, is dropped; four at most.
    saved = client.put("/v1/settings", json={**body, "llm_provider": "claude_cli", "llm_model": "", "llm_fallbacks": [
        {"provider": "codex_cli"}, {"provider": "claude_cli", "model": ""}, {"provider": "codex_cli", "model": " "}]},
        headers=SAME_ORIGIN).json()
    assert (saved["llm_provider"], saved["llm_fallbacks"]) == ("claude_cli", [{"provider": "codex_cli", "model": ""}])
    assert client.put("/v1/settings", json={**body, "llm_fallbacks": [{"provider": "openai", "model": str(n)}
                                                                      for n in range(5)]},
                      headers=SAME_ORIGIN).status_code == 422
    # The display period covers the interval, or the timeline would be empty until the next update.
    assert client.put("/v1/settings", json={**body, "interval_minutes": 1440, "retention_hours": 12},
                      headers=SAME_ORIGIN).status_code == 422
    assert client.put("/v1/settings", json={**body, "interval_minutes": 1440, "retention_hours": 24},
                      headers=SAME_ORIGIN).status_code == 200
    assert client.put("/v1/settings", json=body, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/v1/timeline", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.get("/v1/settings", headers={"Host": "evil.example"}).status_code == 400

    only_name = client.post("/v1/genres", json={"name": "宇宙"}, headers=SAME_ORIGIN)
    assert only_name.status_code == 201 and only_name.json()["locales"] == ["ja-JP"]
    assert only_name.json()["translate"] is True
    for bad in (["xx-XX"], []):
        assert client.post("/v1/genres", json={"name": "x", "locales": bad}, headers=SAME_ORIGIN).status_code == 422
    mixed = client.post("/v1/genres", json={"name": "y", "locales": ["en", "all", "en"]}, headers=SAME_ORIGIN)
    assert mixed.json()["locales"] == ["all"]
    client.delete(f"/v1/genres/{mixed.json()['id']}", headers=SAME_ORIGIN)
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

    class Watching(FakeLLM):
        posted_before_call = []

        def complete(self, system, messages, schema):
            with engine.connect() as connection:
                self.posted_before_call.append(
                    connection.execute(text("SELECT count(*) FROM timeline_entries")).scalar_one())
            return super().complete(system, messages, schema)

    search, llm = FakeSearch(), Watching()
    result = run_cycle(engine, http=fake_http(), llm=llm, searcher=search)
    # Each post appears as soon as its summary is ready.
    assert llm.posted_before_call == [0, 0, 1, 2, 3, 4, 5]
    # 空 finds nothing, so its share of the 12 candidates goes to 科学 and 政策 by weight.
    assert result["found"] == 12 and result["search_error"] is None
    assert {key: value["found"] for key, value in result["genres"].items()} == {
        science["id"]: 9, policy["id"]: 3, next(key for key in result["genres"]
                                                  if key not in (science["id"], policy["id"])): 0}
    assert search.queries[0] == ("科学 量子 -人事 site:lab.example", 1, "news", None)
    assert search.languages == {"ja-JP"}
    with engine.connect() as connection:
        rows = connection.execute(text("""
            SELECT i.url, i.title FROM items i JOIN genres g ON g.id = i.genre_id
            WHERE g.name = '科学' ORDER BY i.rank
        """)).all()
    assert [url for url, _ in rows[:3]] == [f"https://lab.example/q/news/1/{index}" for index in range(3)]
    assert not any("人事" in title for _, title in rows)

    # 空's share goes to 科学 and 政策 by weight. 政策's page older than a week is not used, and its
    # undated page waits until the dated ones are used.
    assert (result["target"], result["posted"]) == (6, 6)
    assert llm.calls == 7
    page = client.get("/v1/timeline").json()
    assert len(page["items"]) == 6 and page["updating"] is False
    with engine.connect() as connection:
        connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": WORKER_LOCK})
        assert client.get("/v1/timeline").json()["updating"] is True
        connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": WORKER_LOCK})
    with engine.connect() as connection:
        posted = connection.execute(text("""
            SELECT g.name, i.url FROM timeline_entries e JOIN items i ON i.id = e.item_id
            JOIN genres g ON g.id = e.genre_id ORDER BY e.added_at DESC
        """)).all()
        run = connection.execute(text("SELECT status, target, posted, found, llm_calls, input_tokens FROM runs")).one()
    assert sorted(name for name, _ in posted) == ["政策", "政策", "科学", "科学", "科学", "科学"]
    assert [url for name, url in posted if name == "政策"] == ["https://gov.example/n/news/1/1",
                                                               "https://gov.example/n/news/1/2"]
    assert run == ("ok", 6, 6, 12, 7, 600)
    detail = client.get(f"/v1/timeline/{page['items'][0]['id']}").json()
    assert detail["source"] == "Example" and detail["original_title"].startswith("記事/")
    assert page["items"][0]["body"] == detail["facts"] + detail["interpretation"]

    # With search down, the update still posts from earlier candidates; the old page is never posted.
    again = run_cycle(engine, http=fake_http(), llm=FakeLLM(), searcher=FakeSearch(down=True))
    assert (again["found"], again["search_error"], again["posted"]) == (0, "search_unavailable", 5)
    status = client.get("/v1/settings").json()["last_run"]
    assert (status["found"], status["found_target"], status["search_error"]) == (0, 12, "search_unavailable")
    with engine.connect() as connection:
        assert dict(connection.execute(text("""
            SELECT url, skip_reason FROM items WHERE genre_id = :id AND posted_at IS NULL
        """), {"id": policy["id"]}).all()) == {"https://gov.example/n/news/1/0": "too_old"}

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


def test_runs_stopped_midway_are_closed(engine):
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO runs (target) VALUES (5)"))
    with engine.connect() as lock:
        lock.execute(text("SELECT pg_advisory_lock(:key)"), {"key": WORKER_LOCK})
        close_interrupted(engine)  # an update is running, so nothing changes
        lock.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": WORKER_LOCK})
    with engine.connect() as connection:
        assert connection.execute(text("SELECT status FROM runs")).scalar_one() == "running"
    close_interrupted(engine)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT status, error FROM runs")).one() == ("error", "interrupted")


class DeepSearch:
    """Two results per page for general searches; nothing from other categories."""

    def __init__(self):
        self.queries = []

    def search(self, query, *, page=1, categories="general", time_range=None, language="all"):
        self.queries.append((query, page, categories, time_range))
        if categories != "general" or page > 3:
            return []
        slug = query.split()[1]
        return [Result(f"https://deep.example/{slug}/{page}/{index}", f"deep {slug} {page}-{index}", "説明")
                for index in range(2)]


def test_genres_of_any_age_search_model_topics_and_use_undated_pages(client, engine, monkeypatch):
    monkeypatch.setattr(run_module, "CANDIDATES", 6)
    client.put("/v1/settings", json=settings_body(client, items_per_run=3), headers=SAME_ORIGIN)
    space = client.post("/v1/genres", json=genre("宇宙"), headers=SAME_ORIGIN).json()
    assert space["latest"] is True
    client.delete(f"/v1/genres/{space['id']}", headers=SAME_ORIGIN)
    thought = create(client, "思想", 1, latest=False, exclude_terms=["広告"])
    assert thought["latest"] is False

    search, llm = DeepSearch(), FakeLLM()
    result = run_cycle(engine, http=fake_http(), llm=llm, searcher=search)
    assert llm.topic_calls == 1
    # One topic for each two candidates, so 6 candidates come from 3 topics.
    assert search.queries == [(f"stoa {n} -広告", 1, "general", None) for n in range(3)]
    # Undated pages are used at once; the topic call counts as a model call.
    assert (result["found"], result["posted"], result["llm_calls"]) == (6, 3, 5)
    with engine.connect() as connection:
        topics, index = connection.execute(text("SELECT topics, topic_index FROM genres")).one()
    assert (topics[0], len(topics), index) == ("stoa 0", 30, 3)

    search, llm = DeepSearch(), FakeLLM()
    run_cycle(engine, http=fake_http(), llm=llm, searcher=search)
    assert llm.topic_calls == 0 and search.queries[0][0] == "stoa 3 -広告"

    # Switching back to the latest news drops the topics and the unposted candidates.
    client.put(f"/v1/genres/{thought['id']}", json=genre("思想", exclude_terms=["広告"]), headers=SAME_ORIGIN)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT topics, topic_index FROM genres")).one() == ([], 0)
        assert connection.execute(text("SELECT count(*) FROM items WHERE posted_at IS NULL")).scalar_one() == 0


class MixedSearch:
    """Chinese and Japanese pages in turn."""

    def search(self, query, *, page=1, categories="general", time_range=None, language="all"):
        if page > 1:
            return []
        return [Result(f"https://{host}/{index}", f"{host} {index}", "") for index in range(2)
                for host in ("cn.example", "jp.example")]


def test_pages_in_other_languages_are_skipped_before_the_model(client, engine, monkeypatch):
    monkeypatch.setattr(run_module, "CANDIDATES", 4)
    client.put("/v1/settings", json=settings_body(client, items_per_run=2), headers=SAME_ORIGIN)
    create(client, "宇宙", 1)
    llm = FakeLLM()
    result = run_cycle(engine, http=fake_http(), llm=llm, searcher=MixedSearch())
    assert (result["posted"], llm.calls) == (2, 3)
    with engine.connect() as connection:
        rows = connection.execute(text("SELECT url, skip_reason, posted_at IS NOT NULL FROM items ORDER BY url")).all()
    assert [(url, reason, posted) for url, reason, posted in rows] == [
        ("https://cn.example/0", "other_language", False), ("https://cn.example/1", "other_language", False),
        ("https://jp.example/0", None, True), ("https://jp.example/1", None, True)]


class EnglishSearch:
    def search(self, query, *, page=1, categories="general", time_range=None, language="all"):
        if page > 1:
            return []
        return [Result(f"https://{host}/{index}", f"{host} {index}", "") for index in range(2)
                for host in ("en.example", "jp.example")]


def test_untranslated_summaries_use_the_page_language(client, engine, monkeypatch):
    monkeypatch.setattr(run_module, "CANDIDATES", 4)
    client.put("/v1/settings", json=settings_body(client, items_per_run=2), headers=SAME_ORIGIN)
    create(client, "space", 1, locales=["en"], translate=False)

    class English:
        provider, model = "fake", "fake-1"
        systems = []

        def complete(self, system, messages, schema):
            if "translations" in schema["properties"]:
                return LLMResult(json.dumps({"translations": []}))  # keeps the written words
            self.systems.append(system)
            return LLMResult(json.dumps({"skip": False, "title": "Moon plan", "facts": "a" * 160,
                                         "interpretation": "b" * 60}))

    llm = English()
    result = run_cycle(engine, http=fake_http(), llm=llm, searcher=EnglishSearch())
    # Japanese pages are not in the chosen language; English ones are summarized in English,
    # with 60-120 characters scaled to 150-300.
    assert (result["posted"], len(llm.systems)) == (2, 2)
    assert "すべて英語で書きます" in llm.systems[0] and "合計を150〜300字" in llm.systems[0]


class RecordingSearch:
    def __init__(self):
        self.calls = []

    def search(self, query, *, page=1, categories="general", time_range=None, language="all"):
        self.calls.append((query, language))
        return []


def test_names_and_terms_are_translated_once_for_each_language(client, engine):
    space = create(client, "宇宙", 1, locales=["ja-JP", "en"], focus_terms=["探査機"], exclude_terms=["広告"])
    search, llm = RecordingSearch(), FakeLLM()
    run_cycle(engine, http=fake_http(), llm=llm, searcher=search)
    assert llm.translate_calls == 1
    # Japanese is translated too (a no-op for Japanese words), so each language has its own terms.
    assert ("宇宙-ja 探査機-ja -広告-ja", "ja-JP") in search.calls and ("宇宙-en 探査機-en -広告-en", "en") in search.calls
    with engine.connect() as connection:
        assert set(connection.execute(text("SELECT translations FROM genres")).scalar_one()) == {"ja", "en"}

    llm = FakeLLM()
    run_cycle(engine, http=fake_http(), llm=llm, searcher=RecordingSearch())
    assert llm.translate_calls == 0

    client.put(f"/v1/genres/{space['id']}", json=genre("宇宙", locales=["ja-JP", "en"]), headers=SAME_ORIGIN)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT translations FROM genres")).scalar_one() == {}


class PagedSearch:
    """One page per query: an old result, one from a host that cannot be read, and four usable ones."""

    def __init__(self):
        self.queries = []

    def search(self, query, *, page=1, categories="general", time_range=None, language="all"):
        self.queries.append((query, page, categories, time_range))
        if page > 1:
            return []
        return [Result("https://old.example/a", "old", "", NOW - timedelta(days=40)),
                Result("https://bad.example/a", "bad", ""), Result("https://new.example/a", "a", "", NOW),
                Result("https://new.example/b", "undated", ""), Result("https://mixed.example/a", "mixed", ""),
                Result("https://new.example/c", "c", "", NOW)]


def test_results_wait_for_their_turn_and_old_or_unreadable_ones_are_dropped(client, engine):
    space = create(client, "宇宙", 1)
    with engine.begin() as connection:
        for host, reasons in (("bad.example", ["http_403", "host_blocked", "too_little_text", "robots_disallow"]),
                              ("mixed.example", ["too_little_text"] * 3 + [None])):
            for index, reason in enumerate(reasons):
                connection.execute(text("""
                    INSERT INTO items (genre_id, url, title, attempted_at, skip_reason)
                    VALUES (:genre, :url, 't', now(), :reason)
                """), {"genre": space["id"], "url": f"https://{host}/old/{index}", "reason": reason})
        genre = dict(connection.execute(text("SELECT * FROM genres")).mappings().one())
    search = PagedSearch()
    collector = run_module.Collector(engine, search, 1, NOW)
    collector.collect([genre], 4)
    # The page is searched once; its results are taken two at a time.
    assert search.queries == [("宇宙", 1, "news", None)]
    with engine.connect() as connection:
        urls = connection.execute(text("SELECT url FROM items WHERE run_id = 1 ORDER BY rank")).scalars().all()
    assert urls == ["https://new.example/a", "https://new.example/b", "https://mixed.example/a",
                    "https://new.example/c"]


def test_a_page_with_nothing_new_ends_the_query(client, engine):
    space = create(client, "宇宙", 1)
    known = [f"https://known.example/{index}" for index in range(3)]
    with engine.begin() as connection:
        for url in known:  # found in an earlier update
            connection.execute(text("INSERT INTO items (genre_id, url, title) VALUES (:genre, :url, 't')"),
                               {"genre": space["id"], "url": url})
        genre = dict(connection.execute(text("SELECT * FROM genres")).mappings().one())

    class Known:
        queries = []

        def search(self, query, *, page=1, categories="general", time_range=None, language="all"):
            self.queries.append((page, categories))
            return [Result(f"{url}?p={page}" if page > 1 else url, "t", "") for url in known]

    search = Known()
    run_module.Collector(engine, search, 1, NOW).collect([genre], 4)
    # Each window's query is searched once, since its first page brought nothing new.
    assert search.queries == [(1, "news"), (1, "general"), (1, "science")]


def test_old_data_is_forgotten(client, engine):
    space = create(client, "宇宙", 1)
    day = timedelta(days=1)
    rows = {  # url: (found days ago, attempted days ago, skip reason, posted days ago)
        "candidate-new": (2, None, None, None), "candidate-old": (4, None, None, None),
        "retry-old": (4, 4, "temporary_error", None),
        "failed-new": (20, 10, "http_403", None), "failed-old": (20, 15, "http_403", None),
        "posted-new": (29, 29, None, 29), "posted-old": (31, 31, None, 31),
        "on-timeline": (40, 40, None, 40),
    }
    with engine.begin() as connection:
        for url, (found, attempted, reason, posted) in rows.items():
            item_id = connection.execute(text("""
                INSERT INTO items (genre_id, url, title, found_at, attempted_at, skip_reason, posted_at)
                VALUES (:genre, :url, 't', :found, :attempted, :reason, :posted) RETURNING id
            """), {"genre": space["id"], "url": f"https://a.example/{url}", "found": NOW - found * day,
                   "attempted": attempted and NOW - attempted * day, "reason": reason,
                   "posted": posted and NOW - posted * day}).scalar_one()
            if posted:
                connection.execute(text("""
                    INSERT INTO summaries (item_id, title, facts, provider, model) VALUES (:id, 't', 'f', 'p', 'm')
                """), {"id": item_id})
            if url == "on-timeline":
                connection.execute(text("""
                    INSERT INTO timeline_entries (item_id, genre_id, title, facts) VALUES (:id, :genre, 't', 'f')
                """), {"id": item_id, "genre": space["id"]})
        for started in (8, 6, 9):
            connection.execute(text("INSERT INTO runs (started_at, status) VALUES (:at, 'ok')"),
                               {"at": NOW - started * day})
    forget_old(engine, NOW)
    with engine.connect() as connection:
        left = set(connection.execute(text("SELECT split_part(url, '/', 4) FROM items")).scalars())
        summaries = set(connection.execute(text("""
            SELECT split_part(i.url, '/', 4) FROM summaries s JOIN items i ON i.id = s.item_id
        """)).scalars())
        runs = connection.execute(text("SELECT count(*) FROM runs")).scalar_one()
    assert left == {"candidate-new", "failed-new", "posted-new", "on-timeline"}
    # Posts off the timeline keep only their URL; the newest run stays however old it is.
    assert summaries == {"on-timeline"} and runs == 2


def test_a_provider_that_cannot_be_used_hands_over_to_the_next(client, engine, monkeypatch):
    monkeypatch.setattr(run_module, "CANDIDATES", 4)
    client.put("/v1/settings", json=settings_body(client, items_per_run=2), headers=SAME_ORIGIN)
    create(client, "宇宙", 1)

    class Limited(FakeLLM):
        provider, model = "claude_cli", "claude-opus-5-5"

        def complete(self, system, messages, schema):
            self.calls += 1
            raise LLMError("http_429")

    limited, fallback = Limited(), FakeLLM()
    result = run_cycle(engine, http=fake_http(), llm=[limited, fallback], searcher=MixedSearch())
    # The first page hits the limit, so it and the rest are summarized by the next provider.
    assert (result["posted"], limited.calls) == (2, 1)
    with engine.connect() as connection:
        providers = connection.execute(text("SELECT DISTINCT provider FROM summaries")).scalars().all()
        retried = connection.execute(text("""
            SELECT count(*) FROM items WHERE url LIKE 'https://jp.example/%' AND posted_at IS NOT NULL
        """)).scalar_one()
    assert providers == ["fake"] and retried == 2
    switches = [{"from": "claude_cli", "error": "http_429", "to": "fake"}]
    assert client.get("/v1/settings").json()["last_run"]["switches"] == switches

    # With none left, the update ends without summaries and says which providers it gave up on.
    class Newer:
        def search(self, query, *, page=1, categories="general", time_range=None, language="all"):
            return [Result(f"https://jp2.example/{index}", f"jp2 {index}", "") for index in range(2)] if page == 1 else []

    result = run_cycle(engine, http=fake_http(), llm=[Limited()], searcher=Newer())
    assert result["posted"] == 0
    assert client.get("/v1/settings").json()["last_run"]["switches"] == [
        {"from": "claude_cli", "error": "http_429", "to": None}]


class Writer(FakeLLM):
    """Writes persona posts, numbered so each one differs, and remembers what it was shown."""

    def __init__(self):
        super().__init__()
        self.post_calls = 0
        self.shown = []
        self.systems = []

    def complete(self, system, messages, schema):
        if "posts" not in schema["properties"]:
            return super().complete(system, messages, schema)
        self.post_calls += 1
        self.shown.append(messages[0]["content"])
        self.systems.append(system)
        asked = int(re.search(r"投稿を(\d+)件", system).group(1))
        start = self.post_calls * 100
        posts = [f"今日は散歩をして、川沿いの紅葉を眺めていた。{start + n}" for n in range(asked)]
        return LLMResult(json.dumps({"posts": ["短い", "https://x.example を見た。とても良かった。", *posts]},
                                    ensure_ascii=False), 30, 40)


def create_persona(client, name, weight, **extra):
    created = client.post("/v1/personas", json={"name": name, "min_chars": 10, "max_chars": 60, **extra},
                          headers=SAME_ORIGIN)
    assert created.status_code == 201
    assert client.patch(f"/v1/personas/{created.json()['id']}", json={"weight": weight},
                        headers=SAME_ORIGIN).status_code == 200
    return created.json()


def test_persona_api_validates(client):
    persona = client.post("/v1/personas", json={"name": " 山田 ", "profile": "東京の会社員"}, headers=SAME_ORIGIN)
    assert persona.status_code == 201
    body = persona.json()
    assert (body["name"], body["profile"], body["weight"], body["min_chars"], body["max_chars"]) == \
        ("山田", "東京の会社員", 10, 40, 140)
    assert client.post("/v1/personas", json={"name": "山田"}, headers=SAME_ORIGIN).status_code == 409
    assert client.post("/v1/personas", json={"name": "x", "min_chars": 50, "max_chars": 55},
                       headers=SAME_ORIGIN).status_code == 422
    assert client.post("/v1/personas", json={"name": "x", "profile": "あ" * 1001},
                       headers=SAME_ORIGIN).status_code == 422
    assert client.post("/v1/personas", json={"name": "宇宙"}, headers=SAME_ORIGIN).status_code == 201
    saved = client.put(f"/v1/personas/{body['id']}", json={"name": "山田", "topics": "料理", "tone": "丁寧語"},
                       headers=SAME_ORIGIN).json()
    assert (saved["topics"], saved["tone"], saved["profile"]) == ("料理", "丁寧語", "")
    rows = client.patch(f"/v1/personas/{body['id']}", json={"weight": 3}, headers=SAME_ORIGIN).json()["personas"]
    assert rows[0] == {"id": body["id"], "name": "山田", "weight": 3}
    assert [row["name"] for row in client.get("/v1/settings").json()["personas"]] == ["山田", "宇宙"]
    assert client.delete(f"/v1/personas/{body['id']}", headers=SAME_ORIGIN).status_code == 204
    assert client.get(f"/v1/personas/{body['id']}").status_code == 404
    assert client.put(f"/v1/personas/{body['id']}", json={"name": "山田"}, headers=SAME_ORIGIN).status_code == 404


def test_personas_share_the_posts_and_write_without_searching(client, engine, monkeypatch):
    monkeypatch.setattr(run_module, "CANDIDATES", 4)
    client.put("/v1/settings", json=settings_body(client, items_per_run=4), headers=SAME_ORIGIN)
    create(client, "宇宙", 1)
    persona = create_persona(client, "山田", 1, profile="東京の会社員", topics="散歩と料理", tone="です・ます調")
    create_persona(client, "休み", 0)
    llm = Writer()
    result = run_cycle(engine, http=fake_http(), llm=llm, searcher=MixedSearch())
    # Half the posts are the genre's summaries, half the persona's; its two posts come from one call.
    assert (result["posted"], result["target"]) == (4, 4)
    assert result["personas"] == {persona["id"]: {"posted": 2, "target": 2}}
    assert llm.post_calls == 1 and "東京の会社員" in llm.systems[0] and "です・ます調" in llm.systems[0]
    assert llm.shown == ["投稿を書いてください。"]
    items = client.get("/v1/timeline").json()["items"]
    posts = [item for item in items if item["kind"] == "post"]
    assert len(posts) == 2 and {post["author"] for post in posts} == {"山田"}
    assert all(post["body"].startswith("今日は散歩") and post["title"] == "" for post in posts)
    assert sum(item["kind"] == "article" for item in items) == 2
    detail = client.get(f"/v1/timeline/{posts[0]['id']}").json()
    assert (detail["kind"], detail["author"], detail["profile"], detail["body"]) == \
        ("post", "山田", "東京の会社員", posts[0]["body"])

    # With only personas, nothing is searched; earlier posts are shown so they are not repeated.
    client.patch(f"/v1/genres/{client.get('/v1/settings').json()['genres'][0]['id']}", json={"weight": 0},
                 headers=SAME_ORIGIN)
    search = FakeSearch()
    again = run_cycle(engine, http=fake_http(), llm=llm, searcher=search)
    assert (again["posted"], again["found"], search.queries) == (4, 0, [])
    assert client.get("/v1/settings").json()["last_run"]["found_target"] == 0
    assert all(post["body"] in llm.shown[1] for post in posts)

    # Deleting the persona removes its posts.
    client.delete(f"/v1/personas/{persona['id']}", headers=SAME_ORIGIN)
    assert [item["kind"] for item in client.get("/v1/timeline").json()["items"]] == ["article", "article"]
