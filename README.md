# Page to Screen

A [Stremio](https://www.stremio.com/) catalog addon listing films adapted from books, sourced
from [Wikidata](https://www.wikidata.org/). Python + FastAPI.

Catalogs (type `movie`):

| Catalog | ID | Extras |
|---|---|---|
| Adaptations by author | `p2s-authors` | `genre` (author), `search`, `skip` |
| Classic literature adaptations (source work published before 1929) | `p2s-classics` | `genre` (author), `skip` |

- The `genre` dropdown lists a curated set of authors first (Dostoevsky, Tolstoy, Kazantzakis,
  Austen, Dickens, Kafka, Nabokov, le Carré, King, Highsmith), then the 40 most-adapted authors.
  Any author name works as a `genre` value, listed or not.
- `search` matches film title or author name, ignoring case and accents.
- 100 items per page; default order is popularity (number of Wikipedia language editions).
- Items use IMDb IDs, so Stremio's Cinemeta supplies details and streams come from your other addons.

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

export P2S_CONTACT="you@example.com"   # goes in the User-Agent sent to Wikidata
python -m page_to_screen refresh       # one-off: fill the cache (takes a few minutes)
python -m page_to_screen               # serves http://127.0.0.1:7000
```

Then in Stremio: **Addons → Add addon →** `http://127.0.0.1:7000/manifest.json`.

`refresh` is optional. Without it the server fetches in the background on first start and the
catalogs are empty until that finishes. `python -m page_to_screen sample` prints the top of each
catalog as a sanity check.

Run the tests (Wikidata is mocked, no network needed):

```bash
pytest
```

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `HOST` / `PORT` | `127.0.0.1` / `7000` | Bind address for `python -m page_to_screen` |
| `P2S_CONTACT` | unset | Email or URL put in the User-Agent ([Wikimedia policy](https://foundation.wikimedia.org/wiki/Policy:User-Agent_policy)) |
| `P2S_CACHE_PATH` | `data/cache.sqlite3` | SQLite cache file |
| `P2S_TTL_HOURS` | `24` | Cache lifetime |
| `P2S_SORT` | `popularity` | `popularity` or `year` (newest first) |

The curated authors live in `page_to_screen/config.py`, keyed by Wikidata QID.

## How the data is fetched

Films are items that are an instance of film (`P31`, including subclasses of `Q11424`), are
"based on" (`P144`) a work with an author (`P50`), and have an IMDb ID (`P345`). Publication
dates come from `P577`; for source works without one, inception (`P571`) is used.

A single query returning all of that plus labels sits at the SPARQL endpoint's 60-second limit
and times out intermittently, so the fetch is split: one structure query (IDs and dates only),
then label lookups in batches of 1,000 IDs. Transient failures are retried, honouring `Retry-After`.

The result (about 13,700 films) is stored in SQLite and held in memory. Requests never touch
Wikidata: once the snapshot is older than the TTL the next request triggers a background
refresh while the old snapshot keeps being served. If a refresh fails, the old data stays and
the next attempt waits 10 minutes.

Known limits of the data: films with no label in English, French, German, Spanish or Italian
are skipped, and works with neither a publication nor an inception date can't appear in the
classics catalog.

## Layout

```
page_to_screen/
  config.py     settings, curated authors
  wikidata.py   SPARQL queries and client
  cache.py      SQLite key/value cache
  store.py      in-memory snapshot + background refresh
  catalog.py    filtering, search, pagination, Stremio meta objects
  app.py        FastAPI routes
  asgi.py       `uvicorn page_to_screen.asgi:app`
tests/          pytest suite with a fake Wikidata endpoint
```

## Deploy

Stremio only installs remote addons over HTTPS; both hosts below provide it automatically.
Both configs run `python -m page_to_screen refresh` during the build, so the cache ships with
the deploy and the first request is served immediately.

### Render

1. Push this repo to GitHub.
2. Render dashboard → **New → Blueprint** → pick the repo. `render.yaml` sets the build and
   start commands and the `/health` check.
3. Set `P2S_CONTACT` when prompted.
4. Install `https://<your-service>.onrender.com/manifest.json` in Stremio.

Free instances sleep after 15 minutes idle and take up to a minute to wake, which is longer
than Stremio waits. `render.yaml` therefore asks for the `starter` plan. To stay on the free
plan, change `plan: free` and have an uptime monitor (UptimeRobot, cron-job.org) request
`/health` every 10 minutes.

### Railway

1. Push this repo to GitHub.
2. Railway → **New Project → Deploy from GitHub repo**. `railway.json` sets the build and start
   commands and the health check.
3. **Variables**: add `P2S_CONTACT`.
4. **Settings → Networking → Generate Domain**, then install
   `https://<your-app>.up.railway.app/manifest.json` in Stremio.

Leave **Serverless** (app sleeping) switched off, otherwise the service has the same wake-up
delay as Render's free plan.

### Keeping the cache across restarts (optional)

Without a volume, each restart starts from the snapshot taken at build time and refreshes in
the background if it is over 24 hours old. To keep refreshed data instead, attach a volume
(Railway volume, Render disk) and point `P2S_CACHE_PATH` at it, e.g. `/data/cache.sqlite3`.
The build can't write to a volume, so the very first boot serves empty catalogs for a few
minutes while it fetches.
