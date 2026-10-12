"""FastAPI app implementing the Stremio addon protocol (manifest + catalog)."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import __version__
from .cache import Cache
from .catalog import (
    AUTHORS_CATALOG,
    CATALOG_NAMES,
    CLASSICS_CATALOG,
    default_author_options,
    parse_extra,
)
from .config import Settings
from .store import FilmStore
from .wikidata import fetch_films, make_client

# How long a request waits for the very first Wikidata fetch before answering empty.
COLD_START_WAIT = 20.0


def build_store(settings: Settings) -> FilmStore:
    async def fetcher():
        async with make_client(settings) as client:
            return await fetch_films(client)

    return FilmStore(
        Cache(settings.cache_path), fetcher, ttl=settings.ttl_seconds, sort=settings.sort
    )


def build_manifest(store: FilmStore) -> dict:
    def catalog(catalog_id: str, searchable: bool) -> dict:
        options = (
            store.catalog.author_options(catalog_id)
            if store.catalog
            else default_author_options()
        )
        extra = [{"name": "genre", "options": options, "isRequired": False}]
        if searchable:
            extra.append({"name": "search", "isRequired": False})
        extra.append({"name": "skip", "isRequired": False})
        return {"type": "movie", "id": catalog_id, "name": CATALOG_NAMES[catalog_id], "extra": extra}

    return {
        "id": "community.pagetoscreen",
        "version": __version__,
        "name": "Page to Screen",
        "description": "Films adapted from books, by author. Data from Wikidata.",
        "resources": ["catalog"],
        "types": ["movie"],
        "idPrefixes": ["tt"],
        # Search lives on one catalog only, so Stremio shows a single result row.
        "catalogs": [catalog(AUTHORS_CATALOG, True), catalog(CLASSICS_CATALOG, False)],
        "behaviorHints": {"configurable": False},
    }


def create_app(store: FilmStore | None = None, settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    store = store or build_store(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store.load_cached()
        store.ensure_fresh()
        yield
        await store.aclose()

    app = FastAPI(title="Page to Screen", version=__version__, lifespan=lifespan)
    app.state.store = store
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    @app.get("/")
    async def index(request: Request):
        return {
            "name": "Page to Screen",
            "manifest": str(request.url_for("manifest")),
            "films": len(store.catalog) if store.catalog else 0,
        }

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "films": len(store.catalog) if store.catalog else 0,
            "fetched_at": store.fetched_at,
            "refreshing": store.refreshing,
        }

    @app.get("/manifest.json", name="manifest")
    async def manifest():
        store.ensure_fresh()
        return build_manifest(store)

    async def serve_catalog(type: str, id: str, raw_extra: str = ""):
        if type != "movie" or id not in CATALOG_NAMES:
            raise HTTPException(status_code=404, detail="Unknown catalog")
        catalog = await store.get_catalog(wait=COLD_START_WAIT)
        if catalog is None:
            # First fetch still running: answer empty and tell clients not to cache it.
            return JSONResponse({"metas": []}, headers={"Cache-Control": "no-store"})
        return JSONResponse(
            {"metas": catalog.query(id, parse_extra(raw_extra))},
            headers={"Cache-Control": "public, max-age=3600"},
        )

    @app.get("/catalog/{type}/{id}.json")
    async def catalog_root(type: str, id: str):
        return await serve_catalog(type, id)

    @app.get("/catalog/{type}/{id}/{extra}.json")
    async def catalog_extra(type: str, id: str, extra: str, request: Request):
        # Parse from the raw path: the routed value is already percent-decoded,
        # which would break values containing "&" or "=".
        raw_path = request.scope.get("raw_path")
        if raw_path:
            segment = raw_path.decode("ascii", "ignore").split("?")[0].rsplit("/", 1)[-1]
            extra = segment.removesuffix(".json")
        return await serve_catalog(type, id, extra)

    return app

