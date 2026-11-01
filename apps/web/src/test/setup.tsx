import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

// lightweight-charts needs a canvas, which jsdom does not have; the charts are exercised in the
// browser (see the screenshots in the PR), the tests check what is passed to them.
vi.mock("../components/Charts", () => ({
  PriceChart: ({ bars }: { bars: unknown[] }) => <div data-testid="price-chart">{bars.length} bars</div>,
  LineChart: ({
    points,
    markers = [],
  }: {
    points: { value: number | null }[];
    markers?: unknown[];
  }) => (
    <div data-testid="line-chart">
      {points.filter((p) => p.value != null).length} points,{" "}
      {points.filter((p) => p.value == null).length} gaps, {markers.length} markers
    </div>
  ),
}));

// jsdom has no ResizeObserver (the real charts are mocked, but keep it available)
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
