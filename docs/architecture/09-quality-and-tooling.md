# 09: Quality and tooling

## Test layers

```mermaid
flowchart TB
  subgraph unit["Fast, no external services"]
    u1["scorers and graph<br/>hand-computed expectations"]
    u2["metrics layer<br/>point-in-time rules"]
    u3["sandbox engine<br/>fills, splits, strategy"]
    u4["alerts policy, delivery, Telegram (mocked API)"]
    u5["web: vitest + Testing Library<br/>charts mocked"]
  end
  subgraph db["Database"]
    d1["SQLite: always"]
    d2["Postgres 16: when TEST_DATABASE_URL is set<br/>(locks, concurrency, migrations)"]
  end
  subgraph sys["System-level"]
    s1["API tests (TestClient)"]
    s2["worker as real processes + advisory lock"]
    s3["nginx template against a real nginx<br/>(skipped if not installed)"]
    s4["compose guard tests"]
  end
  live["live contract tests (-m live):<br/>real SEC and FRED. Opt-in, not run in the sandbox."]
```

Postgres variants are parametrised through the `raw_engine` fixture (`postgres` marker). SQLite does not
enforce column types or lengths, so DB-touching changes must also run on Postgres.

## Local commands (CI is disabled)

```sh
uv run ruff check . && uv run ruff format --check .
uv run pytest -q
TEST_DATABASE_URL=postgresql+psycopg://user@localhost:5432/fin_test uv run pytest
(cd apps/web && pnpm typecheck && pnpm test && pnpm build)
docker compose -f deploy/docker-compose.yml config -q
scripts/smoke-images.sh          # needs Docker
```

## Review loop used for every PR

```mermaid
flowchart LR
  spec["spec / plan"] --> impl["implement on a branch"]
  impl --> chk["ruff + pytest (+ Postgres) + web checks"]
  chk --> pr["pull request<br/>with 'not verified' section"]
  pr --> rev["owner review"]
  rev --> fix["fix on the same branch,<br/>no force-push"]
  fix --> reply["one PR comment mapping each finding<br/>to fixed / deferred / acknowledged"]
  reply --> merge["owner merges"]
```

## CI/CD (deferred)

`.github/workflows/ci.yml` (path-filtered lint, tests, image build, non-root check, Trivy, compose
validation) and `release.yml` (Commitizen release) exist but are manual-only. The TODO list at the top
of `ci.yml` names what to fix before re-enabling: an unresolvable Trivy action version, double triggers
per push, and missing path filters.
