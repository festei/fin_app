import {
  ColorType,
  createChart,
  type IChartApi,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";
import { useEffect, useRef } from "react";
import type { Bar } from "../api/types";

const COLORS = {
  text: "#8b93a3",
  grid: "#1c222d",
  up: "#3fb950",
  down: "#f85149",
  line: "#58a6ff",
  area: "rgba(88,166,255,0.12)",
};

function useChart(height: number, build: (chart: IChartApi) => void, deps: unknown[]) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const chart = createChart(el, {
      height,
      width: el.clientWidth,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: COLORS.text,
      },
      grid: { vertLines: { color: COLORS.grid }, horzLines: { color: COLORS.grid } },
      rightPriceScale: { borderColor: COLORS.grid },
      timeScale: { borderColor: COLORS.grid },
    });
    build(chart);
    chart.timeScale().fitContent();
    const resize = () => chart.applyOptions({ width: el.clientWidth });
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      chart.remove();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return ref;
}

const toDay = (iso: string): Time => iso.slice(0, 10) as Time;

/** Candles from daily bars (adjusted prices). Bars sharing a date are collapsed to the last. */
export function PriceChart({ bars }: { bars: Bar[] }) {
  const ref = useChart(
    320,
    (chart) => {
      const series = chart.addCandlestickSeries({
        upColor: COLORS.up,
        downColor: COLORS.down,
        borderVisible: false,
        wickUpColor: COLORS.up,
        wickDownColor: COLORS.down,
      });
      const byDay = new Map<string, Bar>();
      for (const b of bars) byDay.set(b.ts.slice(0, 10), b);
      series.setData(
        [...byDay.entries()].map(([d, b]) => ({
          time: d as Time,
          open: b.open ?? b.close,
          high: b.high ?? b.close,
          low: b.low ?? b.close,
          close: b.close,
        })),
      );
    },
    [bars],
  );
  return <div ref={ref} className="chart" data-testid="price-chart" />;
}

export interface Point {
  time: string; // ISO date or timestamp
  value: number;
}

export interface Marker {
  time: string;
  text: string;
  tone: "good" | "bad" | "warn";
}

/** A line over time (equity curve, conviction history) with optional signal markers and
 *  horizontal reference lines (for example the buy and sell lines). */
export function LineChart({
  points,
  markers = [],
  lines = [],
  range,
  height = 220,
}: {
  points: Point[];
  markers?: Marker[];
  lines?: { value: number; title: string }[];
  range?: [number, number]; // fixed value axis, e.g. 0-100 for conviction
  height?: number;
}) {
  const ref = useChart(
    height,
    (chart) => {
      const series = chart.addAreaSeries({
        lineColor: COLORS.line,
        topColor: COLORS.area,
        bottomColor: "rgba(88,166,255,0)",
        lineWidth: 2,
        ...(range && {
          autoscaleInfoProvider: () => ({
            priceRange: { minValue: range[0], maxValue: range[1] },
          }),
        }),
      });
      const byDay = new Map<string, number>();
      for (const p of points) byDay.set(p.time.slice(0, 10), p.value);
      series.setData([...byDay.entries()].map(([d, v]) => ({ time: d as Time, value: v })));
      for (const l of lines) {
        series.createPriceLine({
          price: l.value,
          title: l.title,
          color: COLORS.text,
          lineStyle: 2,
          lineWidth: 1,
          axisLabelVisible: false,
        });
      }
      const times = new Set(byDay.keys());
      const marks = markers
        .filter((m) => times.has(m.time.slice(0, 10)))
        .map((m): SeriesMarker<Time> => ({
          time: toDay(m.time),
          position: m.tone === "bad" ? "aboveBar" : "belowBar",
          shape: m.tone === "bad" ? "arrowDown" : "arrowUp",
          color: m.tone === "bad" ? COLORS.down : m.tone === "good" ? COLORS.up : COLORS.line,
          text: m.text,
        }))
        .sort((a, b) => String(a.time).localeCompare(String(b.time)));
      series.setMarkers(marks);
    },
    [points, markers, lines, range, height],
  );
  return <div ref={ref} className="chart" data-testid="line-chart" />;
}
