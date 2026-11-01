# 02: Repository map

## Layout

```mermaid
flowchart LR
  root["repository root"]
  root --> pkg["packages/core<br/>fin_core library"]
  root --> apps["apps/"]
  root --> flw["flows/<br/>fin_flows (Prefect)"]
  root --> cfg["config/*.yml<br/>thresholds, flags, universe"]
  root --> dep["deploy/<br/>Dockerfiles, compose, nginx"]
  root --> scr["scripts/<br/>seed_demo.py, smoke-images.sh"]
  root --> doc["docs/<br/>specs and architecture"]
  root --> gh[".github/workflows<br/>CI (manual only), release"]
  apps --> api["api/<br/>fin_api: FastAPI + worker"]
  apps --> web["web/<br/>React + Vite"]
```

## `fin_core` packages

```mermaid
flowchart TB
  subgraph core["packages/core/src/fin_core"]
    models["models.py<br/>ScoreResult, SecurityMetrics, Signal"]
    features["features.py<br/>feature flags"]
    universe["universe.py<br/>universe config + sync"]
    subgraph dbpkg["db/"]
      dbmodels["models.py<br/>SQLAlchemy tables"]
      repo["repo.py<br/>facts, prices, securities"]
      srepo["signals_repo.py<br/>scores, signals, alerts"]
      sess["session.py, migrate.py<br/>+ migrations/ (0001-0004)"]
    end
    subgraph prov["providers/"]
      edgar["edgar.py + edgar_concepts.py"]
      fredp["fred.py"]
      yf["yfinance_provider.py"]
      avp["alphavantage.py"]
    end
    ingest["ingest.py<br/>jobs + run tracking"]
    subgraph met["metrics/"]
      annual["annual.py<br/>fiscal-year series"]
      compute["compute.py<br/>compute_metrics (point in time)"]
    end
    subgraph gr["graph/"]
      build["build.py<br/>StateGraph"]
      nodes["nodes/<br/>graham, buffett, lynch,<br/>greenblatt, fisher"]
      guard["guardrails.py<br/>Piotroski, Altman"]
    end
    scoring["scoring.py<br/>score_security"]
    subgraph sig["signals/"]
      eng["engine.py<br/>score_and_record, score_universe"]
    end
    subgraph al["alerts/"]
      pol["policy.py"]
      pipe["pipeline.py<br/>create, send_due"]
      chan["channels.py, base.py"]
      tgm["telegram.py<br/>channel + bot"]
    end
    srun["scoring_run.py<br/>run_scoring"]
    subgraph sbx["sandbox/"]
      sbe["engine.py"]
      sbr["run.py"]
      sbc["config.py"]
    end
  end
  prov --> ingest
  ingest --> repo
  repo --> dbmodels
  srepo --> dbmodels
  compute --> repo
  compute --> annual
  scoring --> compute
  scoring --> build
  build --> nodes
  build --> guard
  eng --> scoring
  eng --> srepo
  srun --> eng
  srun --> pipe
  pipe --> pol
  pipe --> chan
  chan --> tgm
  sbe --> srepo
  sbe --> repo
  sbr --> sbe
```

## Who uses `fin_core`

```mermaid
flowchart LR
  core["fin_core"]
  api["fin_api.main<br/>REST endpoints"] --> core
  wk["fin_api.worker<br/>retries, bot, leader lock"] --> core
  fl["fin_flows<br/>ingestion, scoring,<br/>sandbox, pipeline, serve"] --> core
  seed["scripts/seed_demo.py"] --> core
  web["apps/web<br/>no Python: talks HTTP to fin_api"] -.-> api
```

The rule behind the picture: **logic lives in `fin_core`; the apps only wire it to a transport**
(HTTP, Prefect, a long-running loop). `fin_flows` and `fin_api` never import each other.

## Configuration files

| File | Controls | Read by |
| --- | --- | --- |
| `config/philosophies.yml` | Thresholds and weights per philosophy, guardrails, `impl: deterministic \| llm`. | `graph.load_config` |
| `config/alerts.yml` | Cooldown, material change, sell-only-for-watchlist, retry backoff, severity. | `alerts.policy` |
| `config/sandbox.yml` | Strategy sizing defaults. | `sandbox.config` |
| `config/features.yml` | Feature flags (Telegram off). Env `FEATURE_<NAME>` overrides. | `features.FeatureFlags` |
| `config/universe.yml` | 15 stocks and 4 bond ETFs. | `universe` |
| `config/bond_series.yml` | FRED series ids (yields and spreads). | `universe.load_bond_series` (the yields flow) |

All are found by walking up parent directories from the module file, which is why the images copy
`config/` next to the virtual environment.

## Build and workspace

- Python: a `uv` workspace (`packages/core`, `apps/api`, `flows`), Python 3.12 or newer, one
  `uv.lock`, ruff for lint and format (line length 100), pytest.
- Web: `pnpm`, Vite, TypeScript, vitest with Testing Library.
- Releases: `cz.yaml` (Commitizen, Conventional Commits), `semver.sh`, `VERSION`, `CHANGELOG.md`.
