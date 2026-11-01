# 007: Read APIs for the UI

- **Phase / PR:** Phase 4 groundwork, [#16](https://github.com/festei/fin_app/pull/16) (commits `33e7dc0`, review fixes `187dc28`)
- **Status:** Delivered

## Context

The UI's screener and security pages needed data the API did not expose: searching the universe, price
history for charts, and the stored score history.

## Goals / Non-goals

Goals: three small read-only endpoints, no schema change. Non-goals: any write path, bond data.

## Decisions

- **`GET /api/securities?q=&sector=&kind=&limit=`**: active securities by ticker prefix or name
  fragment, optional sector and kind (`stock`/`etf`), flags the watchlist.
- **`GET /api/securities/{ticker}/prices?days=&max_points=`**: daily bars oldest first; beyond
  `max_points` the bars are merged into candles from the newest bar (first open, max high, min low,
  last close, summed volume) so candlestick and band charts keep the true extremes.
- **`GET /api/securities/{ticker}/scores?days=&limit=`**: stored scores and the signals they
  produced; blocked and all-abstained runs are included but flagged, with a null conviction when
  nothing could be evaluated, so a gap shows as a gap, never a 0. Plot against `as_of`.
- 404 unknown ticker, 422 out-of-range parameters, 503 without a database; reads are open.

## Acceptance criteria and verification

437 tests on SQLite; the API suite also on Postgres 16. Tests cover prefix, name, sector, kind and
limit search, wildcard handling, window and ordering, candle aggregation, flagged gaps, signals and
404/422/503.

## Review outcomes (commit `187dc28`)

| Review finding | Change |
| --- | --- |
| Search stripped `%` and `_` while claiming to match them literally; backslash broke `LIKE` | Proper escaping (`escape="\\"`); matches literally; checked on real Postgres. |
| Thinning dropped true highs and lows | Aggregate into candles instead. |
| Signal markers could fall outside the returned score window | Signals limited to the window. |
| `kind` unvalidated; `as_of` versus `computed_at` unclear; inactive securities unexplained | `Literal["stock","etf"]`; documented; documented and tested. |
| Time-of-day-dependent tests | Bars stamped relative to "now". |

## Known limitations

Reads are open (the UI PR adds the proxy auth); Yahoo-sourced prices are personal-use data.
