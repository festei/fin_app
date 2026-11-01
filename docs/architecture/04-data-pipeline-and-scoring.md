# 04: Data pipeline and scoring

## The daily pipeline

One scheduled Prefect deployment, `daily-pipeline`, runs weekdays at 21:30 UTC (after the US close).
Each step is also deployable by hand. A failing step does not stop the later ones (scoring copes with
stale data through its blockers); the flow fails at the end naming the failed steps.

```mermaid
flowchart LR
  s1["sync-universe<br/>config/universe.yml to securities"] --> s2["ingest-prices<br/>Yahoo, back-fill then refresh"]
  s2 --> s3["ingest-yields<br/>FRED, incremental with overlap"]
  s3 --> s4["ingest-fundamentals<br/>EDGAR company facts"]
  s4 --> s5["score-universe<br/>scores, signals, alerts, deliveries"]
  s5 --> s6["sandbox-step<br/>strategy orders, fills, snapshots"]
```

Every job writes an `ingestion_runs` row. Statuses: a run is `failed` until it completes normally;
one bad ticker or series makes it `partial`; a rate-limit or access-denied response aborts it
(`failed`); stale `running` rows are failed by the next run.

```mermaid
sequenceDiagram
  autonumber
  participant F as Prefect flow
  participant J as ingest job
  participant P as Provider (EDGAR, FRED, Yahoo)
  participant D as Postgres
  F->>J: run (tickers or all)
  J->>D: insert ingestion_runs (status running)
  loop each ticker or series
    J->>P: fetch
    alt provider or DB error for this item
      J-->>J: record in failed map, continue
    else rate limit or access denied
      J-->>J: abort the run
    else ok
      J->>D: idempotent upsert (on conflict do nothing)
    end
  end
  J->>D: finish run: ok, partial or failed
  J-->>F: RunSummary (flow raises only on total failure)
```

## From facts to metrics (point in time)

```mermaid
flowchart TB
  facts[("fundamental_facts<br/>every filed version")] --> asof["facts_as_of(as_of)<br/>only what was public then"]
  prices[("price_bars")] --> lastbar["latest bar at or before as_of"]
  asof --> annual["annual_series<br/>10-K family, 1-year flows,<br/>balance-sheet instants,<br/>tag priority per fiscal year,<br/>TotalDebt, single-source share counts"]
  annual --> ratios["ratios from the SAME fiscal year<br/>P/E, P/B, current ratio, debt/equity,<br/>ROE, dividend streak, EPS growth"]
  lastbar --> ratios
  ratios --> sm["SecurityMetrics<br/>None for missing data +<br/>warnings + signal_blockers"]
```

`signal_blockers` (no or stale price, no or stale fundamentals) make the graph withhold buy and sell
signals while still reporting the score. Multi-class issuers (for example BRK-B) get no book value
or P/B.

## The scoring graph (LangGraph)

```mermaid
flowchart LR
  START(("start")) --> g["graham"]
  START --> b["buffett"]
  START --> l["lynch"]
  START --> gb["greenblatt"]
  START --> f["fisher"]
  g --> gr["guardrails<br/>Piotroski F-score,<br/>Altman Z''"]
  b --> gr
  l --> gr
  gb --> gr
  f --> gr
  gr --> ag["aggregate<br/>weighted mean of the<br/>philosophies that evaluated;<br/>veto caps below the sell line"]
  ag --> de["decide<br/>signal only on a transition<br/>vs previous_conviction,<br/>gated by hysteresis flags"]
  de --> END(("end"))
```

| Node | What it does | Abstains when |
| --- | --- | --- |
| `graham` | P/E, P/B, P/E×P/B, current ratio, debt/equity, dividend years, earnings growth. | Never abstains; non-positive P/E, P/B and negative debt/equity fail explicitly. |
| `buffett` | Durable ROE, operating margins, positive earnings, debt payback, capex intensity, margin of safety on owner earnings. | Fewer than 5 fiscal years; Financials. |
| `lynch` | Category by EPS growth (fast grower, stalwart, slow grower), dividend-adjusted PEG. | Cyclicals and turnarounds; Financials. |
| `greenblatt` | Earnings yield and return on capital against **absolute** thresholds. | Financials, Utilities, multi-class issuers, no operating income, no price or share count. |
| `fisher` | Quantifiable subset of his 15 points (growth, margins and trend, R&D intensity, dilution). Missing R&D fails as "no data". | Too little revenue history. |
| `guardrails` | Piotroski and Altman **cap** conviction below the sell line; they never add to it. A distress zone is a note, not a veto, for visibly profitable, cash-generative firms. | Financials, or too few evaluable signals. |

All five philosophies have equal weight (`config/philosophies.yml`). A node returns the same
`ScoreResult` contract whatever implements it, so a deterministic node can later be replaced by an
LLM-agent node (`impl: llm`) without touching this wiring.

**If every node abstains, conviction is `None` (not 0.0) and no signal is emitted**, so missing data
can never look like a collapse and trigger a sell.

### Graph state

```mermaid
flowchart TB
  in["inputs<br/>metrics, config,<br/>previous_conviction,<br/>buy_armed, sell_armed"] --> st["philosophy_results (merge reducer)"]
  st --> gs["guardrail_veto, guardrail_results,<br/>guardrail_reasons, guardrail_notes"]
  gs --> out["conviction (None = no view),<br/>all_abstained, explanation, signal"]
```

## Two ways to score

```mermaid
flowchart LR
  subgraph live["On demand: GET /api/securities/{ticker}/score"]
    a1["compute_metrics"] --> a2["graph"] --> a3["stores nothing<br/>baseline from stored scores<br/>(today, no override)"]
  end
  subgraph stored["Scheduled: signals.engine.score_and_record"]
    b1["compute_metrics"] --> b2["graph"] --> b3["insert scores row<br/>insert signals row if any"]
  end
```

The scheduled path is the system of record; the live path is a read-only preview that uses the same
baseline and hysteresis, so it only reports a signal when a transition has happened since the last
stored score.
