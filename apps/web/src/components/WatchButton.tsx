import { useToggleWatch, useWatchlist } from "../api/hooks";

export function WatchButton({ ticker, compact = false }: { ticker: string; compact?: boolean }) {
  const watchlist = useWatchlist();
  const toggle = useToggleWatch();
  const watched = watchlist.data?.tickers.includes(ticker) ?? false;
  return (
    <button
      type="button"
      className={`watch ${watched ? "on" : ""}`}
      aria-pressed={watched}
      aria-label={`${watched ? "Unwatch" : "Watch"} ${ticker}`}
      disabled={toggle.isPending || watchlist.isLoading}
      onClick={() => toggle.mutate({ ticker, watched })}
      title={watched ? "On your watchlist (sell alerts enabled)" : "Add to watchlist"}
    >
      {watched ? "★" : "☆"}
      {!compact && <span> {watched ? "Watching" : "Watch"}</span>}
    </button>
  );
}
