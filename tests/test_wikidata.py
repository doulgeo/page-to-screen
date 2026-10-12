import asyncio
import json

import httpx
import pytest

from page_to_screen import wikidata as wd
from page_to_screen.config import Settings


def run(coro):
    return asyncio.run(coro)


def fetch(wikidata):
    async def go():
        async with wikidata.client() as client:
            return await wd.fetch_films(client)

    return {film["id"]: film for film in run(go())}


def test_fetch_builds_one_record_per_imdb_id(wikidata):
    films = fetch(wikidata)
    assert set(films) == {
        "tt0049934", "tt1781769", "tt0081505", "tt0057427", "tt0059749", "tt0040416",
    }
    assert films["tt0049934"] == {
        "id": "tt0049934",
        "name": "War and Peace",
        "year": 1956,
        "popularity": 60,
        "authors": ["Leo Tolstoy"],
        "works": [{"title": "War and Peace", "year": 1869}],
    }


def test_label_fallbacks_and_inception_date(wikidata):
    films = fetch(wikidata)
    # No English label: falls back by language priority (fr before de).
    assert films["tt0057427"]["name"] == "Le Procès"
    # Unknown publication date: inception (P571) is used instead.
    assert films["tt0040416"]["works"] == [{"title": "Hamlet", "year": 1599}]
    assert films["tt0040416"]["authors"] == ["William Shakespeare"]
    assert films["tt0040416"]["year"] is None


def test_sends_descriptive_user_agent(wikidata):
    settings = Settings(contact="me@example.org")

    async def go():
        async with wikidata.client(headers={"User-Agent": settings.user_agent}) as client:
            await wd.fetch_films(client)

    run(go())
    assert wikidata.requests
    for request in wikidata.requests:
        assert request.url == wd.SPARQL_ENDPOINT
        assert request.headers["User-Agent"].startswith("PageToScreen/")
        assert "me@example.org" in request.headers["User-Agent"]


def test_structure_query_shape():
    for fragment in ("wdt:P144", "wdt:P50", "wdt:P345", "wdt:P577", "wdt:P31/wdt:P279* wd:Q11424"):
        assert fragment in wd.STRUCTURE_QUERY


@pytest.mark.parametrize(
    "value, year",
    [
        ("1869-01-01T00:00:00Z", 1869),
        ("-0057-01-01T00:00:00Z", -57),
        ("http://www.wikidata.org/.well-known/genid/abc", None),
        ("", None),
    ],
)
def test_parse_year(value, year):
    assert wd.parse_year(value) == year


def test_retries_transient_errors_and_truncated_json(wikidata):
    responses = iter(
        [
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(200, content='{"results": {"bindings": [ {"a"'),
            httpx.Response(200, content=json.dumps({"results": {"bindings": [{"x": 1}]}})),
        ]
    )
    wikidata.handler = lambda request: next(responses)

    async def go():
        async with wikidata.client() as client:
            return await wd.run_query(client, "SELECT 1", retry_delay=0)

    assert run(go()) == [{"x": 1}]
    assert len(wikidata.requests) == 3


def test_gives_up_after_repeated_failures(wikidata):
    wikidata.handler = lambda request: httpx.Response(504, content="upstream request timeout")

    async def go():
        async with wikidata.client() as client:
            return await wd.run_query(client, "SELECT 1", retry_delay=0)

    with pytest.raises(wd.WikidataError):
        run(go())
    assert len(wikidata.requests) == wd.MAX_ATTEMPTS
