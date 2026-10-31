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

Containers: `cp .env.example .env && docker compose -f deploy/docker-compose.yml up --build`.

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

## Feature flags

`config/features.yml` holds defaults; env `FEATURE_<NAME>` overrides. `telegram_enabled` is off by
default: when off, no Telegram channel is registered and no token is needed.
