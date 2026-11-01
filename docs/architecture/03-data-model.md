# 03: Data model

Fifteen tables in four groups, created by four hand-written Alembic migrations
(`0001` data layer, `0002` signals and alerts, `0003` delivery backoff and signal processing,
`0004` sandbox). The models must stay in sync with the migrations (a test asserts it). Code runs on
Postgres (production) and SQLite (fast tests); JSON columns are JSONB on Postgres.

## Entity relationships

```mermaid
erDiagram
  securities ||--o{ price_bars : "daily bars"
  securities ||--o{ fundamental_facts : "filed facts"
  securities ||--o{ scores : "every scoring run"
  securities ||--o{ signals : "transitions"
  securities ||--o{ alerts : ""
  securities ||--o| watchlist : "followed"
  scores ||--o{ signals : "caused by"
  signals ||--o| alerts : "at most one"
  alerts ||--o{ alert_deliveries : "per channel"
  portfolios ||--o{ sandbox_orders : ""
  portfolios ||--o{ portfolio_snapshots : "one per day"
  securities ||--o{ sandbox_orders : ""
  signals |o--o{ sandbox_orders : "signal_id (strategy trades)"

  securities {
    int id PK
    string ticker UK
    string cik
    string name
    string kind "stock or etf"
    string sector
    bool active
  }
  price_bars {
    int security_id PK
    datetime ts PK
    float open
    float high
    float low
    float close
    int volume
  }
  fundamental_facts {
    int id PK
    int security_id FK
    string concept
    string tag
    string unit
    date period_start
    date period_end
    bool instant
    int fiscal_year
    date filed "point in time key"
    string accession
    decimal value
  }
  bond_yields {
    string series_id PK
    date obs_date PK
    decimal value
  }
  ingestion_runs {
    int id PK
    string source "edgar, fred, prices, scoring, sandbox"
    string status "running, ok, partial, failed"
    int rows
    string error
  }
  scores {
    int id PK
    int security_id FK
    datetime as_of
    datetime computed_at
    float conviction "NULL means no view"
    bool all_abstained
    bool signal_blocked
    json details
  }
  signals {
    int id PK
    int security_id FK
    int score_id FK
    string kind "buy, sell, emerging"
    float conviction
    datetime processed_at
    string suppressed_reason
  }
  alerts {
    int id PK
    int security_id FK
    int signal_id FK
    string kind
    string severity "high or normal"
    string title
    string body
    datetime read_at
  }
  alert_deliveries {
    int id PK
    int alert_id FK
    string channel "in_app, telegram"
    string status "pending, sent, failed, skipped"
    int attempts
    datetime last_attempt_at
  }
  watchlist {
    int security_id PK
    datetime added_at
  }
  channel_state {
    string channel PK
    datetime muted_until
    int update_cursor "Telegram offset"
  }
  portfolios {
    int id PK
    string name
    string mode "manual or strategy"
    decimal initial_cash
    decimal cash
    decimal fee_bps
    json strategy_config
    int last_signal_id
  }
  sandbox_orders {
    int id PK
    int portfolio_id FK
    int security_id FK
    string side
    decimal quantity
    decimal notional
    string status "pending, filled, rejected, cancelled"
    datetime created_at "decision time"
    int signal_id
    datetime filled_at "bar timestamp"
    decimal fill_price
  }
  portfolio_snapshots {
    int portfolio_id PK
    date as_of PK
    decimal cash
    decimal positions_value
    decimal total_value
  }
```

`bond_yields`, `ingestion_runs` and `channel_state` have no foreign keys. Holdings are **not** a
table: they are derived from filled orders (see [06](06-sandbox.md)).

## Rules that shape the schema

| Rule | Where it shows up |
| --- | --- |
| **Point in time.** `fundamental_facts` keeps *every filed version* of a fact with its `filed` date. `repo.facts_as_of(as_of)` returns only what was public then, so a restatement is invisible until it was filed. | `fundamental_facts.filed` |
| **No NULLs in natural keys.** Instant facts (balance-sheet items) store `period_start == period_end`. | de-duplication and idempotent upserts |
| **Append-only history.** Every scoring run is kept; nothing is overwritten. | `scores` (retention policy is on the backlog) |
| **No view is not zero.** A run where nothing could be evaluated stores `conviction = NULL`. | `scores.conviction` |
| **Baselines skip bad runs.** The next transition is judged against the last score with a conviction that was not computed on stale or missing data. | `scores.signal_blocked`, `signals_repo.latest_actionable_score` |
| **Signals are processed exactly once.** `processed_at` marks a decided signal; `suppressed_reason` records a deliberate "no alert". | `signals` |
| **One alert per signal, one delivery per channel.** Unique `signal_id` on alerts; unique `(alert_id, channel)` on deliveries. | idempotent delivery |
| **A channel enabled later never replays history.** Delivery rows exist only for channels registered when the alert was created. | `alert_deliveries` |
| **Prices are adjusted, not as-traded.** The source re-adjusts old bars after a split; ingestion re-fetches the whole history if it detects it. | `price_bars` |
| **TimescaleDB is optional.** `price_bars` becomes a hypertable only when the extension exists. | migration `0001` |
| **UTF-8 database.** SQL_ASCII makes psycopg 3 return bytes. | deployment |
