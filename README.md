# fin-app

Investment opportunity scanner for US stocks and bonds. Scores securities against classic
philosophies (Graham first; Buffett, Lynch, Greenblatt, Fisher, bond value to follow) using a
LangGraph graph where each philosophy is a node, emits signals on score transitions, and will feed an
in-app alert feed, a paper-trading sandbox, and an (optional, feature-flagged) Telegram channel.

Not investment advice. See the roadmap and design in the approved plan (phases 0-7).

## Layout

| Path | What |
|---|---|
| `packages/core` | `fin_core`: models, feature flags, providers, LangGraph philosophy graph |
| `apps/api` | `fin_api`: FastAPI service and the fast-loop worker (placeholder until phase 3) |
| `apps/web` | React + TypeScript + Vite trading UI |
| `flows` | Prefect flows for slow batch work |
| `config` | `features.yml` (feature flags), `philosophies.yml` (thresholds, weights) |
| `deploy` | Dockerfiles, `docker-compose.yml` (dev), `docker-compose.prod.yml` (template, not deployed) |

## Develop

```sh
uv sync --all-packages          # Python workspace
uv run pytest                   # Python tests
uv run ruff check . && uv run ruff format --check .
uv run uvicorn fin_api.main:app --reload          # API on :8000

cd apps/web && pnpm install && pnpm dev           # UI on :5173, proxies /api to :8000
```

Containers: `cp .env.example .env && docker compose -f deploy/docker-compose.yml up --build`. A one-shot
`migrate` service applies the migrations before the API, worker and flows start; `/readyz` reports
`db: ok` once the database answers (503 if a configured database is unreachable).

## Database

Postgres (TimescaleDB optional: `price_bars` becomes a hypertable when the extension exists, plain
Postgres works unchanged). The database must be UTF-8 encoded. Config: `DATABASE_URL`, or
`POSTGRES_USER/PASSWORD/HOST/PORT/DB`.

```sh
uv run python -m fin_core.db.migrate upgrade      # apply migrations (also: downgrade, current)
TEST_DATABASE_URL=postgresql+psycopg://user:pw@localhost:5432/fin_test uv run pytest
```

Without `TEST_DATABASE_URL` the DB tests run on SQLite only; with it they also run on Postgres.
Fundamentals are stored with their filing date, and `repo.facts_as_of` only returns what was public
at the given date (no look-ahead).

## Data ingestion (slow cadence)

* `fin_core.providers.edgar`: SEC EDGAR ticker→CIK map and XBRL company facts (needs `SEC_USER_AGENT`).
* `fin_core.providers.fred`: Treasury yields, curve spread and credit spreads (needs `FRED_API_KEY`).
* `fin_core.providers.yfinance_provider`: daily price bars (swap in a paid feed behind `PriceProvider`).
* `fin_core.ingest`: `ingest_fundamentals`, `ingest_prices`, `ingest_yields`; per-ticker/series isolation,
  idempotent upserts, back-fill on first run then a short refresh window, one `ingestion_runs` row per job.
* `flows/fin_flows/ingestion.py` and `scoring.py`: Prefect flows; `python -m fin_flows.serve` serves all schedules (ingestion first, scoring last).
* Config: `config/universe.yml`, `config/bond_series.yml`.

**Run status.** A run is `failed` until it completes normally, so a crash is never recorded as `ok`.
One failing ticker/series (including a database error) makes the run `partial`; rate-limit and
access-denied responses abort it. Rows left `running` by a dead process are marked failed by the next run.

**Price adjustment (known limitation).** Prices are the source's split/dividend-*adjusted* prices as of
fetch time. When a refresh shows a stored bar was restated, the whole history is re-fetched so the series
stays on one basis (`rebackfilled` in the run summary). They are not *as-traded* prices, so a point-in-time
backtest of price-based valuation needs raw OHLC plus adjustment factors; that is planned with the backtester,
before Phase 2 scorers are validated historically.

**Status:** both providers were written from the public API documentation and tested only against
synthetic fixtures with the documented response shape (`packages/core/tests/fixtures`). They have not
been run against the live SEC/FRED APIs yet. Verify where those hosts are reachable:

```sh
RUN_LIVE_TESTS=1 SEC_USER_AGENT="Your Name you@example.com" FRED_API_KEY=... uv run pytest -m live -v
```

## Signals and alerts

Scoring runs after the daily ingestion (`score-universe` flow, or `fin_core.scoring_run.run_scoring`). Every run is stored in `scores`; a *transition* (conviction crossing the buy or sell line relative to the last actionable stored score) creates a `signals` row, which `config/alerts.yml` turns into an alert: sells only for watchlist stocks, with a cooldown unless conviction moved materially.

* In-app feed, always on: `GET /api/alerts`, `POST /api/alerts/{id}/read`, `GET /api/opportunities`, `GET|PUT|DELETE /api/watchlist`, `GET /api/channels`.
* Telegram, **off by default**: set `FEATURE_TELEGRAM_ENABLED=true` plus `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` and run the worker (`python -m fin_api.worker`). Commands: `/watch`, `/unwatch`, `/watchlist`, `/top`, `/why`, `/mute`, `/unmute`, `/help`. Verified only against a mocked Bot API so far.

## Feature flags

`config/features.yml` holds defaults; env `FEATURE_<NAME>` overrides. `telegram_enabled` is off by
default: when off, no Telegram channel is registered and no token is needed.
