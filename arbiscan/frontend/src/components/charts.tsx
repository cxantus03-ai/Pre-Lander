import type { ReactNode } from "react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { PriceData } from "../api";
import { exName, time } from "../format";
import { Empty } from "./ui";

export const C = { grid: "#1f252c", axis: "#5b6470", pos: "#3fb37f", warn: "#d4a23a", neg: "#e05d5d", accent: "#5aa9e6", violet: "#a48be0", gray: "#8a94a0" };
const tick = { fill: C.axis, fontSize: 10 };
const tooltipStyle = { contentStyle: { background: "#111418", border: "1px solid #2a313a", fontSize: 11, borderRadius: 2 }, labelStyle: { color: "#8a94a0" } };

export function ChartBox({ height = 220, children }: { height?: number; children: ReactNode }) {
  return <div style={{ width: "100%", height }}>{children}</div>;
}

export function LinesChart({ data, x, lines, height = 220, yFmt, xFmt, refY }: {
  data: Record<string, unknown>[]; x: string; lines: { key: string; name: string; color: string; dashed?: boolean }[];
  height?: number; yFmt?: (v: number) => string; xFmt?: (v: string) => string; refY?: number;
}) {
  if (!data.length) return <Empty>No history yet — data accumulates while the scanner runs.</Empty>;
  return (
    <ChartBox height={height}>
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 6, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid stroke={C.grid} strokeDasharray="2 4" />
          <XAxis dataKey={x} tick={tick} tickFormatter={xFmt} minTickGap={40} stroke={C.grid} />
          <YAxis tick={tick} tickFormatter={yFmt} width={48} stroke={C.grid} domain={["auto", "auto"]} />
          <Tooltip {...tooltipStyle} labelFormatter={(l) => (xFmt ? xFmt(String(l)) : String(l))} formatter={(v: number) => (yFmt ? yFmt(v) : v)} />
          {refY !== undefined && <ReferenceLine y={refY} stroke={C.gray} strokeDasharray="3 3" />}
          <Legend wrapperStyle={{ fontSize: 10 }} />
          {lines.map((l) => (
            <Line key={l.key} type="stepAfter" dataKey={l.key} name={l.name} stroke={l.color} dot={false} strokeWidth={1.4} strokeDasharray={l.dashed ? "4 3" : undefined} isAnimationActive={false} connectNulls />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </ChartBox>
  );
}

export function BarsChart({ data, x, bars, height = 220, stacked = false, yFmt, layout = "horizontal" }: {
  data: Record<string, unknown>[]; x: string; bars: { key: string; name: string; color: string }[]; height?: number; stacked?: boolean;
  yFmt?: (v: number) => string; layout?: "horizontal" | "vertical";
}) {
  if (!data.length) return <Empty>No data in range.</Empty>;
  const vertical = layout === "vertical";
  return (
    <ChartBox height={height}>
      <ResponsiveContainer>
        <BarChart data={data} layout={layout} margin={{ top: 6, right: 8, left: vertical ? 8 : 0, bottom: 0 }}>
          <CartesianGrid stroke={C.grid} strokeDasharray="2 4" />
          {vertical ? (
            <>
              <XAxis type="number" tick={tick} stroke={C.grid} tickFormatter={yFmt} />
              <YAxis type="category" dataKey={x} tick={tick} width={120} stroke={C.grid} />
            </>
          ) : (
            <>
              <XAxis dataKey={x} tick={tick} stroke={C.grid} minTickGap={10} />
              <YAxis tick={tick} width={44} stroke={C.grid} tickFormatter={yFmt} />
            </>
          )}
          <Tooltip {...tooltipStyle} cursor={{ fill: "#161a1f" }} formatter={(v: number) => (yFmt ? yFmt(v) : v)} />
          {bars.length > 1 && <Legend wrapperStyle={{ fontSize: 10 }} />}
          {bars.map((b) => <Bar key={b.key} dataKey={b.key} name={b.name} fill={b.color} stackId={stacked ? "s" : undefined} isAnimationActive={false} maxBarSize={28} />)}
        </BarChart>
      </ResponsiveContainer>
    </ChartBox>
  );
}

export function ProfitCurve({ curve }: { curve: { qty: string; outlay: string; worst_profit: string }[] }) {
  const data = curve.map((p) => ({ qty: Number(p.qty), profit: Number(p.worst_profit), outlay: Number(p.outlay) }));
  if (!data.length) return <Empty>No profitable depth curve.</Empty>;
  return (
    <ChartBox height={200}>
      <ResponsiveContainer>
        <AreaChart data={data} margin={{ top: 6, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid stroke={C.grid} strokeDasharray="2 4" />
          <XAxis dataKey="qty" tick={tick} stroke={C.grid} type="number" domain={["dataMin", "dataMax"]} />
          <YAxis tick={tick} width={52} stroke={C.grid} tickFormatter={(v) => `$${v}`} />
          <Tooltip {...tooltipStyle} labelFormatter={(l) => `${l} contracts`} formatter={(v: number, nm: string) => [`$${v.toFixed(2)}`, nm]} />
          <ReferenceLine y={0} stroke={C.neg} strokeDasharray="3 3" />
          <Area type="linear" dataKey="profit" name="Worst-case modelled profit" stroke={C.pos} fill={C.pos} fillOpacity={0.12} isAnimationActive={false} />
        </AreaChart>
      </ResponsiveContainer>
    </ChartBox>
  );
}

const SERIES_COLORS = [C.pos, C.accent, C.warn, C.violet];

export function PriceHistoryChart({ prices, side = "both" }: { prices: PriceData | undefined; side?: "both" | "yes" }) {
  const keys = Object.keys(prices?.series ?? {});
  if (!prices || !keys.length) return <Empty>No stored price history yet (sampled every minute while a book is tracked).</Empty>;
  const merged = new Map<string, Record<string, unknown>>();
  keys.forEach((k, i) => {
    for (const p of prices.series[k]) {
      const t = p.ts.slice(0, 19);
      const row = merged.get(t) ?? { ts: p.ts };
      row[`yes_ask_${i}`] = p.yes_ask;
      if (side === "both") row[`no_ask_${i}`] = p.no_ask;
      merged.set(t, row);
    }
  });
  const data = [...merged.values()].sort((a, b) => String(a.ts).localeCompare(String(b.ts)));
  const label = (k: string) => {
    const [ex, id] = k.split(":");
    return `${exName(ex)} ${id?.slice(0, 18) ?? ""}`;
  };
  const lines = keys.flatMap((k, i) => [
    { key: `yes_ask_${i}`, name: `${label(k)} YES ask`, color: SERIES_COLORS[i % 4] },
    ...(side === "both" ? [{ key: `no_ask_${i}`, name: `${label(k)} NO ask`, color: SERIES_COLORS[i % 4], dashed: true }] : []),
  ]);
  return <LinesChart data={data} x="ts" lines={lines} xFmt={(v) => time(v)} yFmt={(v) => v.toFixed(2)} />;
}
