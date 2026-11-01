# 005: Signals and alerts

- **Phase / PR:** Phase 3, [#14](https://github.com/festei/fin_app/pull/14) (commits `13d2c6d`, `40225c0`, `c4bc364`, review fixes `28135f3`)
- **Status:** Delivered, except the intraday fast loop

## Context

Scores existed only as a transient API response. The product needs a history, signals on
*transitions*, and alerts delivered to an always-on in-app feed and an optional Telegram channel.

## Goals / Non-goals

Goals: persisted scores and signals, an alert policy, channel-independent delivery with retries,
the in-app feed, a feature-flagged Telegram channel and bot, a scheduled scoring run, an always-on
worker. Non-goals: intraday polling and incremental re-scoring, quiet hours, daily digests.

## Decisions

- **Scoring is a persisted state machine.** Every run is stored in `scores` (append-only, JSON
  details). `previous_conviction` is the last **actionable** stored score: conviction set AND not
  computed on blocked (stale or missing) data. A blocked run is stored but never becomes the
  baseline (otherwise the buy would be swallowed when fresh data arrives); a NULL-conviction run is
  not a baseline either, so the next real score is `emerging`, not `buy`. Re-running with unchanged
  data is idempotent.
- **Alert policy (`config/alerts.yml`):** sells only for watchlist stocks; a 24 h per-kind cooldown
  overridden by a conviction move of 10 or more points; watchlist stocks are high severity.
- **Delivery is channel-independent.** Creating an alert also writes its `alert_deliveries` rows in
  the same transaction (in-app `sent`, a muted channel `skipped`, others `pending`), so a channel
  enabled later never replays old alerts. Sending happens after commit, outside any DB transaction.
- **Telegram** (`alerts/telegram.py`, plain `httpx`): a channel plus a command bot (`/watch`,
  `/unwatch`, `/watchlist`, `/top`, `/why`, `/mute`, `/unmute`, `/help`) that answers only the
  configured chat. Flag off: no client, no token, zero calls. Flag on without
  `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`: startup fails loudly. The bot token is in every request
  URL, so it is kept out of errors and logs.
- **Scheduling:** `run_scoring` (score universe, alerts, delivery pass) is tracked in
  `ingestion_runs`; the worker holds a Postgres advisory lock (one instance), validates channels at
  startup, runs the bot only when enabled, and retries deliveries.
- **API:** `/api/alerts` (cursor, unread filter), `POST /api/alerts/{id}/read`,
  `/api/opportunities`, `/api/watchlist`, `/api/channels`; 503 without or with an unreachable DB.
- Migration `0002` adds `scores`, `signals`, `alerts`, `alert_deliveries`, `watchlist`, `channel_state`.

## Design

Diagrams: [architecture/05-signals-and-alerts](../architecture/05-signals-and-alerts.md).

## Acceptance criteria and verification

424 tests on SQLite and Postgres 16. A seeded company whose conviction is steered by price drives
hand-checkable transition tests (emerging, sell, buy, idempotent re-run, stale baseline, NULL
baseline). The worker was exercised as real processes against Postgres: Telegram on without
credentials exits 2; a second worker exits 3 while the first holds the lock; SIGTERM stops the first
cleanly and releases the lock.

## Review outcomes (commit `28135f3`)

| Review finding | Change |
| --- | --- |
| A failing `create_alert` silently loses the remaining alerts | Per-signal transaction and error handling; signals carry `processed_at`/`suppressed_reason`; `run_scoring` sweeps unprocessed signals without re-alerting suppressed ones; migration 0003 backfills. |
| Retries had no backoff; two processes could send the same delivery; HTTP inside a DB transaction | Backoff schedule and attempt cap, atomic compare-and-set claim, send after commit, stale `pending` recovery, muted-at-due-time becomes `skipped`. |
| Telegram polling could replay commands | Offset advanced and persisted before each command runs (at-most-once); reply errors are caught. |
| State-changing endpoints unauthenticated | Bearer `API_TOKEN`, default-deny (503 when unset, 401 when wrong). |
| No hysteresis near thresholds | Re-arm below 65 (buy) and at 55 or above (sell), derived from stored scores. |
| Leader lock could be lost silently | `LeaderLock` re-verifies via `pg_locks`, re-acquires, exits 3 if taken. |
| Schedules not chained | One `daily-pipeline` flow runs ingestion, then scoring. |
| `/api/opportunities` ignored age | `max_age_days` (default 10). |
| `scores` growth | Deferred; documented as backlog. |

## Known limitations

Telegram has only run against a mocked Bot API (the sandbox cannot reach Telegram). No intraday loop.
Thresholds and the alert policy are reasoned starting points.
