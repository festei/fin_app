import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";
import { mockApi, opportunity, renderApp } from "../test/utils";

const base = {
  "GET /api/opportunities": [
    opportunity("LOW", 72, { sector: "Energy" }),
    opportunity("TOP", 93),
    opportunity("MID", 78, { name: null }),
  ],
  "GET /api/watchlist": { tickers: ["MID"] },
};

test("ranks securities by conviction and shows the philosophy chips", async () => {
  mockApi(base);
  renderApp("/");
  const rows = await screen.findAllByRole("row");
  const order = rows.slice(1).map((r) => within(r).getByRole("link").textContent);
  expect(order).toEqual(["TOP", "MID", "LOW"]);
  expect(screen.getAllByText("Graham 60").length).toBe(3);
});

test("filters by sector and re-sorts", async () => {
  mockApi(base);
  renderApp("/");
  await screen.findByText("TOP");
  await userEvent.selectOptions(screen.getByLabelText("Sector"), "Energy");
  expect(screen.queryByText("TOP")).not.toBeInTheDocument();
  expect(screen.getByText("LOW")).toBeInTheDocument();
  await userEvent.selectOptions(screen.getByLabelText("Sector"), "");
  await userEvent.selectOptions(screen.getByLabelText("Sort by"), "ticker");
  const rows = screen.getAllByRole("row").slice(1);
  expect(rows.map((r) => within(r).getByRole("link").textContent)).toEqual(["LOW", "MID", "TOP"]);
});

test("starring a stock calls the watchlist API without any credentials", async () => {
  const calls = mockApi({ ...base, "PUT /api/watchlist/TOP": { ticker: "TOP", added: true } });
  renderApp("/");
  await userEvent.click(await screen.findByRole("button", { name: "Watch TOP" }));
  const put = calls.find((c) => c.method === "PUT")!;
  expect(put.url).toBe("/api/watchlist/TOP");
  expect(JSON.stringify(put.headers).toLowerCase()).not.toContain("authorization");
  expect(screen.getByRole("button", { name: "Unwatch MID" })).toHaveAttribute("aria-pressed", "true");
});

test("explains an empty list and a missing database", async () => {
  mockApi({ "GET /api/opportunities": [], "GET /api/watchlist": { tickers: [] } });
  renderApp("/");
  expect(await screen.findByText(/No scored securities yet/)).toBeInTheDocument();
});

test("shows a clear message when the API has no database", async () => {
  const { fail } = await import("../test/utils");
  mockApi({ "GET /api/opportunities": fail(503, "database not configured"), "GET /api/watchlist": fail(503, "x") });
  renderApp("/");
  expect(await screen.findByRole("alert")).toHaveTextContent("no database");
});
