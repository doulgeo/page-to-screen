"""Holds the current catalog and keeps it fresh without blocking requests."""

import asyncio
import logging
import time
from typing import Awaitable, Callable

from .cache import Cache
from .catalog import Catalog

log = logging.getLogger(__name__)

CACHE_KEY = "films-v1"


class FilmStore:
    """Serves films from memory, backed by the SQLite cache.

    Stale data keeps being served while a refresh runs in the background, so
    Wikidata is contacted at most once per TTL and never on the request path.
    """

    def __init__(
        self,
        cache: Cache,
        fetcher: Callable[[], Awaitable[list[dict]]],
        *,
        ttl: float = 86400,
        sort: str = "popularity",
        retry_after: float = 600,
        clock: Callable[[], float] = time.time,
    ):
        self._cache = cache
        self._fetcher = fetcher
        self._ttl = ttl
        self._sort = sort
        self._retry_after = retry_after
        self._clock = clock
        self._task: asyncio.Task | None = None
        self._next_attempt = 0.0
        self.catalog: Catalog | None = None
        self.fetched_at: float | None = None

    def load_cached(self) -> bool:
        entry = self._cache.get(CACHE_KEY)
        if entry is None:
            return False
        films, self.fetched_at = entry
        self.catalog = Catalog(films, self._sort)
        return True

    @property
    def is_fresh(self) -> bool:
        return self.fetched_at is not None and self._clock() - self.fetched_at < self._ttl

    @property
    def refreshing(self) -> bool:
        return self._task is not None and not self._task.done()

    async def refresh(self) -> None:
        films = await self._fetcher()
        if not films:
            raise RuntimeError("Wikidata returned no films; keeping the previous snapshot")
        self.fetched_at = self._cache.set(CACHE_KEY, films, self._clock())
        self.catalog = Catalog(films, self._sort)

    async def _refresh_logged(self) -> None:
        try:
            await self.refresh()
            log.info("Catalog refreshed: %d films", len(self.catalog))
        except Exception:
            self._next_attempt = self._clock() + self._retry_after
            log.exception("Catalog refresh failed; retrying in %.0fs", self._retry_after)

    def ensure_fresh(self) -> None:
        """Start a background refresh if the snapshot is missing or expired."""
        if self.is_fresh or self.refreshing or self._clock() < self._next_attempt:
            return
        self._task = asyncio.create_task(self._refresh_logged())

    async def get_catalog(self, wait: float = 0) -> Catalog | None:
        """Current catalog, possibly stale. None only before the very first fetch lands."""
        self.ensure_fresh()
        if self.catalog is None and self._task is not None and wait > 0:
            await asyncio.wait({self._task}, timeout=wait)
        return self.catalog

    async def aclose(self) -> None:
        if self.refreshing:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
