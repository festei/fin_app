# 006: Paper-trading sandbox

- **Phase / PR:** Phase 5, [#15](https://github.com/festei/fin_app/pull/15) (commits `0f78a66`, review fixes `0232e62`)
- **Status:** Delivered

## Context

The owner wanted to see "what happens to a small portfolio (say $100) if it follows the signals",
and to practise by hand. The plan came before the UI because the UI's portfolio screens need it.

## Goals / Non-goals

Goals: simulated portfolios in two modes (`manual`, `strategy`), honest fills, an equity curve, an
API, scheduled processing. Non-goals: benchmark comparison, shorting, limit orders, cash interest,
intraday execution.

## Decisions

- **Two modes, one or the other per portfolio.** `manual` takes orders through the API;
  `strategy` turns signals into orders.
- **Fill model with no look-ahead.** An order decided on day D fills at the close of the first
  stored daily bar on a **later** day, at the source's adjusted price. Cash and holdings are checked
  at fill time; an order that cannot be afforded or covered is rejected with a reason. Fractional
  shares, optional `fee_bps`.
- **Strategy rules** (`config/sandbox.yml`): buy/emerging spends `position_pct` of total value
  subject to `max_positions`, `min_cash_pct` and cash already committed to pending buys; sell closes
  the position and cancels a pending buy of that stock. A strategy only acts on signals created
  **after** the portfolio existed and looks at each signal once (`portfolios.last_signal_id`).
- **Attribution groundwork:** `sandbox_orders.signal_id` links a trade to the signal and score that
  caused it, for the later backtester and tuning.
- **Equity curve:** one `portfolio_snapshots` row per portfolio and day, idempotent.
- **Scheduling:** a `sandbox-step` flow is the last step of the daily pipeline; each portfolio runs
  in its own transaction; tracked in `ingestion_runs` (source `sandbox`).
- **API:** `GET/POST /api/portfolios`, `/{id}` (positions with market value and P&L), `/history`,
  `/orders`, `POST`/`DELETE` orders; writes need the API token.

## Design

Diagrams: [architecture/06-sandbox](../architecture/06-sandbox.md). Migration `0004`.

## Acceptance criteria and verification

531 tests on SQLite and Postgres 16 with hand-checkable prices: an order never fills at its own
day's close, fill price/quantity/cash/average cost, fees, rejections, partial and full sells,
strategy limits and duplicates, sell cancelling a pending buy, ignoring historical signals, snapshot
idempotency, failure isolation, API validation and auth, the migration round trip.

## Review outcomes (commit `0232e62`)

| Review finding | Change |
| --- | --- |
| A stock split showed as a phantom loss (fixed quantity, re-adjusted prices) | **Holdings are derived, not stored**: each fill is restated to today's share basis by `fill price / current stored close of its bar`; a 2:1 split is neutral and dividends appear as slightly more shares. `sandbox_positions` was dropped (migration 0004 was still unmerged). |
| Overlapping runs could duplicate strategy orders | Row lock per portfolio (`lock_portfolio`, `FOR UPDATE` on Postgres); a two-thread Postgres test. |
| NaN/Infinity and oversized inputs; bad `buy_kinds` | Bounded, finite-checked amounts; `buy_kinds` restricted to buy/emerging. This also exposed that FastAPI's 422 body echoed `Infinity` and failed to encode (a 500): a custom validation handler omits the input. |
| Buy rejected for cash although a same-day sell would fund it | Sells settle before buys on the same close. |
| `avg_cost` ignored fees | Buy fees are part of the cost basis. |
| A skipped buy is never reconsidered; cancelling a strategy order does not re-place it | Documented as deliberate. |

## Known limitations

Daily adjusted prices only; no intraday execution; no benchmark; simulation only, not investment advice.
