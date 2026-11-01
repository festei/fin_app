import { afterEach, expect, test, vi } from "vitest";
import { ApiError, request } from "../api/client";
import { age, day, money, pct, tone } from "./format";

afterEach(() => vi.restoreAllMocks());

test("formats money, percentages and dates", () => {
  expect(money(1234.5)).toBe("$1,234.50");
  expect(money(null)).toBe("–");
  expect(pct(3.456)).toBe("+3.5%");
  expect(pct(-2, 2)).toBe("-2.00%");
  expect(day("2026-10-30T21:00:00")).toBe("2026-10-30");
});

test("age treats timestamps without a zone as UTC", () => {
  const now = Date.parse("2026-11-01T12:00:00Z");
  expect(age("2026-11-01T11:30:00", now)).toBe("just now");
  expect(age("2026-11-01T06:00:00", now)).toBe("6h ago");
  expect(age("2026-10-29T12:00:00+00:00", now)).toBe("3d ago");
  expect(age(null, now)).toBe("–");
});

test("conviction tone follows the buy (70) and sell (50) lines", () => {
  expect([tone(null), tone(49.9), tone(50), tone(69.9), tone(70)]).toEqual([
    "none", "bad", "warn", "warn", "good",
  ]);
});

test("request never sends credentials and surfaces the backend's message", async () => {
  const spy = vi
    .spyOn(globalThis, "fetch")
    .mockResolvedValueOnce(new Response("{}", { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "unknown ticker ZZZ" }), { status: 404 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ detail: [{ msg: "bad a" }, { msg: "bad b" }] }), { status: 422 }))
    .mockResolvedValueOnce(new Response("<html>", { status: 502 }));
  await request("/api/x", { method: "POST", body: "{}" });
  const sent = spy.mock.calls[0][1]!.headers as Record<string, string>;
  expect(Object.keys(sent).map((k) => k.toLowerCase())).not.toContain("authorization");
  expect(sent["Content-Type"]).toBe("application/json");
  await expect(request("/api/x")).rejects.toMatchObject({ status: 404, detail: "unknown ticker ZZZ" });
  await expect(request("/api/x")).rejects.toThrow("bad a; bad b");
  const err = (await request("/api/x").catch((e: unknown) => e)) as ApiError;
  expect(err).toBeInstanceOf(ApiError);
  expect(err.detail).toBe("request failed (502)");
});
