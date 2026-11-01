# 008: Trading UI

- **Phase / PR:** Phase 4, [#17](https://github.com/festei/fin_app/pull/17) (commits `bceee99`, review fixes `e4fe1b9`)
- **Status:** Delivered

## Context

The web app was a one-page scaffold. The owner wanted a UI "tailored to someone who tries to trade
stocks or bonds". Two decisions were taken with the owner before building:

- **Auth:** nginx basic auth (single user) plus server-side injection of
  `Authorization: Bearer $API_TOKEN` into `/api` requests. The token never reaches browser JS.
- **Sandbox modes and fills:** as in [006](006-paper-trading-sandbox.md).

## Goals / Non-goals

Goals: opportunities, security detail with order ticket, alerts, sandbox, settings, search; auth
without a login system. Non-goals: bond screens (no bond scorers yet), real multi-user login.

## Decisions

- **Stack:** Vite + React 18 + TypeScript, React Router, TanStack Query, `lightweight-charts`,
  plain dark-theme CSS (no CSS framework), responsive to phone width. Typed client in `src/api/`.
- **Pages:** Opportunities (ranked table, sector filter, sort, watch star), Security detail (score,
  candles, conviction history with signal markers and buy/sell lines, per-philosophy criteria with
  actual versus limit, data warnings, withheld-signal notice, order ticket), Alerts (feed, unread
  filter, mark read, cursor paging), Sandbox (portfolios, equity curve, positions, orders, cancel),
  Settings (flags, channels).
- **The browser never holds the write token.** nginx adds it in production
  (`deploy/nginx.conf.template`); the Vite proxy does in development.
- **Charts are mocked in jsdom tests** (no canvas); chart rendering is verified by running the app.
- **Demo data:** `scripts/seed_demo.py` builds a database of **synthetic** companies and runs the
  real scoring, alert and sandbox code over 35 weeks so the UI can be run and screenshotted.

## Design

Diagrams: [architecture/07-api-and-web](../architecture/07-api-and-web.md) and
[architecture/08-deployment-and-security](../architecture/08-deployment-and-security.md).

## Acceptance criteria and verification

Typecheck, 33 web tests, production build; 465 Python tests pass on SQLite. Every page was driven in
Chromium against the real API (no console errors); after review the built UI was also driven through
a **real nginx** with basic auth and the CSP: charts draw, and watch, order, mark-read and cancel work
through the proxy.

## Review outcomes (commit `e4fe1b9`)

| Review finding | Change |
| --- | --- |
| The "Signal" banner was spurious (the score endpoint had no baseline, so every high stock read "emerging") | The endpoint judges today's signal against the stored baseline and hysteresis; the page shows the latest **stored** signal, a separate pending notice, and labels the headline "Conviction (live)". |
| Basic auth over plain HTTP, no rate limit, partial security headers | TLS terminator documented, `limit_req`, HSTS and a CSP. |
| CSRF through ambient basic-auth credentials | Non-GET `/api` requests need `X-Requested-With: fin-app`. |
| Rendered nginx config never run | Run for real in tests (auth, token injection, CSRF, headers, rate limit, fail-closed). This caught that `limit_req off` is invalid nginx. An entrypoint script validates `API_TOKEN`, the realm and the htpasswd file. |
| Order ticket could submit an empty buy; charts rebuilt on every render | Validity per side; stable chart defaults and a `ResizeObserver`. |
| Smaller | Gaps for blocked runs, debounced search, structured alert body, wider cache invalidation, dev ports bound to 127.0.0.1, seed script refuses real databases and uses relative dates. |

## Known limitations

The Alpine image build and its entrypoint wiring were never run (no Docker in the sandbox;
`scripts/smoke-images.sh` has the checks). The nginx tested was 1.24, not the image's 1.27. No bond
screens. One shared basic-auth user.
