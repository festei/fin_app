# 003: Point-in-time metrics layer

- **Phase / PR:** Phase 2 (start), [#12](https://github.com/festei/fin_app/pull/12) (commits `cdf3d02`, `fd4347e`, review fixes `2f2f10e`)
- **Status:** Delivered

## Context

The graph consumes a `SecurityMetrics` object. Something has to turn stored facts and prices into
that object using **only data public at the scoring date**, and be honest about gaps.

## Goals / Non-goals

Goals: `compute_metrics(session, ticker, as_of)`, a score-a-stored-ticker endpoint, explicit data
gaps, an initial `CLAUDE.md`. Non-goals: further scorers, persisted scores, the UI.

## Decisions

- **Facts selected by filing date** (`facts_as_of`) and the latest price bar `<= as_of`.
- **`annual.py` decides which facts count:** 10-K family only; flow concepts need a roughly
  one-year duration; stock concepts are balance-sheet instants; when a concept is reported under
  several XBRL tags the highest-priority tag in `CONCEPT_TAGS` wins per fiscal year.
- **Every ratio uses numerator and denominator from the same fiscal year**; an older value never
  leaks into a newer ratio (a warning says when a ratio had to use an older year); book value pairs
  equity with the share count on the same 10-K's cover.
- **Gaps are explicit.** Missing data stays `None` and is listed in `metrics.warnings`. Two
  documented exceptions, which also warn: missing debt is 0 only when that year's balance sheet
  exists, and no dividend data means 0 dividend years.
- **Loss-making and negative-equity companies keep negative ratios** so scorers can reject them
  explicitly.
- **`signal_blockers`** (no or stale price, no or stale fundamentals) make the graph's `decide` node
  withhold buy/sell signals while the score is still reported.
- **Multi-class issuers** (for example BRK-B) get no book value or P/B because EDGAR company facts
  have no class dimension; `normalize_facts` drops same-key facts with conflicting values.
- **More EDGAR concepts** for later scorers (gross profit, retained earnings, operating cash flow,
  capex, D&A); existing databases need a fresh fundamentals ingestion.
- **`score_security` = `compute_metrics` + graph**, exposed as `GET /api/securities/{ticker}/score`
  (404 unknown ticker, 503 without a database).

## Design

See [architecture/04](../architecture/04-data-pipeline-and-scoring.md).

## Acceptance criteria and verification

159 tests pass on SQLite and real Postgres 16. Metrics tests use hand-computed expectations (P/E 10,
P/B 2.5, a 6-year dividend streak, EPS growth 71.8%). Running on Postgres caught a test-data issue
SQLite hid (`accession` is VARCHAR(32)); that rule is recorded in `CLAUDE.md`.

## Review outcomes

Commit `2f2f10e`: consistent same-year ratios, explicit debt and dividend gaps, multi-class handling,
and stale-data handling (the `signal_blockers`).

## Known limitations

Adjusted prices are fine for current valuation but not for historical `as_of` dates after splits or
dividends (an as-traded price store is on the backlog).
