# 002: Data layer and ingestion

- **Phase / PR:** Phase 1, [#11](https://github.com/festei/fin_app/pull/11) (commits `dd1cd86`, `7cdda61`, `82a236f`, review fixes `32f255d`)
- **Status:** Delivered

## Context

Scoring needs fundamentals, prices and yields stored in a database, with **point-in-time**
semantics so a later backtest never sees data that was not yet public.

## Goals / Non-goals

Goals: a Postgres schema with migrations, a stock/ETF universe, SEC EDGAR and FRED providers,
price ingestion, idempotent jobs with recorded runs, scheduled Prefect flows. Non-goals: metrics,
more scorers, signals.

## Decisions

- **SQLAlchemy 2 + hand-written Alembic migrations shipped inside `fin_core`** (so non-editable
  images can migrate). A test asserts migrations match the models and a downgrade/upgrade round
  trip works.
- **Portable across Postgres (production) and SQLite (fast tests)**; the DB tests run on SQLite
  always and on real Postgres when `TEST_DATABASE_URL` is set.
- **TimescaleDB is optional:** `price_bars` becomes a hypertable only if the extension exists.
- **`fundamental_facts` keeps every filed version** of a fact with its `filed` date;
  `repo.facts_as_of(as_of)` returns only what was public then (restatements invisible until filed).
  Instant facts store `period_start == period_end` so the natural key has no NULLs.
- **`upsert_securities` only updates provided columns**, so a universe sync never wipes a resolved CIK.
- **Providers behind small protocols:** EDGAR (ticker to CIK, XBRL company facts normalised into 14
  concepts) and FRED (Treasury yields, curve spread, IG/HY credit spreads); Yahoo (`yfinance`) for
  prices; Alpha Vantage as a fallback fundamentals source (not scheduled). **Errors never contain
  API keys.**
- **Failure isolation:** one bad ticker or series (including DB errors) makes a run `partial`; a
  rate-limit or access-denied response aborts it; a run is `failed` until it completes normally;
  stale `running` rows are failed by the next run.
- **Prices back-fill on the first run, then refresh a short window**; if the source restated stored
  bars, the whole history is re-fetched.
- **Universe in `config/universe.yml`:** 15 stocks across sectors plus 4 bond ETFs as proxies.
- **`/readyz` checks the database** when configured (503 if unreachable, no connection details);
  `/healthz` stays independent. Compose gained a one-shot `migrate` service that api, worker and flows
  wait for.

## Design

Tables `securities`, `price_bars`, `fundamental_facts`, `bond_yields`, `ingestion_runs`; see
[architecture/03-data-model](../architecture/03-data-model.md). Flows: `sync-universe`,
`ingest-prices`, `ingest-yields`, `ingest-fundamentals` (see
[architecture/04](../architecture/04-data-pipeline-and-scoring.md)).

## Acceptance criteria and verification

- 105 tests pass (2 opt-in live tests skipped); run on SQLite and a real Postgres 16, including the
  migration-vs-models check.
- Manual end-to-end on Postgres: migrate CLI, universe sync, `/readyz` reporting `db: ok`, then 503
  with the database stopped while `/healthz` stayed 200, then recovery.

## Review outcomes

Commit `32f255d` (PR #11 review): corrected run status accounting, stopped the universe sync from
wiping a resolved CIK, wired the compose `flows` service, and handled price restatements.

## Known limitations

- **EDGAR and FRED were written from public API docs and tested only against synthetic fixtures**
  (`packages/core/tests/fixtures`, made-up numbers); the sandbox blocks `sec.gov` and FRED, so the
  opt-in `-m live` tests are the real check. The EDGAR 403 rate-limit-versus-bad-User-Agent split is
  an unverified heuristic.
- The `yfinance` path is covered by a fake provider only; the TimescaleDB branch was not exercised.
- The database must be UTF-8 (with SQL_ASCII psycopg 3 returns bytes).
- Stored prices are the source's **adjusted** prices, not as-traded.
