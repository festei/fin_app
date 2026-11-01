import { useChannels, useFeatures } from "../api/hooks";
import { ErrorBox, Loading } from "../components/Status";

export function Settings() {
  const features = useFeatures();
  const channels = useChannels();
  return (
    <>
      <div className="page-head">
        <h1>Settings</h1>
      </div>
      <section className="card">
        <h2>Alert channels</h2>
        {channels.isLoading && <Loading />}
        {channels.error && <ErrorBox error={channels.error} />}
        <ul>
          {channels.data?.map((c) => (
            <li key={c.name}>
              <strong>{c.name}</strong>: {c.enabled ? "on" : "off"}
              {c.muted_until ? ` · muted until ${c.muted_until}` : ""}
            </li>
          ))}
        </ul>
        <p className="muted small">
          The in-app feed is always on. Telegram is a feature flag
          (<code>FEATURE_TELEGRAM_ENABLED</code>) and needs a bot token and chat id.
        </p>
      </section>
      <section className="card">
        <h2>Feature flags</h2>
        {features.isLoading && <Loading />}
        {features.error && <ErrorBox error={features.error} />}
        <ul>
          {Object.entries(features.data ?? {}).map(([name, on]) => (
            <li key={name}>
              {name}: <strong>{on ? "on" : "off"}</strong>
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}
