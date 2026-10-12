"""CLI: `python -m page_to_screen [serve|refresh|sample]`."""

import argparse
import asyncio
import logging

from .config import Settings


def serve(settings: Settings) -> None:
    import uvicorn

    uvicorn.run("page_to_screen.asgi:app", host=settings.host, port=settings.port)


def refresh(settings: Settings) -> None:
    """Fetch from Wikidata and fill the cache (use it to pre-warm a deploy)."""
    from .app import build_store

    store = build_store(settings)
    asyncio.run(store.refresh())
    print(f"Cached {len(store.catalog)} films in {settings.cache_path}")


def sample(settings: Settings) -> None:
    """Print the top of each catalog from the cache, fetching first if it is empty."""
    from .app import build_store
    from .catalog import AUTHORS_CATALOG, CLASSICS_CATALOG

    store = build_store(settings)
    if not store.load_cached():
        asyncio.run(store.refresh())
    for title, catalog_id, extra in [
        ("Most popular adaptations", AUTHORS_CATALOG, {}),
        ("Leo Tolstoy", AUTHORS_CATALOG, {"genre": "Leo Tolstoy"}),
        ("Classics (source published before 1929)", CLASSICS_CATALOG, {}),
    ]:
        print(f"\n{title}")
        for meta in store.catalog.query(catalog_id, extra)[:10]:
            print(f"  {meta['id']:<11} {meta['name']} ({meta.get('releaseInfo', '?')}) - {meta['description']}")
    print(f"\n{len(store.catalog)} films total, {len(store.catalog.classics)} classics")


def main() -> None:
    parser = argparse.ArgumentParser(prog="page_to_screen", description=__doc__)
    parser.add_argument("command", nargs="?", default="serve", choices=["serve", "refresh", "sample"])
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    {"serve": serve, "refresh": refresh, "sample": sample}[args.command](Settings())


if __name__ == "__main__":
    main()
