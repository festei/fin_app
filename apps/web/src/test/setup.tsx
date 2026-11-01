import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

// lightweight-charts needs a canvas, which jsdom does not have; the charts are exercised in the
// browser (see the screenshots in the PR), the tests check what is passed to them.
vi.mock("../components/Charts", () => ({
  PriceChart: ({ bars }: { bars: unknown[] }) => <div data-testid="price-chart">{bars.length} bars</div>,
  LineChart: ({ points, markers = [] }: { points: unknown[]; markers?: unknown[] }) => (
    <div data-testid="line-chart">
      {points.length} points, {markers.length} markers
    </div>
  ),
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
