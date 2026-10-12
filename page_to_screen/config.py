"""Settings, read from the environment once at import time."""

import os
from dataclasses import dataclass, field

from . import __version__

SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
POSTER_URL = "https://images.metahub.space/poster/medium/{imdb_id}/img"

PAGE_SIZE = 100
CLASSIC_BEFORE_YEAR = 1929

# Wikidata QID -> name shown in Stremio's genre dropdown. Keyed on QID so the
# list survives label changes (Wikidata's English label is "Fyodor Dostoyevsky").
CURATED_AUTHORS = {
    "Q991": "Fyodor Dostoevsky",
    "Q7243": "Leo Tolstoy",
    "Q214622": "Nikos Kazantzakis",
    "Q36322": "Jane Austen",
    "Q5686": "Charles Dickens",
    "Q905": "Franz Kafka",
    "Q36591": "Vladimir Nabokov",
    "Q209641": "John le Carré",
    "Q39829": "Stephen King",
    "Q270635": "Patricia Highsmith",
}
# After the curated names, the dropdown is topped up with the most-adapted authors.
EXTRA_AUTHOR_OPTIONS = 40


@dataclass(frozen=True)
class Settings:
    host: str = field(default_factory=lambda: os.getenv("HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(os.getenv("PORT", "7000")))
    cache_path: str = field(
        default_factory=lambda: os.getenv("P2S_CACHE_PATH", "data/cache.sqlite3")
    )
    ttl_seconds: float = field(
        default_factory=lambda: float(os.getenv("P2S_TTL_HOURS", "24")) * 3600
    )
    # "popularity" (Wikipedia sitelink count) or "year" (newest first)
    sort: str = field(default_factory=lambda: os.getenv("P2S_SORT", "popularity"))
    # Wikimedia's User-Agent policy asks for a way to contact the operator.
    contact: str = field(
        default_factory=lambda: os.getenv("P2S_CONTACT", "contact not set; see P2S_CONTACT")
    )

    @property
    def user_agent(self) -> str:
        return f"PageToScreen/{__version__} (Stremio catalog addon; {self.contact})"
