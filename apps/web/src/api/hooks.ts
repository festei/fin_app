import {
  type QueryClient,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "./client";
import type { NewOrder, NewPortfolio } from "./types";

export const useFeatures = () => useQuery({ queryKey: ["features"], queryFn: api.features });
export const useChannels = () => useQuery({ queryKey: ["channels"], queryFn: api.channels });
export const useOpportunities = () =>
  useQuery({ queryKey: ["opportunities"], queryFn: () => api.opportunities() });
export const useWatchlist = () => useQuery({ queryKey: ["watchlist"], queryFn: api.watchlist });

export const useScore = (ticker: string) =>
  useQuery({ queryKey: ["score", ticker], queryFn: () => api.score(ticker), retry: false });
export const usePrices = (ticker: string, days: number) =>
  useQuery({ queryKey: ["prices", ticker, days], queryFn: () => api.prices(ticker, days) });
export const useScoreHistory = (ticker: string) =>
  useQuery({ queryKey: ["scoreHistory", ticker], queryFn: () => api.scoreHistory(ticker) });

/** `value`, but only after it has stopped changing for `ms` (keystrokes in a search box). */
export function useDebounced<T>(value: T, ms = 250): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(id);
  }, [value, ms]);
  return debounced;
}

export const useSearch = (q: string) =>
  useQuery({
    queryKey: ["search", q],
    queryFn: () => api.searchSecurities(q),
    enabled: q.trim().length > 0,
  });

export function useToggleWatch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ ticker, watched }: { ticker: string; watched: boolean }) => {
      if (watched) await api.unwatch(ticker);
      else await api.watch(ticker);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["watchlist"] }),
  });
}

export const useAlerts = (unreadOnly: boolean) =>
  useInfiniteQuery({
    queryKey: ["alerts", unreadOnly],
    queryFn: ({ pageParam }) => api.alerts({ beforeId: pageParam, unreadOnly }),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (last) => last.next_before_id ?? undefined,
  });

export function useMarkRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.markRead(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["alerts"] }),
  });
}

export const usePortfolios = () => useQuery({ queryKey: ["portfolios"], queryFn: api.portfolios });
export const usePortfolio = (id: number) =>
  useQuery({ queryKey: ["portfolio", id], queryFn: () => api.portfolio(id) });
export const usePortfolioHistory = (id: number) =>
  useQuery({ queryKey: ["portfolioHistory", id], queryFn: () => api.history(id) });
export const useOrders = (id: number) =>
  useQuery({ queryKey: ["orders", id], queryFn: () => api.orders(id) });

export function useCreatePortfolio() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: NewPortfolio) => api.createPortfolio(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["portfolios"] }),
  });
}

/** An order changes the portfolio's detail, its order list and the overview card. */
function refreshPortfolio(qc: QueryClient, portfolioId: number) {
  qc.invalidateQueries({ queryKey: ["orders", portfolioId] });
  qc.invalidateQueries({ queryKey: ["portfolio", portfolioId] });
  qc.invalidateQueries({ queryKey: ["portfolios"] });
}

export function usePlaceOrder(portfolioId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: NewOrder) => api.placeOrder(portfolioId, body),
    onSuccess: () => refreshPortfolio(qc, portfolioId),
  });
}

export function useCancelOrder(portfolioId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (orderId: number) => api.cancelOrder(portfolioId, orderId),
    onSuccess: () => refreshPortfolio(qc, portfolioId),
  });
}
