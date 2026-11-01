import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";
import { mockApi, renderApp } from "../test/utils";
import { inAppBody } from "./Alerts";

const alert = (id: number, extra = {}) => ({
  id, ticker: "ACME", kind: "buy", severity: "high", conviction: 80, title: `ACME: BUY #${id}`,
  body: "Scores: graham 60\nUse /why ACME for the full breakdown.", created_at: new Date().toISOString(),
  read: false, deliveries: { in_app: "sent", telegram: "failed" }, ...extra,
});

test("lists alerts with delivery status and hides the Telegram-only hint", async () => {
  mockApi({ "GET /api/alerts": { items: [alert(2), alert(1, { read: true })], next_before_id: null } });
  renderApp("/alerts");
  expect(await screen.findByText("ACME: BUY #2")).toBeInTheDocument();
  expect(screen.getAllByText("in_app: sent · telegram: failed").length).toBe(2);
  expect(screen.queryByText(/Use \/why/)).not.toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: "Mark read" })).toHaveLength(1); // only the unread one
});

test("marks an alert as read", async () => {
  const calls = mockApi({
    "GET /api/alerts": { items: [alert(5)], next_before_id: null },
    "POST /api/alerts/5/read": { id: 5, read: true },
  });
  renderApp("/alerts");
  await userEvent.click(await screen.findByRole("button", { name: "Mark read" }));
  expect(calls.some((c) => c.method === "POST" && c.url === "/api/alerts/5/read")).toBe(true);
});

test("unread-only is sent to the API and an empty feed is explained", async () => {
  const calls = mockApi({ "GET /api/alerts": { items: [], next_before_id: null } });
  renderApp("/alerts");
  expect(await screen.findByText("No alerts yet.")).toBeInTheDocument();
  await userEvent.click(screen.getByLabelText("Unread only"));
  expect(await screen.findByText("Nothing unread.")).toBeInTheDocument();
  expect(calls.at(-1)!.url).toContain("unread_only=true");
});

test("loads older alerts with the cursor", async () => {
  const calls = mockApi({
    "GET /api/alerts": (_init: unknown, url: string) =>
      url.includes("before_id=2")
        ? { items: [alert(1)], next_before_id: null }
        : { items: [alert(2)], next_before_id: 2 },
  });
  renderApp("/alerts");
  await userEvent.click(await screen.findByRole("button", { name: "Older alerts" }));
  expect(await screen.findByText("ACME: BUY #1")).toBeInTheDocument();
  expect(calls.at(-1)!.url).toContain("before_id=2");
  expect(screen.queryByRole("button", { name: "Older alerts" })).not.toBeInTheDocument();
});

test("inAppBody only drops the bot hint line", () => {
  expect(inAppBody("a\nUse /why X for the full breakdown.\nb")).toBe("a\nb");
});
