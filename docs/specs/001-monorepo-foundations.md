# 001: Monorepo foundations

- **Phase / PR:** Phase 0, [#10](https://github.com/festei/fin_app/pull/10) (commits `dc2477a`, `f4bf70e`, `c7641c2`)
- **Status:** Delivered

## Context

Two repositories (`fin_app` with a Streamlit stub, `fin_app_backend` with a Kedro pipeline) had to
become one codebase that could grow into the system described in [000](000-vision-and-roadmap.md).

## Goals / Non-goals

Goals: a single repository with a working skeleton of every layer (core library, API, flows, web,
containers) and one end-to-end path (score a company through the graph). Non-goals: real data
ingestion, more than one philosophy, persistence, a real UI, deployment.

## Decisions

- **Merge by subtree** so the backend history is preserved (`apps/api`), then delete the Kedro
  scaffolding, the committed `mlruns/` and the Streamlit stub.
- **`packages/core` (`fin_core`)** holds the contracts and logic shared by every app:
  pydantic `ScoreResult`, feature flags, the scoring graph and providers.
- **`uv` workspace** for Python (`packages/core`, `apps/api`, `flows`), `pnpm` for `apps/web`.
- **LangGraph scoring graph with one deterministic Graham node** and a `decide` node that emits a
  signal only on a *transition* of conviction across a threshold, never on every run.
- **Feature flags** from `config/features.yml`, overridable by `FEATURE_<NAME>` env vars; Telegram
  off by default.
- **FastAPI is capped below 0.137** because the Prefect version in the shared environment breaks on
  newer FastAPI (reason recorded in `apps/api/pyproject.toml`).
- **Providers ported from the old pipeline**, fixing a swapped balance-sheet/cash-flow output.
- **Containers prepared, not deployed:** one `python.Dockerfile` for all Python images
  (`--build-arg PACKAGE=...`, non-editable install), a web/nginx image, a dev compose file and a
  prod-shaped template.

## Design

See [architecture/02-repository-map](../architecture/02-repository-map.md) for the layout and
[architecture/04-data-pipeline-and-scoring](../architecture/04-data-pipeline-and-scoring.md) for the
graph.

- `fin_api`: `/healthz`, `/readyz`, `/api/features`, `/api/score`; a placeholder worker with clean
  SIGTERM shutdown.
- `flows`: a Prefect scoring flow.
- `apps/web`: Vite + React + TypeScript scaffold (one page, feature flags).

## Acceptance criteria and verification

- 13 Python tests, ruff check and format clean; web typecheck, 1 test and build pass.
- Both compose files validate with `docker compose config`.
- **Not verified:** Docker image builds (Docker Hub rate-limited the dev environment).

## Review outcomes

Commit `c7641c2` addressed the PR review and, at the owner's request, **disabled the CI/CD
workflows** (manual runs only) because everything is tested locally first. The path-filtered CI
workflow and a corrected release workflow (commits as `github-actions[bot]`) remain in the repository
for later.

## Known limitations

Imported backend commits keep their original authorship (GitHub may show them as Unverified).
