import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";
import { fail, mockApi, renderApp } from "../test/utils";

const portfolio = (id: number, extra = {}) => ({
  id, name: `P${id}`, mode: "manual", initial_cash: 100, cash: 60, positions_value: 50,
  total_value: 110, return_pct: 10, fee_bps: 0, strategy_config: null, created_at: null, ...extra,
});

test("lists portfolios with value and return", async () => {
  mockApi({ "GET /api/portfolios": [portfolio(1), portfolio(2, { return_pct: -3.5, name: "Loser" })] });
  renderApp("/sandbox");
  const card = (await screen.findByText("P1")).closest("a")!;
  expect(within(card).getByText("$110.00")).toBeInTheDocument();
  expect(within(card).getByText("+10.00%")).toHaveClass("pos");
  expect(screen.getByText("-3.50%")).toHaveClass("neg");
});

test("creates a portfolio from the form", async () => {
  const calls = mockApi({
    "GET /api/portfolios": [],
    "POST /api/portfolios": portfolio(3),
  });
  renderApp("/sandbox");
  const form = await screen.findByRole("form", { name: "New portfolio" });
  const create = within(form).getByRole("button", { name: "Create" });
  expect(create).toBeDisabled(); // needs a name
  await userEvent.type(within(form).getByLabelText("Name"), "My $100");
  await userEvent.selectOptions(within(form).getByLabelText("Mode"), "manual");
  await userEvent.click(create);
  expect(calls.find((c) => c.method === "POST")!.body).toEqual({ name: "My $100", mode: "manual", cash: 100 });
});

test("shows the API's validation message when creating fails", async () => {
  mockApi({ "GET /api/portfolios": [], "POST /api/portfolios": fail(422, [{ loc: [], msg: "starting cash must be positive", type: "x" }]) });
  renderApp("/sandbox");
  const form = await screen.findByRole("form", { name: "New portfolio" });
  await userEvent.type(within(form).getByLabelText("Name"), "x");
  await userEvent.click(within(form).getByRole("button", { name: "Create" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("starting cash must be positive");
});

const detail = (extra = {}) => ({
  ...portfolio(1),
  positions: [
    { ticker: "ACME", name: "Acme", quantity: 2, avg_cost: 10, price: 12, market_value: 24, pnl: 4, pnl_pct: 20 },
  ],
  ...extra,
});
const order = (id: number, status: string, extra = {}) => ({
  id, ticker: "ACME", side: "buy", quantity: null, notional: 20, status, created_at: "2026-10-01T10:00:00",
  filled_at: null, fill_quantity: null, fill_price: null, fee: null, reason: null, signal_id: null, ...extra,
});

test("portfolio page shows positions, orders and cancels a pending order", async () => {
  const calls = mockApi({
    "GET /api/portfolios/1": detail(),
    "GET /api/portfolios/1/history": [
      { as_of: "2026-10-01", cash: 100, positions_value: 0, total_value: 100 },
      { as_of: "2026-10-02", cash: 60, positions_value: 50, total_value: 110 },
    ],
    "GET /api/portfolios/1/orders": [
      order(2, "pending"),
      order(1, "filled", { fill_quantity: 2, fill_price: 10, filled_at: "2026-10-02T21:00:00" }),
      order(3, "rejected", { reason: "insufficient cash: need 50, have 5" }),
    ],
    "DELETE /api/portfolios/1/orders/2": { id: 2, cancelled: true },
    "GET /api/portfolios": [portfolio(1)],
  });
  renderApp("/sandbox/1");
  expect(await screen.findByText("$110.00", { selector: ".big" })).toBeInTheDocument();
  expect(screen.getByTestId("line-chart")).toHaveTextContent("2 points");
  expect(screen.getByText("$4.00")).toHaveClass("pos"); // P&L of the position
  expect(screen.getByText("insufficient cash: need 50, have 5")).toBeInTheDocument();
  expect(screen.getByText("2 @ $10.00 · 2026-10-02")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(calls.some((c) => c.method === "DELETE" && c.url === "/api/portfolios/1/orders/2")).toBe(true);
});

test("placing or cancelling an order marks the overview as stale, not just the portfolio", async () => {
  mockApi({
    "GET /api/portfolios/1": detail(),
    "GET /api/portfolios/1/history": [],
    "GET /api/portfolios/1/orders": [order(2, "pending")],
    "DELETE /api/portfolios/1/orders/2": { id: 2, cancelled: true },
  });
  const { client } = renderApp("/sandbox/1");
  const cancel = await screen.findByRole("button", { name: "Cancel" });
  client.setQueryData(["portfolios"], [portfolio(1)]); // the overview was loaded earlier
  expect(client.getQueryState(["portfolios"])?.isInvalidated).toBe(false);
  await userEvent.click(cancel);
  await vi.waitFor(() => expect(client.getQueryState(["portfolios"])?.isInvalidated).toBe(true));
});

test("a strategy portfolio shows its settings instead of an order ticket", async () => {
  mockApi({
    "GET /api/portfolios/1": detail({ mode: "strategy", strategy_config: { position_pct: 0.3 } }),
    "GET /api/portfolios/1/history": [],
    "GET /api/portfolios/1/orders": [],
    "GET /api/portfolios": [],
  });
  renderApp("/sandbox/1");
  expect(await screen.findByText(/trades from signals/)).toBeInTheDocument();
  expect(screen.getByText(/"position_pct": 0.3/)).toBeInTheDocument();
  expect(screen.queryByLabelText("Ticker to trade")).not.toBeInTheDocument();
  expect(screen.getByText(/fills in as the daily sandbox step/)).toBeInTheDocument(); // no curve yet
});

test("an unknown portfolio is a message, not a crash", async () => {
  mockApi({
    "GET /api/portfolios/9": fail(404, "unknown portfolio 9"),
    "GET /api/portfolios/9/history": fail(404, "x"),
    "GET /api/portfolios/9/orders": fail(404, "x"),
  });
  renderApp("/sandbox/9");
  expect(await screen.findByText(/Unknown portfolio/)).toBeInTheDocument();
});
