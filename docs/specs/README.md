# Specs

One spec per plan that was executed to build this repository, written **retrospectively**.

> **How these were written.** The planning text from the early sessions was not kept verbatim. Each
> spec is reconstructed from what survives: the pull request descriptions, commit messages, review
> threads, `CLAUDE.md`, the answers given to design questions, and (for specs 006 to 008) the plan
> file that was actually used. Where something is a recollection rather than a recorded decision
> the spec says "decision" only if it is visible in code, config or a PR. Nothing here was
> invented to look tidier; limitations that the PRs admitted are repeated.

## Index

| # | Spec | Phase | PR | Status |
| --- | --- | --- | --- | --- |
| 000 | [Vision, stack decisions and roadmap](000-vision-and-roadmap.md) | all | n/a | Roadmap; phases 0 to 5 delivered |
| 001 | [Monorepo foundations](001-monorepo-foundations.md) | 0 | [#10](https://github.com/festei/fin_app/pull/10) | Delivered |
| 002 | [Data layer and ingestion](002-data-layer-and-ingestion.md) | 1 | [#11](https://github.com/festei/fin_app/pull/11) | Delivered |
| 003 | [Point-in-time metrics layer](003-point-in-time-metrics.md) | 2 | [#12](https://github.com/festei/fin_app/pull/12) | Delivered |
| 004 | [Philosophy scorers and guardrails](004-philosophy-scorers-and-guardrails.md) | 2 | [#13](https://github.com/festei/fin_app/pull/13) | Delivered |
| 005 | [Signals and alerts](005-signals-and-alerts.md) | 3 | [#14](https://github.com/festei/fin_app/pull/14) | Delivered (no intraday loop) |
| 006 | [Paper-trading sandbox](006-paper-trading-sandbox.md) | 5 | [#15](https://github.com/festei/fin_app/pull/15) | Delivered |
| 007 | [Read APIs for the UI](007-ui-read-apis.md) | 4 | [#16](https://github.com/festei/fin_app/pull/16) | Delivered |
| 008 | [Trading UI](008-trading-ui.md) | 4 | [#17](https://github.com/festei/fin_app/pull/17) | Delivered |

Every PR went through at least one review round whose findings were fixed in a follow-up commit on
the same branch. Each spec ends with a **Review outcomes** section recording what changed.

## Not specified yet (backlog)

These were named in the roadmap or admitted as gaps in the PRs. None has a spec because none has
been planned in detail.

- **Intraday fast loop**: price polling every few minutes with incremental re-scoring (the worker
  only runs the Telegram bot, delivery retries and the single-instance lock today).
- **Phase 6**: bond scorers and bond screens, a point-in-time backtester and time-travel view,
  outcome-based threshold tuning (the sandbox's `signal_id` link prepares attribution).
- **Beneish guardrail** (needs receivables, SG&A and PP&E facts).
- **As-traded prices** (raw OHLC plus adjustment factors), required for honest historical backtests.
- **Live verification** of the EDGAR, FRED and Telegram integrations (only synthetic fixtures and a
  mocked Bot API exist; `-m live` tests are the opt-in check).
- **Threshold calibration** for `config/philosophies.yml` (Buffett's margin of safety first, then
  Fisher's R&D criterion).
- **`scores` retention** policy (the table is append-only).
- **Re-enabling CI** (workflows are manual-only; the TODO list sits at the top of `ci.yml`).
- **Image build verification** (no Docker daemon was available while building the UI; checks are in
  `scripts/smoke-images.sh`).
- **Phase 7 hardening**: real multi-user login, secrets management, observability.

## Template for a new spec

Copy this when planning the next piece of work. Keep it short; link to the code instead of
restating it.

```markdown
# NNN: Title

- **Phase / PR:** ...
- **Status:** Proposed | Accepted | Delivered | Superseded by NNN

## Context
Why now? What problem, what constraint, what was decided earlier that this builds on.

## Goals / Non-goals
## Decisions
Each with the reason, and the alternative that was rejected.
## Design
Components, data, interfaces. Diagrams belong in docs/architecture/.
## Acceptance criteria and verification
How we know it works; what was run; what was NOT verified.
## Review outcomes
Findings from review and what changed.
## Known limitations
```
