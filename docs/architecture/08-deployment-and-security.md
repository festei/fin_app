# 08: Deployment and security

Nothing is deployed anywhere by this repository: the images and compose files are **prepared**, so a
deployment can follow.

## Compose topologies

```mermaid
flowchart TB
  subgraph dev["Development: deploy/docker-compose.yml"]
    d_web["web :8080<br/>AUTH_BASIC=off, API_TOKEN default dev-token"]
    d_api["api :8000"]
    d_wk["worker"]
    d_fl["flows"]
    d_pf["prefect :4200"]
    d_db[("db :5432<br/>TimescaleDB")]
    d_mg["migrate"]
  end
  subgraph prod["Production-shaped: deploy/docker-compose.prod.yml"]
    p_web["web :80 to 8080<br/>basic auth required,<br/>htpasswd mounted"]
    p_api["api<br/>API_TOKEN required"]
    p_wk["worker"]
    p_fl["flows"]
    p_pf["prefect"]
    p_db[("db")]
    p_mg["migrate"]
  end
```

| | Dev | Prod-shaped |
| --- | --- | --- |
| Published ports | web 8080, api 8000, db 5432, prefect 4200, **all bound to 127.0.0.1** | web only (`80:8080`); api, db and prefect stay internal |
| Basic auth | off | required; `HTPASSWD_FILE` mounted and the container refuses to start without it |
| `API_TOKEN` | defaults to `dev-token` (well known) | required (`${API_TOKEN:?...}`) |
| Images | built from the repo | `fin-api`, `fin-flows`, `fin-web` at `${TAG}` |
| TLS | none | **a terminator must sit in front** of the web container (it speaks plain HTTP) |

Start-up order: `db`, then `migrate` (one-shot), then `api`, `worker`, `flows`; `web` after `api`.
The image tags for the Prefect server must equal the Prefect version locked in `uv.lock`
(`flows/tests/test_versions.py` enforces it). All images run as a non-root user.

## The authentication chain

```mermaid
flowchart LR
  b["Browser<br/>has basic-auth credentials only"] -->|"1. basic auth over TLS<br/>(terminator in front)"| n["nginx"]
  n -->|"2. limit_req, CSRF header<br/>on non-GET"| n
  n -->|"3. REPLACES Authorization with<br/>Bearer API_TOKEN"| a["FastAPI"]
  a -->|"4. require_write_token<br/>(writes only)"| w["write endpoint"]
  a -->|"reads are open"| r["read endpoint"]
```

```mermaid
sequenceDiagram
  autonumber
  participant X as Attacker page (other origin)
  participant B as Victim browser (cached basic-auth credentials)
  participant N as nginx
  X->>B: auto-submitted cross-site form POST to /api/alerts/1/read
  B->>N: POST with cached basic-auth credentials, NO X-Requested-With
  N-->>B: 403 (a form post cannot set the header)
  X->>B: cross-origin fetch with X-Requested-With
  B->>N: browser sends a CORS preflight first
  N-->>B: no CORS grant, so the real request is never sent
```

## Trust boundaries and where secrets live

```mermaid
flowchart TB
  subgraph pub["Untrusted"]
    br["Browser and network"]
  end
  subgraph edge["Edge (trusted to enforce auth)"]
    tls["TLS terminator (not in repo)"]
    ng["nginx: basic auth, CSRF header,<br/>rate limit, CSP, HSTS"]
  end
  subgraph inner["Internal network"]
    ap["api"]
    db[("db")]
    wk["worker"]
    fl["flows + prefect"]
  end
  br --> tls --> ng --> ap
  ap --> db
  wk --> db
  fl --> db
  wk -->|"token in URL path;<br/>kept out of logs and errors"| tg["Telegram"]
  fl -->|"API keys in query strings;<br/>never in error messages"| src["SEC, FRED, Yahoo"]
```

| Secret | Where it is used | Rule |
| --- | --- | --- |
| `API_TOKEN` | api (checks it) and web (adds it) | URL-safe only (`openssl rand -hex 32`): it lands in a quoted nginx string, and the entrypoint validator rejects `"`, `;`, `$`, whitespace and similar. Never in browser code. |
| htpasswd file | web | Mounted read-only; bcrypt (`htpasswd -B`). A missing or empty file stops the container. |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | worker | Required only when the flag is on; the token never appears in errors or logs. |
| `FRED_API_KEY`, `SEC_USER_AGENT` | flows | Provider errors never contain credentials. |
| Database credentials | all Python containers | `DATABASE_URL` or `POSTGRES_*`; the API reports only the exception type on failures. |

## Web container start-up

```mermaid
flowchart TB
  s["container start"] --> v["10-validate-env.sh"]
  v -->|"API_TOKEN empty or unsafe,<br/>realm unsafe,<br/>auth on but no htpasswd"| die["exit 1: refuse to start"]
  v -->|ok| e["nginx image: envsubst renders<br/>/etc/nginx/templates/default.conf.template"]
  e --> run["nginx serves the SPA and proxies /api"]
```

## What has and has not been verified

Run for real: the rendered template against **nginx 1.24** (config validity, 401/200, token injection,
CSRF header, headers, rate limit, fail-closed, `auth_basic off`) and the validator script, plus the built
UI driven through that nginx in Chromium. **Not run:** the Alpine image build and its entrypoint wiring
(no Docker daemon in the build sandbox; checks are in `scripts/smoke-images.sh`), and nginx 1.27.
