import { useState } from "react";
import { Link } from "react-router-dom";
import { useAlerts, useMarkRead } from "../api/hooks";
import { Empty, ErrorBox, Loading } from "../components/Status";
import { age } from "../lib/format";

/** The alert text is shared with Telegram, whose last line points at a bot command. */
export const inAppBody = (body: string): string =>
  body
    .split("\n")
    .filter((line) => !line.startsWith("Use /why"))
    .join("\n");

export function Alerts() {
  const [unreadOnly, setUnreadOnly] = useState(false);
  const alerts = useAlerts(unreadOnly);
  const markRead = useMarkRead();
  const items = alerts.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <>
      <div className="page-head">
        <h1>Alerts</h1>
        <label className="check">
          <input
            type="checkbox"
            checked={unreadOnly}
            onChange={(e) => setUnreadOnly(e.target.checked)}
          />
          Unread only
        </label>
      </div>
      {alerts.isLoading && <Loading what="Loading alerts" />}
      {alerts.error && <ErrorBox error={alerts.error} />}
      {alerts.data && items.length === 0 && (
        <Empty>{unreadOnly ? "Nothing unread." : "No alerts yet."}</Empty>
      )}
      <ul className="alerts">
        {items.map((a) => (
          <li key={a.id} className={`alert sev-${a.severity} ${a.read ? "read" : ""}`}>
            <div className="alert-head">
              <span className={`pill kind-${a.kind}`}>{a.kind}</span>
              <Link to={`/security/${a.ticker}`} className="ticker">
                {a.ticker}
              </Link>
              <span className="muted small">{age(a.created_at)}</span>
              <span className="grow" />
              {!a.read && (
                <button
                  type="button"
                  onClick={() => markRead.mutate(a.id)}
                  disabled={markRead.isPending}
                >
                  Mark read
                </button>
              )}
            </div>
            <strong>{a.title}</strong>
            <pre className="alert-body">{inAppBody(a.body)}</pre>
            <div className="muted small">
              {Object.entries(a.deliveries)
                .map(([channel, status]) => `${channel}: ${status}`)
                .join(" · ")}
            </div>
          </li>
        ))}
      </ul>
      {alerts.hasNextPage && (
        <button type="button" onClick={() => alerts.fetchNextPage()} disabled={alerts.isFetching}>
          {alerts.isFetching ? "Loading…" : "Older alerts"}
        </button>
      )}
    </>
  );
}
