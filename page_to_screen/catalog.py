"""In-memory catalog: filtering, search, sorting, pagination and Stremio meta shaping."""

import unicodedata
from collections import Counter
from urllib.parse import parse_qsl

from .config import (
    CLASSIC_BEFORE_YEAR,
    CURATED_AUTHORS,
    EXTRA_AUTHOR_OPTIONS,
    PAGE_SIZE,
    POSTER_URL,
)

AUTHORS_CATALOG = "p2s-authors"
CLASSICS_CATALOG = "p2s-classics"
CATALOG_NAMES = {
    AUTHORS_CATALOG: "Adaptations by author",
    CLASSICS_CATALOG: "Classic literature adaptations",
}


def normalize(text: str) -> str:
    """Casefold and strip accents, so "le carre" finds "John le Carré"."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold().strip()


def parse_extra(raw: str) -> dict[str, str]:
    """Parse Stremio's extra path segment, e.g. "genre=Leo%20Tolstoy&skip=100"."""
    return dict(parse_qsl(raw, keep_blank_values=True))


def is_classic(film: dict) -> bool:
    return any(
        work["year"] is not None and work["year"] < CLASSIC_BEFORE_YEAR for work in film["works"]
    )


def to_meta(film: dict) -> dict:
    meta = {
        "id": film["id"],
        "type": "movie",
        "name": film["name"],
        "poster": POSTER_URL.format(imdb_id=film["id"]),
        "posterShape": "poster",
    }
    if film["year"] is not None:
        meta["releaseInfo"] = str(film["year"])

    titles = []
    for work in film["works"]:
        if work["title"]:
            year = work["year"]
            era = f" ({abs(year)} BC)" if year is not None and year < 0 else f" ({year})" if year else ""
            titles.append(f"“{work['title']}”{era}")
    source = " and ".join(titles[:3]) or "a work"
    meta["description"] = f"Based on {source} by {', '.join(film['authors'])}."
    return meta


class Catalog:
    def __init__(self, films: list[dict], sort: str = "popularity"):
        if sort == "year":
            key = lambda f: (f["year"] is None, -(f["year"] or 0), -f["popularity"])
        else:
            key = lambda f: (-f["popularity"], f["name"])
        self.films = sorted(films, key=key)
        self.classics = [f for f in self.films if is_classic(f)]
        self._search_text = {
            f["id"]: normalize(" | ".join([f["name"], *f["authors"]])) for f in self.films
        }

    def __len__(self) -> int:
        return len(self.films)

    def _pool(self, catalog_id: str) -> list[dict]:
        return self.classics if catalog_id == CLASSICS_CATALOG else self.films

    def author_options(self, catalog_id: str) -> list[str]:
        """Curated authors first, then the most-adapted ones in this catalog."""
        counts = Counter(a for f in self._pool(catalog_id) for a in f["authors"])
        options = [name for name in CURATED_AUTHORS.values() if counts[name]]
        for name, _ in counts.most_common():
            if len(options) >= len(CURATED_AUTHORS) + EXTRA_AUTHOR_OPTIONS:
                break
            if name not in options:
                options.append(name)
        return options

    def query(self, catalog_id: str, extra: dict[str, str]) -> list[dict]:
        films = self._pool(catalog_id)

        genre = extra.get("genre")
        if genre:
            wanted = normalize(genre)
            films = [f for f in films if any(normalize(a) == wanted for a in f["authors"])]

        search = normalize(extra.get("search", ""))
        if search:
            films = [f for f in films if search in self._search_text[f["id"]]]

        try:
            skip = max(int(extra.get("skip", 0)), 0)
        except ValueError:
            skip = 0
        return [to_meta(f) for f in films[skip : skip + PAGE_SIZE]]


def default_author_options() -> list[str]:
    return list(CURATED_AUTHORS.values())
