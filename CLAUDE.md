# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Investment opportunity scanner for US stocks and bonds: ingest fundamentals/prices/yields, score securities against investment philosophies (Graham first), emit signals on score transitions, and (later) feed an in-app alert feed, a paper-trading sandbox and an optional Telegram channel. It is built in phases; **only Phase 0-1 exist**: scoring graph, data layer, ingestion, API skeleton, web scaffold. The metrics layer that turns stored facts/prices into scorer inputs, the fast-loop worker (`apps/api/src/fin_api/worker.py` is a heartbeat placeholder), alerts, sandbox and the real UI are not built yet. Not investment advice.

## Commands

Python is a `uv` workspace (`packages/core`, `apps/api`, `flows`), Python >= 3.12; the web app uses `pnpm`.

```sh
uv sync --all-packages                    # install everything
uv run pytest -q                          # all Python tests (testpaths set in root pyproject)
uv run pytest packages/core/tests/test_graph.py::test_graham_all_pass   # one test
uv run pytest -k "rebackfill"             # by keyword
uv run ruff check . && uv run ruff format --check .      # lint + format (line length 100)

TEST_DATABASE_URL=postgresql+psycopg://user@localhost:5432/fin_test uv run pytest   # also run DB tests on real Postgres
RUN_LIVE_TESTS=1 SEC_USER_AGENT="Name you@example.com" FRED_API_KEY=... uv run pytest -m live  # real SEC/FRED contract checks

uv run python -m fin_core.db.migrate upgrade        # apply migrations (also: downgrade, current); needs DATABASE_URL or POSTGRES_*
uv run uvicorn fin_api.main:app --reload            # API on :8000
uv run python -m fin_flows.ingestion                # serve Prefect schedules (needs PREFECT_API_URL)

cd apps/web && pnpm install && pnpm dev            # UI on :5173, proxies /api to :8000
cd apps/web && pnpm typecheck && pnpm test && pnpm build
cd apps/web && pnpm vitest run -t "name"           # one web test

scripts/smoke-images.sh                              # build + smoke-test images (needs Docker and network access to ghcr.io/docker hub)
docker compose -f deploy/docker-compose.yml config -q
```

GitHub Actions workflows (`.github/workflows/`) are **disabled** (`workflow_dispatch` only; a TODO list of fixes before re-enabling is at the top of `ci.yml`). Nothing runs on GitHub, so run lint and tests locally before every push. Commits follow Conventional Commits (Commitizen, `cz.yaml`).

## Architecture

**Scoring graph (`packages/core/src/fin_core/graph/`).** A LangGraph `StateGraph` built in `build.py`: every node in `PHILOSOPHY_NODES` (`nodes/__init__.py`) runs in parallel from START, then `guardrails` -> `aggregate` -> `decide` -> END. Nodes write `philosophy_results` through a merge reducer (`state.py`), and each returns the same `ScoreResult` contract (`models.py`), so a deterministic node can later be swapped for an LLM-agent node (`impl: deterministic | llm` per node in `config/philosophies.yml`) without touching the wiring. To add a philosophy: new module in `graph/nodes/`, register it in `PHILOSOPHY_NODES`, add its thresholds and a weight to `config/philosophies.yml`. `decide` emits a signal only when conviction crosses a threshold relative to `previous_conviction` (buy >= 70, sell < 50 are constants in `build.py`), never on every run. `guardrails` is a stub (Piotroski/Altman/Beneish are planned). Graham deliberately fails non-positive P/E, P/B and negative debt/equity; the P/E x P/B product requires both factors positive.

**Data layer (`fin_core/db/`).** SQLAlchemy 2 models, repository functions, and Alembic migrations that live **inside the package** (`db/migrations/`, shipped in the wheel so non-editable images can migrate). Migrations are hand-written; a test asserts they match `models.py`, so every model change needs a new revision. Code must run on both Postgres (production) and SQLite (fast tests), hence the dialect switch in `repo._insert`. Key semantics:
- `fundamental_facts` keeps every filed version of a fact with its `filed` date; `repo.facts_as_of(as_of)` returns only what was public then (restatements invisible until filed). Instant facts store `period_start == period_end` so the natural key has no NULLs.
- `repo.upsert_securities` only updates columns a row provides (absent columns are left alone, so the universe sync never wipes a resolved CIK).
- TimescaleDB is optional: the migration turns `price_bars` into a hypertable only if the extension exists. The database must be UTF-8 (SQL_ASCII makes psycopg 3 return bytes and breaks SQLAlchemy).

**Providers and ingestion (`fin_core/providers/`, `fin_core/ingest.py`, `flows/fin_flows/ingestion.py`).** Providers sit behind small protocols (`providers/base.py`): `edgar.py` (ticker->CIK, XBRL company facts, mapped to normalized concepts via `edgar_concepts.py`), `fred.py` (yields/spreads), `yfinance_provider.py` (prices), `alphavantage.py` (fallback fundamentals). Provider errors must never contain credentials (API keys travel in query strings). `ingest.py` jobs write an `ingestion_runs` row: a run is `failed` until it completes normally, one bad ticker/series (including DB errors) makes it `partial`, `RateLimitError`/`AccessDeniedError` abort it, and stale `running` rows are failed by the next run. Prefect flows in `flows/` are thin wrappers that raise on a total failure; the always-on minute-level loop is meant to be a separate service, not a Prefect job.

**Config and flags.** `config/*.yml` is found by walking up parent directories from the module file (`load_config`, `load_universe`, `FeatureFlags`), which is why Docker images copy `config/` next to the venv. Feature flags come from `config/features.yml`, overridable by `FEATURE_<NAME>` env vars; `telegram_enabled` is off by default and, when it exists, must not start the bot or require a token while off.

**API and web.** `apps/api` (`fin_api.main`) exposes `/healthz` (liveness), `/readyz` (config/graph load plus a DB check when a DB is configured; 503 if unreachable), `/api/features`, `/api/score`. `apps/web` is a Vite + React + TypeScript scaffold only.

**Deployment prep.** `deploy/` has one `python.Dockerfile` for all Python images (`--build-arg PACKAGE=fin-api|fin-flows`, must stay `uv sync --no-editable`), a web/nginx image, a dev compose file and a prod-shaped template. A one-shot `migrate` service runs before api/worker/flows. Nothing is deployed anywhere by this repo.

## Constraints and gotchas

- Prefect's server image tag in both compose files must equal the Prefect version locked in `uv.lock` (`flows/tests/test_versions.py` enforces this). `apps/api/pyproject.toml` caps FastAPI below 0.137 because the Prefect version in the shared env breaks on newer FastAPI; re-check both when upgrading Prefect.
- Every `python -m <module>` in the compose files must name a real module (tested).
- Stored prices are the source's split/dividend-**adjusted** prices, not as-traded. If a refresh shows stored bars were restated, `ingest_prices` re-fetches the whole history. A point-in-time backtest of price-based valuation needs raw OHLC plus adjustment factors, which does not exist yet.
- The EDGAR and FRED providers were written from public API docs and tested only against synthetic fixtures (`packages/core/tests/fixtures`, made-up numbers). They have not been verified against the live APIs (a sandbox that blocks `sec.gov`/FRED cannot run them); the `-m live` tests are the check. The EDGAR 403 rate-limit-vs-bad-User-Agent split is an unverified heuristic.
- SEC requires `SEC_USER_AGENT` with contact details; FRED needs `FRED_API_KEY`. Copy `.env.example` to `.env` (never commit it).
