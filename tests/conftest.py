import json

import httpx
import pytest

from page_to_screen.cache import Cache
from page_to_screen.store import FilmStore

WD = "http://www.wikidata.org/entity/"

LABELS = {
    "Q1": [("en", "War and Peace")],  # film
    "Q2": [("en", "Anna Karenina")],  # film
    "Q3": [("en", "The Shining")],  # film
    "Q4": [("de", "Der Prozess"), ("fr", "Le Procès")],  # film with no English label
    "Q5": [],  # film with no label at all -> dropped
    "Q6": [("en", "The Spy Who Came In from the Cold")],  # film
    "Q7": [("en", "Hamlet")],  # film, source work has only an inception date
    "Q101": [("en", "War and Peace")],
    "Q102": [("en", "Anna Karenina")],
    "Q103": [("en", "The Shining")],
    "Q104": [("en", "The Trial")],
    "Q106": [("en", "The Spy Who Came In from the Cold")],
    "Q107": [("en", "Hamlet")],
    "Q7243": [("en", "Leo Tolstoy")],
    "Q39829": [("en", "Stephen King")],
    "Q905": [("en", "Franz Kafka")],
    "Q209641": [("en", "John le Carré")],
    "Q692": [("mul", "William Shakespeare")],
}
INCEPTIONS = {"Q107": "1599-01-01T00:00:00Z"}


def _row(film, imdb, work, author, sitelinks, work_date=None, film_date=None):
    row = {
        "film": {"type": "uri", "value": WD + film},
        "imdb": {"type": "literal", "value": imdb},
        "work": {"type": "uri", "value": WD + work},
        "author": {"type": "uri", "value": WD + author},
        "sitelinks": {"type": "literal", "value": str(sitelinks)},
    }
    if work_date:
        row["workDate"] = {"type": "literal", "value": work_date}
    if film_date:
        row["filmDate"] = {"type": "literal", "value": film_date}
    return row


STRUCTURE_ROWS = [
    _row("Q1", "tt0049934", "Q101", "Q7243", 60, "1869-01-01T00:00:00Z", "1956-08-21T00:00:00Z"),
    _row("Q2", "tt1781769", "Q102", "Q7243", 50, "1878-01-01T00:00:00Z", "2012-09-07T00:00:00Z"),
    _row("Q3", "tt0081505", "Q103", "Q39829", 90, "1977-01-28T00:00:00Z", "1980-05-23T00:00:00Z"),
    _row("Q4", "tt0057427", "Q104", "Q905", 30, "1925-04-26T00:00:00Z", "1962-12-21T00:00:00Z"),
    _row("Q5", "tt9999999", "Q104", "Q905", 5, "1925-04-26T00:00:00Z"),
    _row("Q6", "tt0059749", "Q106", "Q209641", 40, "1963-09-01T00:00:00Z", "1965-12-16T00:00:00Z"),
    # "unknown value" publication date comes back as a blank-node URI
    _row("Q7", "tt0040416", "Q107", "Q692", 45, WD.replace("entity/", ".well-known/genid/abc")),
    # malformed IMDb ID -> dropped
    _row("Q1", WD + "genid", "Q101", "Q7243", 60),
]


def sparql_handler(request: httpx.Request) -> httpx.Response:
    """Fake query.wikidata.org: answers the structure, label and inception queries."""
    query = dict(httpx.QueryParams(request.content.decode()))["query"]
    qids = [token[3:] for token in query.split() if token.startswith("wd:Q")]
    if "rdfs:label" in query:
        bindings = [
            {
                "item": {"type": "uri", "value": WD + qid},
                "label": {"type": "literal", "xml:lang": lang, "value": text},
            }
            for qid in qids
            for lang, text in LABELS.get(qid, [])
        ]
    elif "wdt:P571" in query:
        bindings = [
            {"item": {"type": "uri", "value": WD + qid}, "date": {"type": "literal", "value": INCEPTIONS[qid]}}
            for qid in qids
            if qid in INCEPTIONS
        ]
    else:
        bindings = STRUCTURE_ROWS
    return httpx.Response(200, content=json.dumps({"results": {"bindings": bindings}}))


class FakeWikidata:
    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.handler = sparql_handler

    def __call__(self, request):
        self.requests.append(request)
        return self.handler(request)

    def client(self, **kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(self), **kwargs)


@pytest.fixture
def wikidata():
    return FakeWikidata()


@pytest.fixture
def make_store(wikidata, tmp_path):
    from page_to_screen.wikidata import fetch_films

    def factory(**kwargs):
        async def fetcher():
            async with wikidata.client() as client:
                return await fetch_films(client)

        return FilmStore(Cache(str(tmp_path / "cache.sqlite3")), fetcher, **kwargs)

    return factory
