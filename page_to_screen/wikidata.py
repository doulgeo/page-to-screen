"""Wikidata SPARQL client: fetches book-to-film adaptations.

One query asking for everything (films, works, authors and all their labels)
sits right at the endpoint's 60s limit and times out intermittently. So the
fetch is split: one structure query returning only IDs and dates, then small
batched lookups for labels, which are fast and predictable.
"""

import asyncio
import json
import logging
import re

import httpx

from .config import CURATED_AUTHORS, SPARQL_ENDPOINT, Settings

log = logging.getLogger(__name__)

# Films (including subclasses of film) based on a work with a known author.
STRUCTURE_QUERY = """
SELECT ?film ?imdb ?work ?author ?sitelinks
       (MIN(?wd) AS ?workDate) (MIN(?fd) AS ?filmDate)
WHERE {
  ?film wdt:P144 ?work .
  ?work wdt:P50 ?author .
  ?film wdt:P345 ?imdb .
  ?film wdt:P31/wdt:P279* wd:Q11424 .
  ?film wikibase:sitelinks ?sitelinks .
  OPTIONAL { ?work wdt:P577 ?wd . }
  OPTIONAL { ?film wdt:P577 ?fd . }
}
GROUP BY ?film ?imdb ?work ?author ?sitelinks
"""

# Preferred first; non-English labels are a fallback so films aren't dropped.
LABEL_LANGUAGES = ("en", "mul", "fr", "de", "es", "it")

LABEL_QUERY = """
SELECT ?item ?label WHERE {
  VALUES ?item { %(items)s }
  ?item rdfs:label ?label .
  FILTER(LANG(?label) IN (%(langs)s))
}
"""

# Many works (plays, folk tales) have no publication date but do have an inception.
INCEPTION_QUERY = """
SELECT ?item (MIN(?d) AS ?date) WHERE {
  VALUES ?item { %(items)s }
  ?item wdt:P571 ?d .
}
GROUP BY ?item
"""

BATCH_SIZE = 1000
# Wikimedia allows 5 parallel queries per client; stay well under it.
BATCH_CONCURRENCY = 3
MAX_ATTEMPTS = 4
_RETRY_STATUS = {429, 500, 502, 503, 504}

_IMDB_RE = re.compile(r"^tt\d+$")
_YEAR_RE = re.compile(r"^(-?\d+)-")
_QID_RE = re.compile(r"Q\d+$")


class WikidataError(RuntimeError):
    pass


async def run_query(client: httpx.AsyncClient, query: str, *, retry_delay: float = 5.0):
    """POST one SPARQL query and return its result bindings, retrying transient failures."""
    last_error: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        if attempt:
            await asyncio.sleep(delay)
        delay = retry_delay * (3**attempt)
        try:
            response = await client.post(
                SPARQL_ENDPOINT,
                data={"query": query},
                headers={"Accept": "application/sparql-results+json"},
            )
        except httpx.HTTPError as exc:
            last_error = exc
            continue
        if response.status_code in _RETRY_STATUS:
            last_error = WikidataError(f"HTTP {response.status_code} from Wikidata")
            retry_after = response.headers.get("Retry-After", "")
            if retry_after.isdigit():
                delay = min(float(retry_after), 120.0)
            continue
        if response.status_code != 200:
            raise WikidataError(
                f"HTTP {response.status_code} from Wikidata: {response.text[:200]}"
            )
        try:
            return response.json()["results"]["bindings"]
        except (json.JSONDecodeError, KeyError) as exc:
            # A query that times out mid-stream yields a 200 with truncated JSON.
            last_error = exc
    raise WikidataError(f"Wikidata query failed after {MAX_ATTEMPTS} attempts: {last_error!r}")


def _value(row: dict, key: str) -> str:
    return row.get(key, {}).get("value", "")


def _qid(uri: str) -> str | None:
    match = _QID_RE.search(uri)
    return match.group(0) if match else None


def parse_year(value: str) -> int | None:
    """Year from an xsd:dateTime ("1869-01-01T00:00:00Z", "-0057-..."), else None.

    Wikidata's "unknown value" comes back as a blank-node URI, which yields None.
    """
    match = _YEAR_RE.match(value)
    return int(match.group(1)) if match else None


async def _batched(client, template: str, qids, **params) -> list[dict]:
    qids = sorted(qids)
    limit = asyncio.Semaphore(BATCH_CONCURRENCY)

    async def one(start: int) -> list[dict]:
        items = " ".join(f"wd:{q}" for q in qids[start : start + BATCH_SIZE])
        async with limit:
            return await run_query(client, template % {"items": items, **params})

    batches = await asyncio.gather(*(one(i) for i in range(0, len(qids), BATCH_SIZE)))
    return [row for batch in batches for row in batch]


async def fetch_labels(client, qids) -> dict[str, str]:
    langs = ", ".join(f'"{lang}"' for lang in LABEL_LANGUAGES)
    best: dict[str, tuple[int, str]] = {}
    for row in await _batched(client, LABEL_QUERY, qids, langs=langs):
        qid = _qid(_value(row, "item"))
        lang = row.get("label", {}).get("xml:lang")
        if qid is None or lang not in LABEL_LANGUAGES:
            continue
        rank = LABEL_LANGUAGES.index(lang)
        if qid not in best or rank < best[qid][0]:
            best[qid] = (rank, _value(row, "label"))
    return {qid: label for qid, (_, label) in best.items()}


async def fetch_inceptions(client, qids) -> dict[str, int]:
    years = {}
    for row in await _batched(client, INCEPTION_QUERY, qids):
        qid, year = _qid(_value(row, "item")), parse_year(_value(row, "date"))
        if qid and year is not None:
            years[qid] = year
    return years


def build_films(rows, labels: dict[str, str], inceptions: dict[str, int]) -> list[dict]:
    """Collapse (film, work, author) rows into one record per IMDb ID."""
    films: dict[str, dict] = {}
    for row in rows:
        imdb = _value(row, "imdb")
        film_qid, work_qid = _qid(_value(row, "film")), _qid(_value(row, "work"))
        author_qid = _qid(_value(row, "author"))
        if not _IMDB_RE.match(imdb) or not (film_qid and work_qid and author_qid):
            continue
        name = labels.get(film_qid)
        author = CURATED_AUTHORS.get(author_qid) or labels.get(author_qid)
        if not name or not author:
            continue
        try:
            popularity = int(_value(row, "sitelinks"))
        except ValueError:
            popularity = 0

        film = films.setdefault(
            imdb,
            {"id": imdb, "name": name, "year": None, "popularity": -1, "authors": [], "works": {}},
        )
        if popularity > film["popularity"]:
            film["popularity"], film["name"] = popularity, name
        year = parse_year(_value(row, "filmDate"))
        if year is not None and (film["year"] is None or year < film["year"]):
            film["year"] = year
        if author not in film["authors"]:
            film["authors"].append(author)
        work_year = parse_year(_value(row, "workDate"))
        if work_year is None:
            work_year = inceptions.get(work_qid)
        film["works"][work_qid] = {"title": labels.get(work_qid), "year": work_year}

    for film in films.values():
        film["works"] = list(film["works"].values())
    return list(films.values())


def make_client(settings: Settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": settings.user_agent},
        timeout=httpx.Timeout(90.0, connect=15.0),
    )


async def fetch_films(client: httpx.AsyncClient) -> list[dict]:
    """Run the full fetch against Wikidata. Takes a few minutes; call it rarely."""
    rows = await run_query(client, STRUCTURE_QUERY)
    log.info("Wikidata: %d adaptation rows, fetching labels", len(rows))

    qids, undated_works = set(), set()
    for row in rows:
        for key in ("film", "work", "author"):
            qid = _qid(_value(row, key))
            if qid:
                qids.add(qid)
        if parse_year(_value(row, "workDate")) is None:
            work_qid = _qid(_value(row, "work"))
            if work_qid:
                undated_works.add(work_qid)

    labels = await fetch_labels(client, qids)
    inceptions = await fetch_inceptions(client, undated_works)
    films = build_films(rows, labels, inceptions)
    log.info("Wikidata: built %d films", len(films))
    return films
