import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";
import { fail, mockApi, renderApp } from "../test/utils";

test("the search box finds a security and opens its page", async () => {
  mockApi({
    "GET /api/securities": [{ ticker: "ACME", name: "Acme Industrial", sector: null, kind: "stock", watched: false }],
    "GET /api/opportunities": [],
    "GET /api/watchlist": { tickers: [] },
    "GET /api/securities/ACME/score": fail(404, "unknown ticker ACME"),
    "GET /api/securities/ACME/prices": [],
    "GET /api/securities/ACME/scores": { scores: [], signals: [] },
  });
  renderApp("/");
  await userEvent.type(screen.getByLabelText("Search securities"), "acm");
  await userEvent.click(await screen.findByRole("button", { name: /ACME/ }));
  expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  expect(screen.getByLabelText("Search securities")).toHaveValue("");
  expect(await screen.findByText(/Unknown ticker ACME/)).toBeInTheDocument(); // navigated to /security/ACME
});

test("typing in the search box asks the API once, after a short pause", async () => {
  const calls = mockApi({
    "GET /api/securities": [],
    "GET /api/opportunities": [],
    "GET /api/watchlist": { tickers: [] },
  });
  renderApp("/");
  await userEvent.type(screen.getByLabelText("Search securities"), "apple");
  await screen.findByText("No match");
  expect(calls.filter((c) => c.url.startsWith("/api/securities"))).toHaveLength(1);
  expect(calls.find((c) => c.url.startsWith("/api/securities"))!.url).toContain("q=apple");
});

test("the navigation reaches every section", async () => {
  mockApi({
    "GET /api/opportunities": [], "GET /api/watchlist": { tickers: [] },
    "GET /api/alerts": { items: [], next_before_id: null }, "GET /api/portfolios": [],
    "GET /api/channels": [{ name: "in_app", enabled: true, muted_until: null }, { name: "telegram", enabled: false, muted_until: null }],
    "GET /api/features": { telegram_enabled: false },
  });
  renderApp("/");
  const nav = screen.getByRole("navigation", { name: "Main" });
  await userEvent.click(nav.querySelector('a[href="/alerts"]')!);
  expect(await screen.findByRole("heading", { name: "Alerts" })).toBeInTheDocument();
  await userEvent.click(nav.querySelector('a[href="/sandbox"]')!);
  expect(await screen.findByRole("heading", { name: "Sandbox" })).toBeInTheDocument();
  await userEvent.click(nav.querySelector('a[href="/settings"]')!);
  expect(await screen.findByText("telegram_enabled:")).toBeInTheDocument();
  expect(screen.getByText(/in_app/)).toBeInTheDocument();
});

test("an unknown route says so", () => {
  mockApi({});
  renderApp("/nope");
  expect(screen.getByText("Page not found.")).toBeInTheDocument();
});
