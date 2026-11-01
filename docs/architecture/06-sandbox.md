# 06: Paper-trading sandbox

A simulation: nothing here touches a broker. Two kinds of portfolio share one engine
(`fin_core/sandbox/engine.py`).

## Order lifecycle

```mermaid
stateDiagram-v2
  [*] --> pending: placed (manual) or created from a signal (strategy)
  pending --> filled: a bar on a LATER day exists and cash or holdings suffice
  pending --> rejected: at fill time: not enough cash, nothing to sell, or amount too small
  pending --> cancelled: cancelled by the user, or a sell signal supersedes a pending buy
  filled --> [*]
  rejected --> [*]
  cancelled --> [*]
```

## Fill model (no look-ahead)

```mermaid
flowchart LR
  d["order decided on day D<br/>(created_at)"] --> need["needs a stored daily bar<br/>on a day AFTER D"]
  need --> bar["first such bar at or before the step's as_of"]
  bar --> fill["fill at that bar's close<br/>(adjusted price)"]
  d -. "never D's own close" .-> x["not allowed"]
```

Within one close, sells settle before buys, so a sale can fund a purchase. Cash and holdings are
checked **at fill time**. Fees (`fee_bps`) reduce cash and are part of the cost basis.

## The daily sandbox step

```mermaid
sequenceDiagram
  autonumber
  participant S as run_sandbox (flow sandbox-step)
  participant D as Postgres
  participant E as sandbox.engine
  S->>D: list portfolios
  loop each portfolio (own transaction)
    S->>D: lock_portfolio (FOR UPDATE on Postgres)
    S->>E: run_strategy: new signals since last_signal_id become orders
    S->>E: settle: fill pending orders whose bar exists
    S->>E: snapshot: one value row for the day (idempotent)
  end
  S->>D: ingestion_runs (source sandbox): ok, partial or failed
```

## Strategy portfolios

```mermaid
flowchart TB
  sig["signal created AFTER the portfolio existed<br/>(each looked at once, last_signal_id advances)"] --> k{"kind"}
  k -->|"buy or emerging"| b1{"already held or buy pending?"}
  b1 -->|yes| s0["skip"]
  b1 -->|no| b2{"held + pending buys<br/>below max_positions?"}
  b2 -->|no| s0
  b2 -->|yes| b3{"has a price bar?"}
  b3 -->|no| s0
  b3 -->|yes| b4["notional = min(position_pct x total value,<br/>cash - pending buys - min_cash_pct x total value)"]
  b4 --> b5{"at least 0.01?"}
  b5 -->|no| s0
  b5 -->|yes| ob["create pending buy order<br/>(signal_id kept for attribution)"]
  k -->|sell| c1["cancel a pending buy of the stock"]
  c1 --> c2{"position held and<br/>no sell pending?"}
  c2 -->|yes| os["create pending sell order (whole position)"]
  c2 -->|no| s0
```

Deliberate rules: a skipped buy is **not** reconsidered (the signal was consumed), and cancelling a
strategy-created order does not re-place it. Defaults come from `config/sandbox.yml`:
`position_pct` 0.20, `max_positions` 5, `min_cash_pct` 0.05, `buy_kinds` buy and emerging, `fee_bps` 0.

## Holdings are derived, not stored

```mermaid
flowchart LR
  orders[("filled orders<br/>fill_quantity, fill_price,<br/>filled_at = bar timestamp")] --> restate["restate each fill to today's share basis:<br/>factor = fill price / current stored close<br/>of THAT bar"]
  bars[("price_bars<br/>re-adjusted by the source<br/>after splits and dividends")] --> restate
  restate --> avg["average-cost accounting<br/>(buy fees in the cost)"]
  avg --> holdings["quantity, cost, avg cost per share"]
```

The price source re-adjusts history after a corporate action (ingestion re-backfills), so the ratio is the
cumulative adjustment since the fill. With no corporate action the factor is exactly 1. A 2:1 split
turns 1 share at 100 into 2 shares at 50 (no phantom loss); dividends appear as slightly more shares,
like reinvested dividends.

## Known limits

Daily adjusted prices only; no intraday execution, shorting, limit orders, cash interest or benchmark.
Strategy P&L on the demo database is invented, because the demo prices are synthetic.
