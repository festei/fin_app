# 01: System context

## Context: people and systems

```mermaid
flowchart TB
  owner(["Owner / investor<br/>browser"])
  tgu(["Owner on Telegram<br/>(optional)"])
  subgraph sys["fin-app"]
    direction TB
    app["Investment opportunity scanner<br/>ingest, score, signal, alert, simulate"]
  end
  sec["SEC EDGAR<br/>XBRL company facts"]
  fred["FRED<br/>Treasury yields, spreads"]
  yahoo["Yahoo Finance<br/>daily adjusted prices"]
  av["Alpha Vantage<br/>fallback fundamentals<br/>(provider exists, not scheduled)"]
  tg["Telegram Bot API"]
  owner -->|"views opportunities, places simulated orders"| app
  tgu <-->|"alerts and bot commands"| tg
  tg <-->|"only if FEATURE_TELEGRAM_ENABLED"| app
  app -->|"fundamentals"| sec
  app -->|"yield series"| fred
  app -->|"price history"| yahoo
  app -.->|"unused today"| av
```

Nothing here executes real trades. The sandbox is a simulation, and alerts are the only outbound
product of the system.

## Containers

The compose files (`deploy/docker-compose.yml`, `deploy/docker-compose.prod.yml`) define these
services. The three Python images are built from one `deploy/python.Dockerfile`.

```mermaid
flowchart LR
  user(["Browser"]) -->|"HTTP 8080 (TLS terminator in production)"| web
  subgraph c["Containers"]
    web["web<br/>nginx + built React app<br/>basic auth, token injection"]
    api["api<br/>uvicorn fin_api.main:app :8000"]
    worker["worker<br/>python -m fin_api.worker"]
    flows["flows<br/>python -m fin_flows.serve"]
    prefect["prefect<br/>server + UI :4200"]
    migrate["migrate<br/>one-shot: alembic upgrade"]
    db[("db<br/>Postgres 16 or TimescaleDB")]
  end
  web -->|"/api/* with Bearer token"| api
  api --> db
  worker --> db
  flows --> db
  flows -->|"flow runs and schedules"| prefect
  migrate --> db
  migrate -.->|"must finish first"| api
  migrate -.-> worker
  migrate -.-> flows
  flows --> sec["SEC, FRED, Yahoo"]
  worker -.-> tg["Telegram (optional)"]
```

| Container | Role | Scales to |
| --- | --- | --- |
| `web` | Serves the SPA, enforces basic auth in production, adds the API token, rate-limits. | Stateless. |
| `api` | REST API: reads, scoring a ticker on demand, watchlist, alerts, sandbox writes. | Stateless; many replicas are safe. |
| `worker` | Delivery retries and the Telegram bot. Holds a Postgres advisory lock. | **Exactly one** (a second exits with status 3). |
| `flows` | Registers the schedules with Prefect and runs the daily pipeline. | One. |
| `prefect` | Prefect server and UI. Its image tag must equal the Prefect version locked in `uv.lock`. | One. |
| `migrate` | Applies Alembic migrations, then exits. | One-shot. |
| `db` | System of record. | One. |

The always-on minute-level loop planned for intraday polling is **not built**; it is meant to be a
separate service, not a Prefect job.
