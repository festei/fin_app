# Spec 009: Redesign around DDD and hexagonal architecture

Status: delivered (steps 0-6). Nothing is committed by the tooling; the work is in the working tree.

## 1. Context

`fin_core` is one package in which domain rules, use cases and infrastructure live side by side.
A coupling inventory of the current code found:

- **No domain layer.** Only the scoring graph (`graph/*`), the pydantic DTOs in `models.py`, and parts of `metrics/`, `alerts/policy.py` and `sandbox/config.py` are I/O-free.
- **Rules are hidden in infrastructure.**
  - Hysteresis (`armed_state`) and "actionable score" live in SQL in `db/signals_repo.py`.
  - Fill, holdings and strategy rules are interleaved with `select()` and ORM mutation in `sandbox/engine.py` (468 lines).
  - `ingest.py` mixes job-run tracking with ingestion and raw queries.
- **ORM rows leak upward.** `AlertPolicy.decide`, `annual_series`, `build_message_text` and all of `sandbox.engine` take or return ORM objects. `fin_api/main.py` (840 lines) imports `fin_core.db.models` and holds 9 inline `select()` calls plus business logic (candle aggregation, score-history windowing, portfolio views).
- **Hidden dependencies inside the core.**
  - Business constants sit in the wrong place: `BUY_REARM_BELOW` in `graph/build.py`, `CONCEPT_TAGS` in `providers/`.
  - Config discovery walks `__file__.parents` in 5 places; `os.environ` is read in 6 modules; `datetime.now()` is called in 7.
  - `ScoreRecord.details` is an untyped JSON contract between `signals/engine._details` and 3 readers.
- **Nothing enforces boundaries.** No import-linter and no architecture test exist.

**Goal.** A protected core (domain plus application) that depends on nothing outside itself, with every
I/O concern (database, HTTP providers, Telegram, LangGraph, YAML/env config) as an adapter behind a port.
"Protected" means the boundary is a build and CI fact, not a convention.

**Non-goals.**
- No behaviour change, no new features.
- No schema change: Alembic revisions 0001-0004 stay, and the stored `scores.details` JSON keeps its current shape.
- No message broker and no microservices.
- The web app is untouched, and the HTTP API contract is unchanged.

## 2. Decisions taken

| Question | Decision |
| --- | --- |
| Physical protection | Separate uv workspace packages. The domain package declares **zero** third-party dependencies, so `import sqlalchemy` cannot resolve there. import-linter and ruff back it up. |
| Domain split | Four bounded contexts: Market Data, Scoring, Signals & Alerts, Trading Sandbox, plus a small shared kernel. |
| Domain vs ORM | Pure domain entities plus explicit mappers in the persistence adapter. The tables are unchanged. |
| Rollout | Strangler, one context at a time. The full suite is green and there is one commit after each step. |

Decisions I made while designing (flagged so you can overrule them):

1. **Ports live in the application layer, not the domain.** The domain has no I/O abstractions. Aggregate methods take plain data and `now`, and never call a repository. The use case loads, calls the domain, saves.
2. **The domain uses stdlib only** (frozen dataclasses with `__post_init__` invariants), not pydantic. Serialisation lives in adapters. This costs a rewrite of the pydantic models in `models.py`, and buys a truly dependency-free core.
3. **`decide` leaves the LangGraph.** Scoring becomes stateless (philosophies, guardrails, aggregate produce an `Assessment`). The stateful transition rule (previous conviction, hysteresis, blockers) becomes the Signals context's `SignalDecider`. Behaviour is identical; the graph loses `previous_conviction`/`buy_armed`/`sell_armed` from its state.
4. **Contexts reference each other by `Ticker`** (a value object), never by database integer id. Ints exist only inside the persistence adapter.
5. **Cross-context order is a one-way chain**, enforced by an import-linter `layers` contract:
   `sandbox → signals → scoring → market_data → shared`.
6. **Reads use query ports** (CQRS-lite): the write side goes through aggregates and repositories, and the read side returns DTOs from query services backed by SQL. This is where the API's inline `select()`s go.

## 3. Target architecture

```
                    DRIVING ADAPTERS (call the core)
   fin_api (FastAPI)   fin_worker (retries, bot)   fin_flows (Prefect)   scripts/seed_demo
        └─────────────────────────┬────────────────────────┘
                                  ▼  (only via fin_bootstrap's Container)
  ┌───────────────────────── THE PROTECTED CORE ─────────────────────────┐
  │  fin_application   use cases · DTOs · read-model queries · PORTS      │
  │        ▲ depends on                                                   │
  │  fin_domain        shared kernel · market_data · scoring ·            │
  │                    signals · sandbox   (stdlib only, no ports)        │
  └──────────────────────────────────────────────────────────────────────┘
                                  ▲  implemented by
                    DRIVEN ADAPTERS (the core calls them via ports)
   fin_persistence   fin_providers   fin_telegram   fin_langgraph   fin_config
   (SQLAlchemy,      (httpx, pandas, (httpx;        (langgraph)     (yaml, env,
    Alembic, UoW,     yfinance)       channel + bot                  file discovery)
    mappers, queries,                 inbound)
    leader lock)
```

**Dependency rule.** `fin_domain ← fin_application ← adapters ← fin_bootstrap ← apps`. Arrows only point inward.
Adapters never import each other. Apps import adapters **only** through `fin_bootstrap`.

### 3.1 Workspace packages

| Package (dir) | Import name | Third-party deps | Role |
| --- | --- | --- | --- |
| `packages/domain` | `fin_domain` | **none** | Entities, value objects, domain services, domain events, domain errors. |
| `packages/application` | `fin_application` | **none** | Use cases, command/query DTOs, driven-port Protocols, application errors. |
| `packages/persistence` | `fin_persistence` | sqlalchemy, alembic, psycopg | ORM tables, mappers, repositories, `SqlUnitOfWork`, query services, migrations, `LeaderLock` as `SingleInstanceGuard`. |
| `packages/providers` | `fin_providers` | httpx, pandas, yfinance | EDGAR, FRED, yfinance, Alpha Vantage clients. |
| `packages/telegram` | `fin_telegram` | httpx | `TelegramChannel` (driven) and `TelegramBot` (driving). The bot calls application use cases and has no DB access. |
| `packages/langgraph` | `fin_langgraph` | langgraph | `ScoringEngine` adapter that wires the domain scorers into a `StateGraph`. |
| `packages/config` | `fin_config` | pyyaml | One `ConfigLocator`, YAML/env loaders, `FeatureFlags`, universe files. Implements the config ports. |
| `packages/bootstrap` | `fin_bootstrap` | all adapters | `Settings` (the only env reader) and `Container` (the composition root). |
| `packages/testkit` | `fin_testkit` | `fin-application` | In-memory fakes of the ports for tests. No production package may import it (import-linter). Added in step 1. |
| `apps/api` | `fin_api` | fastapi, uvicorn | HTTP only: routers per context, schemas, error mapping. |
| `apps/worker` | `fin_worker` | none extra | Moved out of `fin_api`. Retry loop, bot loop, shutdown. |
| `flows` | `fin_flows` | prefect | Thin wrappers over use cases. |

`fin_core` stays as a shrinking legacy package, with re-export shims, until step 6 deletes it.

### 3.2 The domain (`fin_domain`)

Every package below is a context. Contexts expose only their own public types.

**`shared`**: `Ticker` (VO: validates and uppercases), `DomainError` family (`InvariantViolation`, `NotFound`), `DomainEvent` base, `AsOf`, `JobRun` aggregate
(status is `failed` until completed; `partial`/`ok`/`failed` derivation; stale-run expiry rule; moved from `ingest.py`).

**`market_data`**
- Aggregate `Security` (ticker, cik, name, kind, sector, active; the "absent columns are left alone" merge rule becomes `Security.merged_with(update)`).
- VOs `PriceBar`, `Fact` (concept, tag, unit, period, filed, accession, value; instant facts keep `period_start == period_end`), `BondYield`.
- Domain services `PointInTimeFacts` (the `facts_as_of` rule: latest version filed on or before `as_of`), `FiscalYearSeries` (from `metrics/annual.py`: 10-K family only, duration windows, tag priority, derived `TotalDebt`, single-source share counts), `restated()` (price restatement tolerance), `aggregate_candles()` (from `main.py:_aggregate`).
- Constants `CONCEPT_TAGS`/`TAG_TO_CONCEPT` move here from `providers/edgar_concepts.py`.

**`scoring`** (stateless)
- VOs `SecurityMetrics`, `AnnualPoint`, `Criterion`, `ScoreResult`, `GuardrailVerdict`, `Assessment` (conviction or `None`, per-philosophy results, guardrail reasons/notes, `signal_blockers`, explanation). The `Assessment` is a typed replacement for the untyped `details` dict.
- Services:
  - `MetricsCalculator` (the pure part of `compute_metrics`: ratios, staleness thresholds, blockers, same-fiscal-year rule, multi-class list)
  - five pure scorers: `GrahamScorer`, `BuffettScorer`, `LynchScorer`, `GreenblattScorer`, `FisherScorer`
  - `Guardrails` (Piotroski, Altman)
  - `Aggregator` (weighted mean, abstention, guardrail cap)
- Typed config VOs `PhilosophyConfig`, `GuardrailConfig`. `PhilosophyScorer` is a domain Protocol, so deterministic and LLM scorers stay swappable.

**`signals`** (stateful decisions)
- Aggregates:
  - `Signal` (lifecycle: awaiting decision → processed or suppressed with reason)
  - `Alert` with `Delivery` entities (status machine pending → sent/failed/skipped; `claim()`, `mark_sent()`, `mark_failed()` with backoff; `attempts` is the optimistic-concurrency version)
  - `Watchlist`
  - `ChannelState` (mute window)
- Services:
  - `SignalDecider` (transition across 70/50, hysteresis 65/55, `emerging` when no baseline, withheld on blockers; constants move here)
  - `AlertPolicy` (cooldown, material change, sell-only-for-watchlist, severity, retry schedule; takes a `LastAlert` VO, not an ORM row)
  - `AlertComposer` (the message text; the "Use /why" hint is a channel concern passed in)
- `ScoreEntry` VO plus the pure rules "actionable" (conviction set and not blocked) and `is_armed(convictions_since_last_signal)`, moved out of SQL.
- Events: `ScoreRecorded`, `SignalRaised`, `AlertCreated`.

**`sandbox`**
- Aggregate `Portfolio` (cash, fee, mode, `StrategyRules`, `last_processed_signal`) owning `Order` entities (pending/filled/rejected/cancelled).
- Methods: `place_order`, `cancel_order`, `settle(price_book, now)`, `apply_signals(signals, price_book)`, `holdings(price_book)`, `snapshot(price_book)`.
- Services:
  - `FillModel` (fills on the first bar after the decision day, adjusted price, sells before buys on the same close)
  - `HoldingsLedger` (average cost, restated by `fill price / current bar close`, buy fees in cost basis)
  - `StrategySizing` (`position_pct`, `max_positions`, `min_cash_pct`, pending-buy reservation)
- VOs `Money`, `Quantity`, `FeeBps`, `OrderSide`, `Holding`, `PortfolioSnapshot`, `StrategyRules` (validation moves here from `sandbox/config.py`).
- Limits (`MAX_QUANTITY` etc.) are domain constants.

**Domain purity rules.**
- No `datetime.now()`: `now` and `as_of` are parameters (tz-aware UTC only; naive-SQLite handling stays in the persistence mappers, which removes the three duplicated `_utc` helpers).
- No `os`, `logging` config, files or network.
- Money is `Decimal`.

### 3.3 The application layer (`fin_application`)

- **Driving side**: one class per use case with `execute(command) -> result`.
  - Market Data: `SyncUniverse`, `IngestPrices`, `IngestFundamentals`, `IngestYields`
  - Scoring/Signals: `ScoreSecurityLive` (API), `RunScoring`, `ProcessSignals`, `DeliverDueAlerts`, `MarkAlertRead`, `Watch`/`Unwatch`, `MuteChannel`
  - Sandbox: `CreatePortfolio`, `PlaceOrder`, `CancelOrder`, `RunSandbox`
  - Telegram bot commands become use cases too (`ExplainScore`, `TopOpportunities`, …).
- **Read side**: query ports returning frozen DTOs: `OpportunityQuery`, `SecuritySearchQuery`, `PriceHistoryQuery`, `ScoreHistoryQuery`, `AlertFeedQuery`, `PortfolioViewQuery`, `ChannelStatusQuery`. The candle, windowing and position-view *rules* stay in domain or application code. Only the SQL goes to the adapter.
- **Driven ports (Protocols)**:
  - Storage: `UnitOfWork`/`UnitOfWorkFactory` (one transaction each, preserving today's per-security and per-signal transactions) exposing `SecurityRepository`, `PriceRepository`, `FactRepository`, `YieldRepository`, `ScoreRepository`, `SignalRepository`, `AlertRepository`, `WatchlistRepository`, `ChannelStateRepository`, `PortfolioRepository`, `JobRunRepository`.
  - Outbound services: `PriceProvider`, `FundamentalsProvider`, `YieldProvider`, `AlertChannel`, `ScoringEngine`.
  - Config and environment: `ScoringConfigProvider`, `AlertPolicyConfigProvider`, `SandboxConfigProvider`, `UniverseSource`, `FeatureFlagProvider`.
  - Runtime: `Clock`, `SingleInstanceGuard`, `BotCursorStore`.
- **Concurrency without leaking SQL**: `PortfolioRepository.load_exclusive(id)` replaces `FOR UPDATE`. Delivery claiming becomes optimistic: `AlertRepository.save(alert, expected_version)` raises `ConcurrencyConflict`, and the adapter implements it with the same compare-and-set on `attempts`. Send-after-commit ordering is unchanged.
- **Errors**: `NotFound`, `InvalidCommand`, `ConcurrencyConflict`, `ProviderError`/`RateLimitError`/`AccessDeniedError` (moved from `providers/base.py` because they are part of the port contract). The API maps them to 404/422/503.

### 3.4 Adapters

- **`fin_persistence`**
  - `tables.py` (today's `db/models.py`, unchanged), `mappers.py`, one repository per port.
  - `SqlUnitOfWork`, `session_scope`/`make_engine`, the Postgres/SQLite `_insert` dialect switch, Alembic and `migrate.py` (revisions 0001-0004 untouched).
  - `queries/*.py`: the SQL that currently sits in `main.py` and `signals_repo.py`.
  - `AssessmentCodec` writes and reads `scores.details` in exactly today's JSON shape.
  - `PgAdvisoryLeaderLock` implements `SingleInstanceGuard`.
- **`fin_providers`**: provider clients unchanged in behaviour. Credentials still never appear in errors. `normalize_facts` stays here as the anti-corruption translation into domain `Fact`s.
- **`fin_telegram`**: `TelegramClient`, `TelegramChannel`, `TelegramBot`. The bot parses commands and calls use cases, and its update cursor goes through `BotCursorStore`. Token masking is unchanged.
- **`fin_langgraph`**: builds the `StateGraph` (START → philosophy nodes in parallel → guardrails → aggregate → END) from domain scorers and implements `ScoringEngine`. This is the only package that imports `langgraph`.
- **`fin_config`**: one `ConfigLocator` (explicit `FIN_CONFIG_DIR`, else walk parents, so the Docker `config/` layout keeps working), YAML loaders producing typed domain config VOs, `FeatureFlags` (`FEATURE_<NAME>` overrides unchanged).
- **`fin_bootstrap`**: `Settings` is the **only** place that reads `os.environ`. `Container` builds engine, UoW factory, adapters and use cases, with `dispose()`. Telegram stays fully absent unless `FEATURE_TELEGRAM_ENABLED` (no client, no token, zero calls), and invalid channel config still fails at startup (`ChannelConfigError`).
- **Apps**
  - `fin_api`: routers per context, Pydantic response schemas (presentation only), `require_write_token`, 422 handler, 503 mapping, a per-request `Container` dependency. No `sqlalchemy` import and no `fin_persistence` import.
  - `fin_worker`: `LeaderLock` loop, retry loop and bot loop become use-case calls. Exit codes (2 invalid channel config, 3 lost lock) are preserved.
  - `fin_flows`: unchanged behaviour. Flows call use cases via the container.

## 4. How the core is protected (enforced, not conventional)

1. **Build-level**: `fin_domain` declares no dependencies, and `fin_application` declares only `fin-domain`. `uv sync --package fin-application` yields an environment with no SQLAlchemy, httpx or pandas.
2. **Isolation test** (`packages/domain/tests/test_core_isolation.py`): asserts the declared dependencies above, scans every source file of both core packages for forbidden imports, and self-tests the checker.
3. **import-linter contracts** in the root `pyproject.toml` (added as a dev dependency):
   - `layers`: `fin_api | fin_worker | fin_flows` → `fin_bootstrap` → adapters → `fin_application` → `fin_domain`.
   - `forbidden`: `fin_domain` and `fin_application` may not import `sqlalchemy`, `httpx`, `pandas`, `yfinance`, `yaml`, `langgraph`, `fastapi`, `prefect`, `alembic`, nor stdlib `os`, `sqlite3`, `socket`, `urllib`, `pathlib` (domain only).
   - `independence`: the adapters are mutually independent.
   - `forbidden`: apps may not import `fin_persistence`, `fin_providers`, `fin_telegram`, `fin_langgraph` or `fin_config` directly.
   - `layers` inside the domain: `sandbox → signals → scoring → market_data → shared`.
4. **Ruff**: `flake8-tidy-imports` banned-api for fast editor feedback, and `DTZ` rules in the core packages to catch naive or implicit `now()`.
5. **Ratchet**: step 0 adds the contracts with `ignore_imports` listing every existing violation; each step deletes the entries it fixes, and step 6 ends with none.
6. **Ports are tested by contract**: one shared contract suite per repository/channel/provider runs against the in-memory fake and the real adapter.

## 5. Test strategy

- **Domain tests**: pure, no DB, no fixtures. The existing scorer, guardrail, `decide`, policy and sandbox-rule tests move here and run in milliseconds.
- **Application tests**: use cases run against `fin_testkit` in-memory fakes of the ports (a `FakeUnitOfWork`, `FakeClock`, `FakeChannel`, `FakeProvider`). The scenarios in `test_signals.py`, `test_alert_delivery.py`, `test_scoring_run.py`, `test_ingest.py`, `test_sandbox.py` move here, so most of the 25 core test files stop needing migrated SQLite.
- **Adapter tests**: repository contract tests on SQLite, and on Postgres under `TEST_DATABASE_URL`. The `migrations match tables` test stays. Provider tests use `httpx.MockTransport`. The `-m live` contract tests stay.
- **API/worker/flows tests**: `TestClient` with a Container built on SQLite, unchanged behaviour assertions.
- **Characterization first**: before moving a context, add golden tests for the behaviours listed in `CLAUDE.md` that no test pins yet (see risks).

## 6. Strangler rollout

Each step ends with `uv run pytest -q`, `ruff check`, `ruff format --check`, the import-linter ratchet shrunk, and (for steps touching storage) the suite on Postgres. One Conventional Commit per step.

| Step | Scope | Exit criteria |
| --- | --- | --- |
| **0. Scaffolding** (done) | Create empty packages and workspace wiring (root `pyproject`, `[tool.uv.sources]`, `testpaths`, isort `known-first-party`, Dockerfile COPY lines, regenerate `uv.lock`). Add import-linter with the ratchet baseline, ruff rules, the dependency-metadata test. | Suite green; Docker build still passes `scripts/smoke-images.sh`. |
| **1. Foundation** (done) | Shared kernel (`Ticker`, errors, events, `JobRun`), `Clock`, `UnitOfWork` skeleton, `fin_config` (one `ConfigLocator`), `Settings`/`Container`, `fin_persistence` skeleton with `session_scope`/migrations moved. `fin_core.db.*` re-exports from the new location. | Migrations test green on both DBs. |
| **2. Market Data** (done) | Domain: `Security`, `Fact`, `PriceBar`, `PointInTimeFacts`, `FiscalYearSeries`, candle aggregation, `CONCEPT_TAGS`. Application: ingestion use cases + `JobRun` tracking + provider ports. Adapters: `fin_providers`, persistence repos/queries. | `ingest.py`, `metrics/annual.py`, `db/repo.py`, `universe.py` deleted from `fin_core`; ingestion flows call use cases. |
| **3. Scoring** (done) | Domain scorers/guardrails/aggregator/`MetricsCalculator`/`Assessment` to `fin_domain.scoring`; `fin_langgraph` adapter; `ScoreSecurityLive` use case. `decide` is not moved yet (still called by the legacy engine). | `graph/`, `metrics/`, `scoring.py`, `models.py` removed; `/api/score` and `/api/securities/{t}/score` unchanged. |
| **4. Signals & Alerts** (done) | `SignalDecider`, `ScoreEntry` rules, `Signal`/`Alert`/`Delivery`/`Watchlist`/`ChannelState`, `AlertPolicy`, `AlertComposer`; use cases `RunScoring`, `ProcessSignals`, `DeliverDueAlerts`; `fin_telegram`; `AssessmentCodec`; `SingleInstanceGuard`. Delivery claim becomes optimistic version check. | `signals/`, `alerts/`, `db/signals_repo.py`, `scoring_run.py` removed; alert feed/opportunities/watchlist/channels endpoints served by query ports. |
| **5. Sandbox** (done) | Port `Portfolio`/`Order`/`FillModel`/`HoldingsLedger`/`StrategySizing` into `fin_domain.sandbox`; `PlaceOrder`/`CancelOrder`/`RunSandbox`; `PortfolioRepository.load_exclusive`; `PortfolioViewQuery`. | `sandbox/` removed from `fin_core`; `/api/portfolios*` unchanged. |
| **6. Apps and cleanup** | Slim `fin_api` to routers + schemas; move the worker to `apps/worker` (`fin_worker`), update both compose files and `test_compose.py` (every `python -m <module>` must name a real module); flows via the container; `seed_demo.py` via the container. Delete `fin_core`. Zero ratchet entries. | `fin_api` has no `sqlalchemy` import; docs rewritten (below). |

Why bottom-up (Market Data first): each context depends only on contexts already migrated, so no
step needs a temporary shim in the new layers. Shims exist only on the legacy side.

## 7. Behaviours that must survive (acceptance)

These come from `CLAUDE.md` and are the regression list. Each gets a characterization test before its step if one does not exist.

- Point-in-time reads: restatements are invisible until filed; price re-backfill when stored bars were restated.
- Scoring: abstention semantics (`evaluated=False`, all-abstained gives `None` conviction and no signal), guardrail cap and the profitable-buyback exception, `skip_sectors`, `signal_blockers` withhold signals while the score is still reported.
- Signals: baseline is the last actionable score; blocked and `None` runs never become baseline (so `emerging`, not `buy`); hysteresis 65/55; idempotent re-runs.
- Alerts: sells only for watchlist; 24h per-kind cooldown overridden by a ≥ 10 point move; deliveries created in the alert's transaction; a channel enabled later never replays history; claim-before-send so concurrent senders cannot double-send; backoff and pending-timeout recovery; interrupted-run sweep via `processed_at`.
- Telegram: absent when the flag is off; at-most-once update offset; token never in logs or errors.
- Sandbox: next-bar fill, adjusted price, sells before buys, fill-time cash/holdings checks, average-cost holdings restated by `fill price / current bar close`, strategy signals acted on once (`last_signal_id`), exclusive per-portfolio lock.
- API: bearer-token default-deny writes, 422 without echoing input, 503 without a database, response shapes unchanged.
- Worker: exit 2 on invalid channel config, exit 3 when another instance holds the lock.

## 8. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| `decide` moving out of the graph changes graph state and tests. | Behaviour is a pure function; move `test_graph.py::decide` cases verbatim and assert the same signals before/after on the `seed_demo` data. |
| Aggregate loading cost (`Portfolio` with all orders; `ScoreHistory`). | Load only what the rule needs: `ScoreRepository` exposes `last_actionable` and `convictions_since_last_signal`, not full history. Portfolio loading is no worse than today's `holdings()`, which already reads all fills. A holdings snapshot is a later optimisation, not in scope. |
| Behaviour drift from mapper rewrites. | Keep the stored shapes (tables, `details` JSON) byte-compatible. Add an `AssessmentCodec` round-trip test against fixtures built from the current `_details`. |
| Naive vs aware datetimes (SQLite returns naive). | Normalise only in the mappers; domain accepts aware UTC only and rejects naive values (test). |
| Package-split breakage in images. | Step 0 updates the Dockerfile COPY lines and runs `scripts/smoke-images.sh`; `uv sync --no-editable` stays; each new package is wheel-buildable (hatchling). `fastapi<0.137` and the Prefect/compose version pin test stay untouched. |
| `fin-api` currently relies on transitive `sqlalchemy`/`pydantic`. | After step 6 it imports neither; pydantic is declared explicitly (via FastAPI). |
| Config lookup changes break images. | `ConfigLocator` keeps the walk-up fallback and adds `FIN_CONFIG_DIR`; test both. |
| Big diff fatigue. | Seven independent commits, each reviewable, each green. The ratchet makes progress measurable. |
| Test churn (≈40 test files move). | Tests move with their code in the same commit. No test is deleted without an equivalent. |

## 9. Documentation deliverables

- `docs/specs/009-hexagonal-redesign.md` (this spec) and the index row in `docs/specs/README.md`.
- Rewrite `docs/architecture/02-repository-map.md` (new package graph) and add `10-hexagonal-architecture.md` (ports/adapters diagram, dependency rule, bounded-context map). Update `01`, `04`, `05`, `06`, `07` diagrams where a component moved.
- `CLAUDE.md`: new "Architecture" section (package roles, the dependency rule, where to add a philosophy / provider / channel / use case), updated command paths, and the import-linter command.

## 10. Verification

End to end, per step and at the end:

1. `uv sync --all-packages --frozen && uv run pytest -q` (all suites), and `uv run ruff check . && uv run ruff format --check .`.
2. `uv run lint-imports` (import-linter): passes, and the ignore list is empty after step 6.
3. `TEST_DATABASE_URL=postgresql+psycopg://user@localhost:5432/fin_test uv run pytest` after every step that touches `fin_persistence`.
4. `uv run pytest packages/domain packages/application` runs with only the two core packages installed (`uv sync --package fin-application`), proving the core has no hidden infrastructure imports.
5. Smoke the real app: `uv run python scripts/seed_demo.py sqlite:///demo.db`, then `DATABASE_URL=sqlite:///demo.db API_TOKEN=dev-token uv run uvicorn fin_api.main:app` and `cd apps/web && pnpm dev`; compare the Opportunities, Security detail, Alerts and Sandbox pages with screenshots taken before step 0.
6. `scripts/smoke-images.sh` and `docker compose -f deploy/docker-compose.yml config -q` (needs Docker).
7. A before/after equivalence check: run `seed_demo` on the commit before step 0 and on the final commit, then diff the `scores`, `signals`, `alerts`, `sandbox_orders` and `portfolio_snapshots` tables (expected: identical apart from timestamps and ids).
8. A negative test of the protection itself: temporarily add `import sqlalchemy` to `fin_domain` and confirm both `lint-imports` and the dependency-metadata test fail.

## 11. Critical existing files (sources of the moves)

- Rules to extract: `graph/build.py` (`aggregate_node`, `decide_node`, rearm constants), `graph/nodes/*.py`, `graph/guardrails.py`, `graph/data.py`, `metrics/annual.py`, `metrics/compute.py`, `alerts/policy.py`, `sandbox/engine.py`, `sandbox/config.py`, `ingest.py` (`tracked_run`, `run_status`), `db/signals_repo.py` (`armed_state`, `latest_actionable_score`), `db/repo.py` (`facts_as_of`), `apps/api/src/fin_api/main.py` (`_aggregate`, `_view`, score-history windowing, search).
- Already ports, to reuse as-is: `alerts/base.py::AlertChannel`, `providers/base.py` Protocols.
- Reuse unchanged: the Alembic migrations, `db/models.py` as `tables.py`, `LeaderLock`, `TelegramClient`, the provider clients, `flows/tests/test_versions.py`.

## 12. Progress

**Step 0 (scaffolding) is done.**
- Workspace: nine new packages (`fin_domain` with its five context sub-packages, `fin_application`, `fin_persistence`, `fin_providers`, `fin_telegram`, `fin_langgraph`, `fin_config`, `fin_bootstrap`, `fin_worker`), all empty. `uv.lock` regenerated; the Dockerfile copies `apps/worker`.
- Boundaries: 8 import-linter contracts (`uv run lint-imports`). Two are ratchets that list the current violations: 30 edges from `fin_api`/`fin_flows` into `fin_core`, and 2 direct `sqlalchemy` imports in `fin_api`. Ruff bans infrastructure imports (TID251) and naive datetimes (DTZ) in the core packages only.
- Checked: the contracts and ruff rules reject planted violations; the domain tests pass in an environment containing only `fin-domain`; the lock installs with the Dockerfile's uv 0.5 line for `fin-api`, `fin-flows` and `fin-worker`.
- Images: `fin-api`, `fin-flows` and `fin-worker` build from `deploy/python.Dockerfile` and import cleanly (non-root uid 10001; the `fin-worker` image contains all new packages). `docker compose config -q` passes. `uv.lock` `revision` went from 3 to 5 because of the local uv 0.13.6; the Dockerfile's uv 0.5 line installs it fine.
- Known, unrelated to this step: `scripts/smoke-images.sh` stops at the web image, where `pnpm install --frozen-lockfile` fails with `ERR_PNPM_IGNORED_BUILDS` (pnpm's build-script approval policy). `apps/web` and `deploy/web.Dockerfile` are untouched; this needs its own fix.
- Ratchet at this point: 32 ignored imports, to reach 0 at step 6.

**Step 1 (foundation) is done.**
- Domain `shared` kernel: `Ticker`, `DomainError`/`InvariantViolation`/`NotFound`, `DomainEvent`, `ensure_utc`, and the `JobRun` aggregate with `RunStatus`, `derive_status`, `summarise_failures` (the rules that were in `ingest.py`).
- Application: `Clock`, `JobRunRepository`, `UnitOfWork` ports, `ApplicationError`/`InvalidCommand`/`ConcurrencyConflict`, and the `JobRunTracker` use case (the bookkeeping behind `tracked_run`).
- `fin_persistence`: `tables.py`, `migrations/` and `migrate.py` moved here with `git mv` (history kept), `session.py`, `SqlJobRunRepository`, `SqlUnitOfWork`. Alembic revisions 0001-0004 are unchanged.
- `fin_config`: one `ConfigLocator` (explicit dir, `FIN_CONFIG_DIR`, then nearest `config/`) replaces the five walk-up loops; `FeatureFlags` moved here.
- `fin_bootstrap`: `Settings` (environment read in one place) and `Container` (engine, unit-of-work factory, `JobRunTracker`, `dispose`).
- `fin_testkit`: `FakeClock`, `InMemoryJobRunRepository`, `InMemoryUnitOfWork`. The same repository/unit-of-work contract suite runs against the fake, SQLite and Postgres.
- Legacy shims keep every old import path working: `fin_core.db.models`, `fin_core.db.session` (still reads the environment; `test_settings_parity.py` pins it to `Settings`), `fin_core.db.migrate` (the compose command), `fin_core.features`.
- Deviations from the plan above: `Settings` is a frozen dataclass, not pydantic-settings (no dependency needed); `AsOf` is deferred to step 2, where the point-in-time semantics it must carry are ported; `JobRunTracker` exists beside the legacy `tracked_run`, which is replaced when the ingestion jobs move in step 2.
- Checked: ruff, 9 import-linter contracts, 548 tests on SQLite and 702 on real Postgres 16. In the rebuilt images, `python -m fin_core.db.migrate upgrade` (the compose command) migrates an empty Postgres to 0004, the migration files are present in the non-editable wheel, `ConfigLocator` finds `/app/config`, and a job run recorded through `Container` lands in Postgres.
- Found on the way: `from conftest import bar` in a core test breaks as soon as a second `conftest.py` exists in the repo (pytest registers them all as the module `conftest`). The persistence fixtures live in `db_fixtures.py` for that reason; the core test is left as is until it moves.
- Ratchet: still 32 ignored imports (apps still import `fin_core`; its modules now sit on top of the new packages).

**Step 2 (Market Data) is done.**
- Domain `market_data`: `Fact`, `PriceBar`, `YieldPoint`, `Security`/`SecurityUpdate`/`SecurityKind`, concept tags and classifications (moved from `providers/edgar_concepts.py` and `metrics/annual.py`), `known_as_of`/`latest_versions` (the point-in-time rule, previously only a SQL window function), `annual_series` (moved with `git mv`), `is_restated`, `aggregate_candles` (moved out of `main.py`; the API now calls it), universe and bond-series validation.
- Application: the market-data ports (repositories keyed by `Ticker`; `FundamentalsSource`, `PriceSource`, `YieldSource`, `UniverseSource`), `ProviderError`/`RateLimitError`/`AccessDeniedError` (moved out of `providers/base.py`), `RunSummary`/`tracked_run`/`run_status`, and the use cases `SyncUniverse`, `IngestFundamentals`, `IngestYields`, `IngestPrices` with the failure semantics of the old `ingest.py`.
- Adapters: `fin_providers` (clients moved with `git mv`, plus `EdgarFundamentalsSource`, `FredYieldSource`, `YahooPriceSource`, `bars_from_frame`), `fin_persistence.market_data` (four SQL repositories; `db/repo.py` moved to `fin_persistence/repo.py`), `fin_config.YamlUniverseSource`.
- `fin_bootstrap`: `Settings` gained `config_dir`, `sec_user_agent`, `fred_api_key`; `Container` builds the sources lazily and exposes the four use cases. The Prefect ingestion flows call them through `_container()`, and dispose the container after each run (the old flows leaked an engine per call).
- Deleted from `fin_core`: `ingest.py`, `universe.py`, `providers/`, `metrics/annual.py`. Tests moved with their code: `test_ingest.py` (24 scenarios) became `packages/application/tests/test_ingestion.py` on in-memory fakes, `test_universe.py` became config + wiring tests, the provider tests and fixtures moved to `packages/providers`, the pure `annual_series` tests moved to `packages/domain/tests/test_market_data.py`.
- Deviations from the plan: `fin_core/db/repo.py` stays as a re-export shim (compute_metrics, signals, sandbox, alerts and the HTTP app still call it) and goes away with its last consumer in steps 3 to 5; `compute_metrics` still reads facts through the SQL `facts_as_of` rather than `FactRepository` + `known_as_of` until step 3; `fin_core/run_tracking.py` is a small bridge so `scoring_run` and the sandbox runner keep their `Engine`-based interface until steps 4 and 5.
- Checked: ruff, 9 import-linter contracts, 625 tests on SQLite and 776 on real Postgres 16 (the repository contract suite runs on memory, SQLite and Postgres). In the rebuilt images, the compose migrate command reaches 0004, and `Container.sync_universe` run inside the flows image against Postgres stores the 19 securities idempotently, reading the YAML from `/app/config`.
- Ratchet: 26 ignored imports (24 into `fin_core`, 2 direct `sqlalchemy` in `fin_api`), down from 32; the removed ones were all in `fin_flows.ingestion`.
- Not verified: the EDGAR/FRED/Yahoo adapters against the live APIs (still synthetic fixtures; the `-m live` tests are the check). The failure paths I added around them (a figure that breaks a domain rule is skipped with a warning) are covered by unit tests only.

**Step 3 (Scoring) is done.**
- Domain `scoring`: `models.py` rewritten from pydantic to stdlib dataclasses (same fields, defaults and the 0-100 score rule, now an `InvariantViolation`), the five philosophies and `series.py` and `guardrails.py` moved with `git mv` and stripped of their LangGraph node wrappers, `aggregate()` (the old `aggregate_node`), `GuardrailVerdict`/`assess_guardrails`, `PHILOSOPHIES` + `run_philosophy` + `validate_config`, `Assessment` + `assess()` (a reference engine), and `calculate_metrics()` (the pure part of `compute_metrics`, which applies `known_as_of` itself).
- Domain `signals`: `decide_signal` and the thresholds/hysteresis constants left the graph and now live here as a pure function (`Signal`, `Decision`). This is the first cut of the context step 4 builds out. `decide` is no longer a graph node, as decision 3 in section 2 said.
- Application: `ScoringEngine` and `ScoringConfigSource` ports; `score()` (assess then decide), `Baseline`, `LiveScore`, and the `ScoreSecurityLive` use case.
- Adapters: `fin_langgraph` (`LangGraphScoringEngine`; nodes are thin wrappers over the domain functions) and `fin_config.load_scoring_config`/`YamlScoringConfig` (validates against the registry at load time). `Container` gained `scoring_engine`, `scoring_config` and `score_security_live`.
- Deleted from `fin_core`: `graph/`, `metrics/`, `models.py`. `fin_core/scoring.py` stays as a bridge (`score_security(session, ...)`, `run_state`, `metrics_for`) for the signals engine and the HTTP app, which still work inside one SQL session; it goes in step 4. `facts_as_of` (the SQL form of the point-in-time rule) is deleted: its scenario test now runs through `SqlFactRepository` + `known_as_of`.
- The HTTP app no longer imports `fin_core.graph/models/metrics`; `/api/score` calls `fin_application.scoring.score` with the engine and thresholds from a `Container` (no adapter imports in the app). `/api/securities/{t}/score` still goes through the bridge.
- Deviations from the plan: typed config value objects (`PhilosophyConfig`, `GuardrailConfig`) were not introduced: scorers keep reading their thresholds from a plain mapping, with `validate_config` guarding the shape, because retyping 5 scorers' threshold access is a large change with no behaviour to show for it. The scorer test files that need the shipped `philosophies.yml` (`test_lynch`, `test_fisher`, `test_buffett`, `test_greenblatt`, `test_guardrails`, `test_graph`, `test_philosophies`) stay in `packages/core/tests` with updated imports plus a test-only `graph_compat.py` that keeps the old `build_graph().invoke(...)`/`decide_node(...)` call shapes; they need a home that has YAML (planned: `packages/config/tests`) and move there with the final cleanup in step 6. `ScoreSecurityLive` has no production caller yet: the HTTP endpoint needs the stored baseline and hysteresis, which arrive with the signals repositories in step 4.
- Checked: ruff, 9 import-linter contracts, 661 tests on SQLite and 812 on real Postgres 16. The old and new API gave identical responses for 8 awkward `/api/score` bodies (ints for floats, date strings, unknown fields, bad dates, nested annual points, ...); the one difference is the internal error-type label for a `null` body (`model_attributes_type` -> `dataclass_type`), with the same 422 status and location. Engine parity: the LangGraph engine and the domain `assess()` give equal assessments for five cases (nothing known, scalars only, a strong company, a financial, a distressed company with a guardrail veto). In the rebuilt images, `ScoreSecurityLive` ran with the real engine and YAML against Postgres, and `/api/score` answered from the `fin-api` image.
- Ratchet: 22 ignored imports (20 into `fin_core`, 2 direct `sqlalchemy` in `fin_api`), down from 26.

**Step 4 (Signals & Alerts) is done.**
- Domain `signals`: the stored-score rules that used to hide in SQL (`ScoreEntry.actionable`, `buy_armed`/`sell_armed` hysteresis, `StoredSignal` lifecycle), the typed `ScoreDetails` (replaces the untyped `scores.details` blob), `AlertPolicy` (moved, no YAML, takes a `LastAlert` value), the `Alert` aggregate with its `Delivery` state machine (`claim`, `mark_sent`, `mark_failed`, `skip`; `attempts` is the version), `ChannelState` (mute window), and `compose_alert`.
- Application: ports `ScoreRepository`, `SignalRepository`, `AlertRepository`, `WatchlistRepository`, `ChannelStateRepository`, `BotCursorStore`, the read queries `OpportunityQuery` and `AlertFeedQuery`, `AlertChannel`, `AlertPolicySource`, `SingleInstanceGuard`; use cases `ScoreUniverse` (per-security transactions, idempotent), `ProcessSignals`, `DeliverDueAlerts`, `RunScoring`, `WatchlistService`, `ChannelService`, `AlertFeed`, `Opportunities`, `BotCommands`. `ScoreSecurityLive` gained `use_stored_baseline`, and the HTTP endpoint now calls it.
- Adapters: `fin_persistence.signals` (SQL repositories and queries; the claim is a compare-and-set UPDATE that raises `ConcurrencyConflict` for the loser), `score_codec` (JSON shape unchanged, tolerant of partial rows), `PgAdvisoryLeaderLock` (the `SingleInstanceGuard`), and `seed` (row-level helpers for tests and the demo seeder only); `fin_telegram` (client and channel moved with `git mv`; the bot rebuilt on `BotCommands`, so it no longer touches the database); `fin_config.load_alert_policy`; `fin_bootstrap.build_channels` and `Container` properties for every use case, `channels()`, `telegram_bot()` and `leader_lock`.
- Deleted: `fin_core/signals/`, `fin_core/alerts/`, `fin_core/db/signals_repo.py`, `fin_core/scoring_run.py`, `fin_core/scoring.py` (its remaining helpers are test support in `graph_compat.py`: only tests used them once the engine and the API moved). The API's alert feed, read-marking, opportunities, watchlist, channels and live-score endpoints, the worker, the scoring flow and the demo seeder all go through the container.
- Tests moved with their code: `test_signals`, `test_alerts`, `test_alert_delivery`, `test_scoring_run` became `packages/application/tests` scenarios on in-memory fakes (the full list of legacy scenario names was checked off one by one); `test_telegram` was split into `packages/telegram/tests` (client, channel, bot) and `packages/bootstrap/tests/test_channels.py` (the feature-flag guarantees); the 0003 migration test moved to the persistence tests. Two scenarios only a real database can prove were re-created against one: "no transaction is held open while a channel sends" (SQLite) and "concurrent senders deliver exactly once" (Postgres, 4 threads).
- Found by the contract suite: the schema requires every alert to belong to a signal (`signal_id` NOT NULL) but the domain allowed `None` (now required), and the in-memory watchlist accepted unknown tickers where SQL refuses them (the fake now enforces it). Found by the import contracts: the worker imported `FeatureFlags` straight from the `fin_config` adapter; `fin_bootstrap` now exports it. Decision revisited: the domain refuses naive datetimes, so the legacy "naive SQLite times are UTC" tolerance in `AlertPolicy` moved to the SQL mapper (`aware()`), pinned by the contract tests.
- Deviations from the plan: there is no `Watchlist` aggregate class (a set of tickers with add/remove needs a repository, not behaviour); the `/api/securities/{t}/scores` history endpoint and the securities search/price endpoints still query the ORM directly through the legacy shims (step 6), as does the sandbox (`SignalRecord`, step 5); `fin_core/db/{models,repo,session}.py`, `features.py` and `run_tracking.py` remain as shims for those.
- Checked: ruff, 9 import-linter contracts, 762 tests on SQLite and 886 on real Postgres 16, plus 3 more on Postgres for the delivery concurrency. **Equivalence with the original code:** the demo seeder (35 weeks of synthetic history: scoring, signals, alerts, deliveries, watchlist, the signal-driven and manual sandbox portfolios) was run on commit `34a2c4e` (before any refactoring, in a clean worktree) and on the new stack; all 11 tables are row-for-row identical (210 scores with their full details JSON, 8 signals, 7 alerts with their text, 7 deliveries, 10 orders, 70 snapshots, 2,772 price bars, 282 facts), ignoring only wall-clock timestamps and JSON key order. In the rebuilt images against Postgres: migrations reach 0004; a scoring run raises one signal and one alert, a rerun creates nothing, nothing stays pending; the worker starts with its channels and flags, a second worker exits with status 3 while the first holds the lock, a bad channel configuration exits with status 2, and SIGTERM stops it cleanly.
- Note on process: the earlier steps were committed by the repository owner as `a27b88c`, `83f9b30` and `2503f10`; the equivalence baseline is therefore `34a2c4e` (the last commit before any refactoring), not `HEAD`.
- Ratchet: 9 ignored imports (8 into `fin_core`, 1 direct `sqlalchemy` in `fin_api`), down from 22. What is left is the API's search/prices/scores-history/portfolio endpoints and the sandbox flow.

**Step 5 (Sandbox) is done.**
- Domain `sandbox`: `Portfolio` (aggregate owning its `Order`s: `open`, `place_order`, `cancel_order`, `holdings`, `valued_positions`, `positions_value`, `settle`, `apply_signals`, `snapshot`), `Order`/`Holding`/`PositionValue`/`PortfolioSnapshot`, `StrategyRules` (was `SandboxConfig`; the stored `strategy_config` JSON is byte-identical), the money arithmetic and limits, and `OrderError`. `fin_domain.sandbox` sits at the top of the context chain (it consumes `StoredSignal` from `signals` and `PriceBar` from `market_data`).
- **`PriceBook`**: the aggregate never reads storage. It states what it will need (`price_requests`: the latest close per ticker, the stored close of each past fill's bar, the first bar after each pending order's day); the application answers with narrow queries (`PriceRepository.close_at`, `first_bar_between`, `latest`) and passes the result back. A fresh book is built before each step (strategy, settle, snapshot), because what a step needs depends on what the previous one did.
- Application: `PortfolioRepository` and `SandboxConfigSource` ports; `SignalRepository.after`/`latest_id`; use cases `CreatePortfolio` (a strategy starts after the newest signal), `PlaceOrder`, `CancelOrder`, `RunSandbox` (own transaction per portfolio, row lock via `load_exclusive`, failures isolated) and `PortfolioReader` (views with totals, return and per-position P&L).
- Adapters: `SqlPortfolioRepository` (loads a portfolio with its orders, remembers each order's state and writes only the ones that changed, so a long-lived portfolio does not rewrite every order on every run), `fin_config.YamlSandboxConfig`, and `Container` properties for each use case. The API's seven portfolio endpoints, the `sandbox-step` flow and the demo seeder go through the container.
- Deleted: `fin_core/sandbox/` (engine, config, run), `fin_core/run_tracking.py` (its last two callers were scoring and sandbox, both migrated), and `packages/core/tests/test_sandbox.py`.
- Tests moved with their code: the 33 legacy scenarios became 42 pure rule tests (`packages/domain/tests/test_sandbox_rules.py`, with the exact numbers: fees, rounding, 2:1 and 1:5 splits, sells before buys on the same close), 25 use-case scenarios on in-memory fakes, a contract suite for the repository on fakes, SQLite and Postgres, and the 0004 migration round trip. "Overlapping runs do not duplicate strategy orders", which only real row locks can prove, runs against Postgres with two threads.
- Found by the contract suite: the in-memory portfolio repository accepted an order for an unknown security where the real one refuses it (the fake now enforces it). Found by a test: the reader values positions at the clock's "now", so a position whose bars are all in the clock's future is valued at cost, which is the designed fallback.
- Deviations from the plan: the API's own response schemas still convert Decimals to floats (presentation, unchanged); the `NotFound` entity/key pair is turned back into the legacy `unknown portfolio 7` / `unknown ticker X` messages in the API.
- Checked: ruff, 9 import-linter contracts, 819 tests on SQLite and 924 on real Postgres 16. **Equivalence with the original code (`34a2c4e`), now including the sandbox:** the demo seeder (35 weeks: a signal-driven portfolio and a manual one, orders, fills, fees, snapshots) gives row-for-row identical results in all 12 tables compared (10 sandbox orders with fill times, quantities, prices and fees; 70 snapshots; both portfolios' cash, signal cursor and strategy config; plus scores, signals, alerts, deliveries, prices, facts and 70 job runs). In the rebuilt images against Postgres, a strategy portfolio bought on a signal and a manual one filled its order over four scheduled steps (strategy $104, manual $110, history of 4 points each), and the API returned them with the legacy 404 text.
- Ratchet: 6 ignored imports (5 into `fin_core`, 1 direct `sqlalchemy`), down from 9: all in `fin_api.main` (securities search, price history, score history, the version, the feature flags). Step 6 clears them.

**Step 6 (apps and cleanup) is done. The redesign is complete.**
- `fin_api.main` is container-only: no `sqlalchemy`, no `fin_persistence`, no `fin_core`. Its routes call use cases and query ports (`SecurityCatalog`, `PortfolioReader`, `AlertFeed`, `Opportunities`, `ScoreSecurityLive`); `StorageUnavailable` maps to 503.
- The worker moved to `apps/worker` (`fin_worker`, `python -m fin_worker`) with its own image target; exit codes are unchanged (2 invalid channel config, 3 lost or contested lock). Migrations run through `python -m fin_bootstrap.migrate`. Both compose files, `ci.yml` and `scripts/smoke-images.sh` follow, and `flows/tests/test_compose.py` now checks the worker and migrate entry points.
- Deleted: `packages/core` (`fin_core`) with all shims. The import-linter ratchets are gone: 8 contracts, no ignored imports. The scorer tests moved to `packages/bootstrap/tests/scoring` (they need the DB helpers).
- Mistakes found on the way: the `fin_langgraph` tests were never in `testpaths` (and one distressed-company fixture did not veto; fixed). `packages/bootstrap/tests/test_repository_layout.py` now fails if a package's tests are missing from `testpaths` or a listed path does not exist.
- Checked: ruff and format clean; 8 contracts kept; 847 tests passed on SQLite and the whole suite on Postgres 16; the core's 294 tests pass in an environment with only `fin-application` and the test kit (no SQLAlchemy installed). Planting `import sqlalchemy` in `fin_domain` makes both `lint-imports` and the isolation test fail. **Equivalence with `34a2c4e`:** the demo seeder gives identical rows in all 14 tables (only wall-clock timestamps differ). Images `fin-api`, `fin-flows`, `fin-worker` build; migrations reach `0004 (head)`; a second worker exits 3, a bad channel configuration exits 2, SIGTERM stops it cleanly; both compose files validate (the prod one needs `POSTGRES_PASSWORD`).
- Found by the Postgres run: the catalog search expectation assumed binary ordering, but Postgres orders `A-B` and `AAPL` by locale collation. The query is unchanged from the original (same behaviour); the test now accepts either order for that pair.
- Docs: `docs/architecture/02-repository-map.md` rewritten, `10-hexagonal-architecture.md` added, diagrams 01/05/06/07/08 updated, `CLAUDE.md` and `README.md` describe the new layout.

