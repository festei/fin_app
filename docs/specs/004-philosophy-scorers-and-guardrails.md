# 004: Philosophy scorers and guardrails

- **Phase / PR:** Phase 2, [#13](https://github.com/festei/fin_app/pull/13) (commits `b2be1c5`, `b924221`, `23eb2d3`, review fixes `d02da45`)
- **Status:** Delivered

## Context

The graph had one philosophy (Graham) and a stub guardrail. The goal was the other four
philosophies plus quality guardrails, all behind the same `ScoreResult` contract.

## Goals / Non-goals

Goals: Buffett, Lynch, Greenblatt and Fisher nodes; Piotroski and Altman guardrails; equal weights.
Non-goals: Beneish (needs receivables, SG&A, PP&E), bond scorers, threshold calibration.

## Decisions

- **Abstention (`ScoreResult.evaluated=False`).** A philosophy that cannot be evaluated (too little
  history, excluded sector, ...) is left out of the weighted mean instead of counting a 0, and is
  named in the explanation. If **every** node abstains the conviction is `None` (not 0.0) and no
  signal is emitted, so missing data can never look like a collapse and trigger a sell.
- **Buffett:** durable ROE, operating margin level and floor, consistently positive earnings, debt
  repayable from earnings, capital intensity, and a margin of safety on owner earnings (net income
  + D&A − capex, capitalised at a configurable rate; a deliberately simple earnings-power value,
  not a forecast, and strict).
- **Guardrails do not add to conviction; they cap it** below the sell threshold (never producing a
  buy) when the Piotroski F-score share is at or below the veto level, or Altman Z'' is in the
  distress zone, with reasons in the explanation. Z'' distress is overridden by a note when the
  company is visibly profitable and cash-generative (buyback-heavy firms such as McDonald's show
  negative equity). Financials are skipped; signals without data are not counted against a company.
- **Lynch:** categorises by smoothed EPS growth (fast grower / stalwart / slow grower), applies
  category-specific criteria with a dividend-adjusted PEG; cyclicals and turnarounds abstain.
- **Greenblatt:** earnings yield and return on capital against **absolute thresholds per stock**,
  not the cross-sectional ranking (that needs stored scores for the whole universe); abstains for
  Financials/Utilities and multi-class issuers.
- **Fisher:** the quantifiable subset of his 15 points; missing R&D fails as "no data".
- **Equal weights** in `config/philosophies.yml`; a test keeps the node registry and config in sync.
- **New concepts** (R&D, inventory, net PP&E, current debt) require a fundamentals re-ingestion;
  a test enforces that every mapped concept is classified in `annual_series`.

## Design

See the scoring graph in [architecture/04](../architecture/04-data-pipeline-and-scoring.md). To add a
philosophy: a module in `graph/nodes/`, register it in `PHILOSOPHY_NODES`, add thresholds and a
weight to `config/philosophies.yml`.

## Acceptance criteria and verification

283 tests pass on SQLite and real Postgres 16. Each scorer has hand-computed expectations on
synthetic companies (a fast grower with PEG 0.80, Greenblatt EV 1,150 giving a 17.4% yield, share
dilution 2.96%/yr, Altman Z'' 4.35 versus −2.84), abstention and "no data" paths, a five-philosophy
graph test, and end-to-end tests from stored facts.

## Review outcomes

Commit `d02da45`: Altman corroboration, **one debt definition** (`TotalDebt`, used by every
consumer), and "no view" conviction semantics.

## Known limitations

Thresholds in `config/philosophies.yml` are reasoned starting points, not calibrated. Fisher's R&D
rule lowers the score of companies that report no R&D (deliberate, but it biases an equal-weighted
composite). Beneish and bond scorers are not built.
