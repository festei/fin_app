# 05: Signals and alerts

## The scheduled scoring run

```mermaid
sequenceDiagram
  autonumber
  participant R as run_scoring
  participant E as signals.engine
  participant G as scoring graph
  participant D as Postgres
  participant P as alerts.pipeline
  participant C as channels (in_app, telegram)
  R->>E: score_universe(as_of)
  loop each active stock (own transaction)
    E->>D: latest actionable score (baseline) and hysteresis state
    E->>G: score_security(previous_conviction, buy_armed, sell_armed)
    G-->>E: conviction, signal?
    E->>D: insert scores row, insert signals row if a transition
  end
  R->>D: pending_signal_ids (new + left over from interrupted runs)
  R->>P: process_signals(ids)
  loop each signal (own transaction)
    P->>D: policy decides, insert alert and delivery rows, mark signal processed
  end
  P->>C: send_due(): claim, send OUTSIDE any transaction, record outcome
  R->>D: ingestion_runs row: ok, partial or failed
```

## Signal state machine

A signal is emitted only on a **transition** relative to the last actionable stored score, never on
every run. Hysteresis stops a score flapping around a line from signalling repeatedly.

```mermaid
stateDiagram-v2
  [*] --> NoView
  NoView: No baseline
  Low: Conviction below 70
  High: Conviction 70 or more
  Weak: Conviction below 50
  NoView --> High: first score at 70+ emits "emerging"
  NoView --> Low: no signal
  Low --> High: crosses 70 and buy is armed emits "buy"
  High --> Weak: falls below 50 emits "sell"
  Low --> Weak: falls below 50 from 50 or more emits "sell"
  Weak --> High: crosses 70 emits "buy"
  High --> Low: dips below 70, no signal
  note right of High
    Re-arm: after a buy another buy needs
    conviction to fall below 65 first.
    After a sell another sell needs it back at 55 or more.
  end note
```

What does **not** move the baseline:

```mermaid
flowchart TB
  run["a scoring run"] --> q1{"conviction is None?<br/>(every philosophy abstained)"}
  q1 -->|yes| nb1["stored as NULL.<br/>Not a baseline, so the next<br/>real score is 'emerging', not 'buy'"]
  q1 -->|no| q2{"signal_blocked?<br/>(stale or missing price or fundamentals)"}
  q2 -->|yes| nb2["stored and shown, but no signal<br/>and never a baseline, otherwise the buy<br/>would be swallowed when fresh data arrives"]
  q2 -->|no| ok["actionable: becomes the baseline"]
```

## Alert policy

```mermaid
flowchart TB
  sig["new signal"] --> a1{"already has an alert?"}
  a1 -->|yes| skip0["idempotent: reuse it"]
  a1 -->|no| a2{"sell and stock not on watchlist?"}
  a2 -->|yes| sup1["suppressed: sells only for watchlist stocks"]
  a2 -->|no| a3{"same kind alerted within 24 h?"}
  a3 -->|yes| a4{"conviction moved 10 points or more?"}
  a4 -->|no| sup2["suppressed: cooldown"]
  a4 -->|yes| mk
  a3 -->|no| mk["create alert<br/>severity: high for watchlist,<br/>else by kind (config/alerts.yml)"]
```

A suppressed signal is still marked processed (with a `suppressed_reason`), so the recovery sweep never
re-alerts it.

## Delivery

Creating an alert writes its delivery rows in the **same transaction**. Sending happens afterwards.

```mermaid
stateDiagram-v2
  [*] --> pending: alert created
  [*] --> sent: in_app, attempts = 1
  [*] --> skipped: channel muted at creation
  pending --> sent: claimed and delivered
  pending --> failed: channel error or unexpected exception
  failed --> sent: retry after backoff succeeds
  failed --> failed: retry fails (2, 10, 30, 120, 360 minutes)
  failed --> skipped: channel muted when the retry falls due
  pending --> pending: claim left by a dead process, retried after 10 min
  failed --> [*]: attempts reach the cap (6)
  sent --> [*]
  skipped --> [*]
```

Safety properties:

- **Claim by compare-and-set** on `attempts` (`claim_delivery`), so the scoring run and the worker can
  both call `send_due` and a delivery is still attempted by exactly one of them.
- **HTTP never runs inside a DB transaction**; each outcome is recorded in its own short transaction.
- A channel exception never escapes: `ChannelError` text is recorded, any other exception by type only.
- **No replay:** a channel enabled later has no delivery rows for older alerts.

## The worker and the Telegram bot

```mermaid
flowchart TB
  start["python -m fin_api.worker"] --> cfg{"FEATURE_TELEGRAM_ENABLED?"}
  cfg -->|no| off["no client, no token needed, zero calls"]
  cfg -->|yes| chk{"TELEGRAM_BOT_TOKEN and<br/>TELEGRAM_CHAT_ID set?"}
  chk -->|no| exit2["exit status 2 (fails loudly)"]
  chk -->|yes| bot["run the bot loop"]
  start --> lock["LeaderLock: Postgres advisory lock"]
  lock -->|already held| exit3["exit status 3"]
  lock -->|acquired| loop["every loop: re-verify lock (pg_locks),<br/>re-acquire if the connection died,<br/>send_due() retries"]
  loop -->|lost to another instance| exit3
```

The bot answers only the configured chat. Its update offset is persisted **before** each command runs
(at-most-once), so a failing reply or a restart never replays `/mute` or `/unwatch`. The bot token is
part of every request URL, so it is kept out of errors and logs. **Telegram has only run against a
mocked Bot API.**
