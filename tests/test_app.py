import asyncio

import pytest
from fastapi.testclient import TestClient

from page_to_screen.app import create_app
from page_to_screen.cache import Cache
from page_to_screen.store import CACHE_KEY, FilmStore


@pytest.fixture
def client(make_store):
    store = make_store()
    asyncio.run(store.refresh())
    with TestClient(create_app(store=store)) as client:
        yield client


def names(response):
    assert response.status_code == 200
    return [meta["name"] for meta in response.json()["metas"]]


def test_manifest(client):
    manifest = client.get("/manifest.json").json()
    assert manifest["name"] == "Page to Screen"
    assert manifest["resources"] == ["catalog"]
    assert manifest["types"] == ["movie"]
    by_author, classics = manifest["catalogs"]
    assert by_author["name"] == "Adaptations by author"
    assert classics["name"] == "Classic literature adaptations"
    extras = {extra["name"]: extra for extra in by_author["extra"]}
    assert set(extras) == {"genre", "search", "skip"}
    assert not extras["genre"]["isRequired"]
    assert {"Leo Tolstoy", "Stephen King", "Franz Kafka", "John le Carré"} <= set(extras["genre"]["options"])


def test_cors_allows_any_origin(client):
    response = client.get("/manifest.json", headers={"Origin": "https://web.stremio.com"})
    assert response.headers["access-control-allow-origin"] == "*"


def test_catalog_default_sorted_by_popularity(client):
    response = client.get("/catalog/movie/p2s-authors.json")
    assert names(response)[:2] == ["The Shining", "War and Peace"]
    meta = response.json()["metas"][0]
    assert meta["id"] == "tt0081505"
    assert meta["poster"] == "https://images.metahub.space/poster/medium/tt0081505/img"
    assert "max-age" in response.headers["cache-control"]


def test_catalog_genre_and_skip_extra(client):
    assert names(client.get("/catalog/movie/p2s-authors/genre=Leo%20Tolstoy.json")) == [
        "War and Peace", "Anna Karenina",
    ]
    assert names(client.get("/catalog/movie/p2s-authors/genre=Leo%20Tolstoy&skip=1.json")) == [
        "Anna Karenina"
    ]
    assert names(client.get("/catalog/movie/p2s-authors/genre=Leo%20Tolstoy&skip=100.json")) == []


def test_catalog_search_extra(client):
    assert names(client.get("/catalog/movie/p2s-authors/search=le%20carr%C3%A9.json")) == [
        "The Spy Who Came In from the Cold"
    ]
    assert names(client.get("/catalog/movie/p2s-authors/search=anna.json")) == ["Anna Karenina"]


def test_classics_catalog(client):
    assert names(client.get("/catalog/movie/p2s-classics.json")) == [
        "War and Peace", "Anna Karenina", "Hamlet", "Le Procès",
    ]


def test_unknown_catalog_is_404(client):
    assert client.get("/catalog/movie/nope.json").status_code == 404
    assert client.get("/catalog/series/p2s-authors.json").status_code == 404


def test_requests_are_served_from_cache_not_wikidata(client, wikidata):
    before = len(wikidata.requests)
    for _ in range(3):
        client.get("/catalog/movie/p2s-authors.json")
        client.get("/manifest.json")
    assert len(wikidata.requests) == before


def test_cache_survives_restart(make_store, wikidata):
    asyncio.run(make_store().refresh())
    before = len(wikidata.requests)
    with TestClient(create_app(store=make_store())) as client:
        assert len(names(client.get("/catalog/movie/p2s-authors.json"))) == 6
    assert len(wikidata.requests) == before


def test_stale_cache_is_served_while_refreshing(tmp_path):
    cache = Cache(str(tmp_path / "c.sqlite3"))
    old = {"id": "tt1", "name": "Old", "year": 1990, "popularity": 1, "authors": ["A"], "works": []}
    new = dict(old, name="New")
    cache.set(CACHE_KEY, [old], fetched_at=0)  # long expired
    release = asyncio.Event()
    calls = []

    async def fetcher():
        calls.append(1)
        await release.wait()
        return [new]

    async def scenario():
        store = FilmStore(cache, fetcher, ttl=86400)
        store.load_cached()
        stale = await store.get_catalog()
        assert [f["name"] for f in stale.films] == ["Old"] and store.refreshing
        await store.get_catalog()  # does not start a second fetch
        release.set()
        await store._task
        assert [f["name"] for f in (await store.get_catalog()).films] == ["New"]
        assert store.is_fresh and len(calls) == 1

    asyncio.run(scenario())


def test_cold_start_answers_empty_without_caching(tmp_path, monkeypatch):
    monkeypatch.setattr("page_to_screen.app.COLD_START_WAIT", 0.05)

    async def fetcher():
        await asyncio.sleep(3600)

    store = FilmStore(Cache(str(tmp_path / "c.sqlite3")), fetcher)
    with TestClient(create_app(store=store)) as client:
        response = client.get("/catalog/movie/p2s-authors.json")
        assert response.json() == {"metas": []}
        assert response.headers["cache-control"] == "no-store"
        assert client.get("/manifest.json").status_code == 200


def test_failed_refresh_keeps_old_data_and_backs_off(tmp_path):
    cache = Cache(str(tmp_path / "c.sqlite3"))
    cache.set(CACHE_KEY, [{"id": "tt1", "name": "Old", "year": None, "popularity": 1, "authors": ["A"], "works": []}], fetched_at=0)
    calls = []

    async def fetcher():
        calls.append(1)
        raise RuntimeError("wikidata down")

    async def scenario():
        store = FilmStore(cache, fetcher)
        store.load_cached()
        await store.get_catalog()
        await store._task
        assert len((await store.get_catalog()).films) == 1
        assert not store.refreshing and len(calls) == 1

    asyncio.run(scenario())
