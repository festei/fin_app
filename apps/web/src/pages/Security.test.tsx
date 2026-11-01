import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";
import { fail, mockApi, renderApp } from "../test/utils";

const result = (philosophy: string, score: number, evaluated = true) => ({
  philosophy,
  score,
  evaluated,
  explanation: evaluated ? "" : "needs 5 years of history",
  evidence: [],
  impl: "deterministic",
  criteria: evaluated
    ? [
        { name: "Low P/E", passed: true, actual: 9.9, threshold: 15, detail: "" },
        { name: "Low debt", passed: false, actual: 1.4, threshold: 1, detail: "debt too high" },
      ]
    : [],
});

const score = {
  conviction: 78.5,
  all_abstained: false,
  explanation: "Conviction from: graham 57",
  philosophy_results: {
    buffett: result("buffett", 0, false),
    graham: result("graham", 57),
    lynch: result("lynch", 100),
  },
  guardrail_results: { piotroski: result("piotroski", 0, false) },
  signal: { ticker: "ACME", kind: "buy", conviction: 78.5, as_of: "2026-10-30T00:00:00", reason: "" },
  metrics: {
    ticker: "ACME", as_of: "2026-10-30T00:00:00", price: 25, price_date: "2026-10-30", pe: 10,
    pe_avg3: 9.9, pb: 2.5, sector: "Technology", warnings: ["no dividend data"], signal_blockers: [],
  },
};

const routes = {
  "GET /api/securities/ACME/score": score,
  "GET /api/securities/ACME/prices": [
    { ts: "2026-10-29T21:00:00", open: 24, high: 26, low: 23, close: 25, volume: 10 },
    { ts: "2026-10-30T21:00:00", open: 25, high: 27, low: 24, close: 26, volume: 10 },
  ],
  "GET /api/securities/ACME/scores": {
    scores: [
      { computed_at: null, as_of: "2026-10-01", conviction: 60, signal_blocked: false, all_abstained: false },
      { computed_at: null, as_of: "2026-10-08", conviction: 90, signal_blocked: true, all_abstained: false },
      { computed_at: null, as_of: "2026-10-15", conviction: null, signal_blocked: false, all_abstained: true },
      { computed_at: null, as_of: "2026-10-22", conviction: 78, signal_blocked: false, all_abstained: false },
    ],
    signals: [{ kind: "buy", conviction: 78, as_of: "2026-10-22" }],
  },
  "GET /api/watchlist": { tickers: [] },
  "GET /api/portfolios": [
    { id: 1, name: "Manual", mode: "manual", initial_cash: 100, cash: 100, positions_value: 0, total_value: 100, return_pct: 0, fee_bps: 0, strategy_config: null, created_at: null },
  ],
};

test("shows conviction, the signal, the data notes and the charts' inputs", async () => {
  mockApi(routes);
  renderApp("/security/acme");
  // the banner comes from the STORED signals (the live endpoint only adds a pending transition)
  expect(await screen.findByText(/Latest signal:/)).toHaveTextContent("buy at 78 on 2026-10-22");
  expect(screen.getByText(/New since the last stored score/)).toBeInTheDocument();
  expect(screen.getByText("Conviction (live)")).toBeInTheDocument();
  expect(screen.getByText("no dividend data")).toBeInTheDocument();
  expect(await screen.findByTestId("price-chart")).toHaveTextContent("2 bars");
  // blocked and all-abstained runs become gaps instead of a line across them; the signal is a marker
  expect(await screen.findByTestId("line-chart")).toHaveTextContent("2 points, 2 gaps, 1 markers");
});

test("lists evaluated philosophies first and expands the criteria with actual vs limit", async () => {
  mockApi(routes);
  renderApp("/security/ACME");
  await screen.findByText(/Latest signal:/);
  const order = screen
    .getAllByText(/^(Buffett|Graham|Lynch)$/)
    .map((e) => e.textContent);
  expect(order).toEqual(["Lynch", "Graham", "Buffett"]); // best first, abstentions last
  await userEvent.click(screen.getByText("Lynch"));
  expect(screen.getAllByText(/limit 15/).length).toBeGreaterThan(0);
  expect(screen.getAllByLabelText("failed").length).toBeGreaterThan(0);
});

test("a stock that is already high shows no pending signal when none is new", async () => {
  mockApi({ ...routes, "GET /api/securities/ACME/score": { ...score, signal: null } });
  renderApp("/security/ACME");
  expect(await screen.findByText(/Latest signal:/)).toBeInTheDocument();
  expect(screen.queryByText(/New since the last stored score/)).not.toBeInTheDocument();
});

test("a leftover 'sell the whole position' never turns a buy into an empty order", async () => {
  mockApi(routes);
  renderApp("/security/ACME");
  await userEvent.click(await screen.findByRole("button", { name: "Sell" }));
  await userEvent.click(screen.getByLabelText("Sell the whole position"));
  expect(screen.getByRole("button", { name: "Place sell order" })).toBeEnabled();
  await userEvent.click(screen.getByRole("button", { name: "Buy" }));
  expect(screen.getByRole("button", { name: "Place buy order" })).toBeDisabled(); // no amount
  await userEvent.click(screen.getByRole("button", { name: "Sell" }));
  expect(screen.getByLabelText("Sell the whole position")).not.toBeChecked(); // was reset
});

test("an unknown ticker gets a helpful message, not a crash", async () => {
  mockApi({
    "GET /api/securities/ZZZ/score": fail(404, "unknown ticker ZZZ"),
    "GET /api/securities/ZZZ/prices": [],
    "GET /api/securities/ZZZ/scores": { scores: [], signals: [] },
  });
  renderApp("/security/ZZZ");
  expect(await screen.findByText(/Unknown ticker ZZZ/)).toBeInTheDocument();
});

test("signals withheld because of stale data are explained", async () => {
  mockApi({
    ...routes,
    "GET /api/securities/ACME/score": {
      ...score,
      signal: null,
      metrics: { ...score.metrics, signal_blockers: ["price is stale: last bar 2026-09-01"] },
    },
  });
  renderApp("/security/ACME");
  expect(await screen.findByText(/Signals withheld: price is stale/)).toBeInTheDocument();
});

test("places a simulated buy by dollar amount in the chosen manual portfolio", async () => {
  const calls = mockApi({
    ...routes,
    "POST /api/portfolios/1/orders": { id: 7, status: "pending", ticker: "ACME" },
  });
  renderApp("/security/ACME");
  const form = await screen.findByRole("form", { name: "Simulated order" });
  expect(form).toHaveTextContent("Fills at the first daily close after today");
  const submit = screen.getByRole("button", { name: "Place buy order" });
  expect(submit).toBeDisabled(); // no amount yet
  await userEvent.type(screen.getByLabelText("Amount in USD"), "20");
  await userEvent.click(submit);
  const post = calls.find((c) => c.method === "POST")!;
  expect(post.body).toEqual({ ticker: "ACME", side: "buy", notional: 20 });
  expect(await screen.findByText(/Order #7 queued/)).toBeInTheDocument();
});

test("selling the whole position sends no amount and shows API errors", async () => {
  const calls = mockApi({
    ...routes,
    "POST /api/portfolios/1/orders": fail(422, "this portfolio trades from signals"),
  });
  renderApp("/security/ACME");
  await userEvent.click(await screen.findByRole("button", { name: "Sell" }));
  await userEvent.click(screen.getByLabelText("Sell the whole position"));
  await userEvent.click(screen.getByRole("button", { name: "Place sell order" }));
  expect(calls.find((c) => c.method === "POST")!.body).toEqual({ ticker: "ACME", side: "sell" });
  expect(await screen.findByRole("alert")).toHaveTextContent("trades from signals");
});

test("asks for a manual portfolio first when there is none", async () => {
  mockApi({ ...routes, "GET /api/portfolios": [] });
  renderApp("/security/ACME");
  expect(await screen.findByText(/need a manual sandbox portfolio/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Create one" })).toHaveAttribute("href", "/sandbox");
});
