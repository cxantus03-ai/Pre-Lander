import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Trade } from "../api";
import { BarsChart, C, LinesChart } from "../components/charts";
import { Badge, btnCls, btnPrimary, Check, Empty, ErrorBox, Field, inputCls, Metric, NumInput, Panel } from "../components/ui";
import { CATEGORIES, catLabel, date, dt, duration, exName, pct, signClass, usd } from "../format";
import { useLive } from "../live";

interface OppAnalytics {
  total: number; verified: number; potential: number; per_day: Record<string, number | string>[];
  average_net_roi: number | null; highest_net_roi: number | null; average_duration_s: number | null; disappeared: number;
  invalid_fees: number; invalid_settlement: number; total_theoretical_profit: number;
  by_exchange_pair: { exchange_pair: string; count: number }[]; by_category: { category: string; count: number; theoretical_profit: number }[];
  by_strategy: { strategy: string; count: number }[]; duration_histogram: { bucket: string; count: number }[]; note: string;
}
interface Perf { trades: number; open: number; settled: number; realized_pnl: number; theoretical_profit_of_recorded: number; capital_in_open_positions: number; win_rate?: number | null; cumulative: { ts: string; cum: number }[]; note: string }
interface Ended { id: string; title: string; strategy: string; classification: string; exchanges: string[]; first_seen: string; ended_at: string | null; end_reason: string | null; peak_net_roi: number; peak_profit: number; is_simulated: boolean }

const isoDay = (d: Date) => d.toISOString().slice(0, 10);

export default function AnalyticsPage() {
  const { summary } = useLive();
  const [start, setStart] = useState(isoDay(new Date(Date.now() - 30 * 86400e3)));
  const [end, setEnd] = useState(isoDay(new Date()));
  const [exchange, setExchange] = useState("");
  const [category, setCategory] = useState("");
  const [strategy, setStrategy] = useState("");
  const [includeSim, setIncludeSim] = useState(true);
  const [a, setA] = useState<OppAnalytics | null>(null);
  const [signals, setSignals] = useState<Record<string, number | string>[]>([]);
  const [ended, setEnded] = useState<Ended[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    const s = new Date(start + "T00:00:00").toISOString(), e = new Date(end + "T23:59:59").toISOString();
    api.get<OppAnalytics>("/analytics/opportunities", { start: s, end: e, exchange, category, strategy, include_simulated: includeSim })
      .then((r) => { setA(r); setError(null); }).catch(setError);
    api.get<Record<string, number | string>[]>("/analytics/signals", { start: s, end: e }).then(setSignals).catch(() => {});
    api.get<Ended[]>("/opportunities/recent-ended", { limit: 50 }).then(setEnded).catch(() => {});
  }, [start, end, exchange, category, strategy, includeSim, tick]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-2 justify-between">
        <h1 className="text-base font-semibold text-gray-100">Historical Analytics</h1>
        <div className="flex flex-wrap items-end gap-2">
          <Field label="From"><input type="date" className={inputCls} value={start} onChange={(e) => setStart(e.target.value)} /></Field>
          <Field label="To"><input type="date" className={inputCls} value={end} onChange={(e) => setEnd(e.target.value)} /></Field>
          <Field label="Exchange"><select className={inputCls} value={exchange} onChange={(e) => setExchange(e.target.value)}><option value="">All</option>{(summary?.exchanges ?? []).map((x) => <option key={x} value={x}>{exName(x)}</option>)}</select></Field>
          <Field label="Category"><select className={inputCls} value={category} onChange={(e) => setCategory(e.target.value)}><option value="">All</option>{CATEGORIES.map((c) => <option key={c} value={c}>{catLabel(c)}</option>)}</select></Field>
          <Field label="Market type"><select className={inputCls} value={strategy} onChange={(e) => setStrategy(e.target.value)}>
            <option value="">All</option><option value="cross_exchange_binary">Cross-exchange binary</option><option value="same_exchange_binary">Same-market binary</option>
            <option value="multi_outcome_yes">Multi-outcome (YES set)</option><option value="multi_outcome_no">Multi-outcome (NO set)</option><option value="implication">Implication</option></select></Field>
          <Check label="Include simulated" checked={includeSim} onChange={setIncludeSim} />
          <button className={btnCls} onClick={() => setTick((t) => t + 1)}>Refresh</button>
        </div>
      </div>
      <ErrorBox error={error} />

      <section className="space-y-3">
        <div className="flex items-center gap-2"><Badge kind="potential">theoretical</Badge><h2 className="text-sm text-gray-100">Detected opportunities — modelled from market data, not executed</h2></div>
        <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-2">
          <Metric label="Detected" value={a?.total ?? "—"} sub={`${a?.verified ?? 0} verified · ${a?.potential ?? 0} potential`} />
          <Metric label="Avg peak net ROI" value={pct(a?.average_net_roi)} />
          <Metric label="Highest net ROI" value={pct(a?.highest_net_roi)} tone="pos" />
          <Metric label="Avg duration" value={duration(a?.average_duration_s)} />
          <Metric label="Disappeared" value={a?.disappeared ?? "—"} tone="neg" sub="before end of window" />
          <Metric label="Invalidated by fees" value={a?.invalid_fees ?? "—"} tone="warn" sub="gross edge, net ≤ 0" />
          <Metric label="Invalid: settlement" value={a?.invalid_settlement ?? "—"} tone="warn" />
          <Metric label="Total theoretical profit" value={usd(a?.total_theoretical_profit)} sub="peak, depth-limited" />
        </div>
        <div className="grid gap-3 lg:grid-cols-2">
          <Panel title="Opportunities per day"><BarsChart data={a?.per_day ?? []} x="day" stacked bars={[{ key: "verified", name: "Verified", color: C.pos }, { key: "potential", name: "Potential", color: C.warn }]} /></Panel>
          <Panel title="Net ROI per day (peak)"><LinesChart data={a?.per_day ?? []} x="day" yFmt={(v) => `${v.toFixed(2)}%`} lines={[{ key: "avg_net_roi", name: "Average", color: C.accent }, { key: "max_net_roi", name: "Highest", color: C.pos }]} /></Panel>
          <Panel title="Duration of profitable windows"><BarsChart data={a?.duration_histogram ?? []} x="bucket" bars={[{ key: "count", name: "Opportunities", color: C.accent }]} /></Panel>
          <Panel title="Arbitrage signal frequency (profitable snapshots / hour)"><BarsChart data={signals} x="ts" stacked bars={[{ key: "verified", name: "Verified", color: C.pos }, { key: "potential", name: "Potential", color: C.warn }]} /></Panel>
          <Panel title="Most frequent exchange pairs"><BarsChart data={(a?.by_exchange_pair ?? []).map((r) => ({ ...r, exchange_pair: r.exchange_pair.split(" / ").map(exName).join(" / ") }))} x="exchange_pair" layout="vertical" bars={[{ key: "count", name: "Opportunities", color: C.violet }]} /></Panel>
          <Panel title="Most active categories (count · theoretical profit)"><BarsChart data={(a?.by_category ?? []).map((r) => ({ ...r, category: catLabel(r.category) }))} x="category" bars={[{ key: "count", name: "Count", color: C.accent }, { key: "theoretical_profit", name: "Theoretical $", color: C.pos }]} /></Panel>
        </div>
        <Panel title="Recently disappeared opportunities" bodyClass="p-0">
          <div className="overflow-auto max-h-[360px]">
            <table className="dense">
              <thead><tr><th>Event</th><th>Type</th><th>Exchanges</th><th className="num">Peak ROI</th><th className="num">Peak profit</th><th className="num">Lifetime</th><th>Ended</th><th>Reason</th></tr></thead>
              <tbody>
                {ended.map((e) => (
                  <tr key={e.id}>
                    <td className="max-w-[320px] truncate" title={e.title}><Badge kind={e.classification} /> {e.is_simulated && <Badge kind="sim">sim</Badge>} {e.title}</td>
                    <td className="text-mute">{e.strategy}</td><td>{[...new Set(e.exchanges)].map(exName).join(", ")}</td>
                    <td className="num">{pct(e.peak_net_roi)}</td><td className="num">{usd(e.peak_profit)}</td>
                    <td className="num">{e.ended_at ? duration((new Date(e.ended_at).getTime() - new Date(e.first_seen).getTime()) / 1000) : "—"}</td>
                    <td className="text-mute">{dt(e.ended_at)}</td><td className="text-mute max-w-[240px] truncate" title={e.end_reason ?? ""}>{e.end_reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!ended.length && <Empty>None yet.</Empty>}
          </div>
        </Panel>
      </section>

      <RealizedSection />
    </div>
  );
}

interface LegRow { exchange: string; side: string; price: string; qty: string; fees: string }

function RealizedSection() {
  const [perf, setPerf] = useState<Perf | null>(null);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [title, setTitle] = useState("");
  const [oppId, setOppId] = useState("");
  const [theo, setTheo] = useState("");
  const [notes, setNotes] = useState("");
  const [legs, setLegs] = useState<LegRow[]>([{ exchange: "polymarket", side: "YES", price: "", qty: "", fees: "0" }, { exchange: "kalshi", side: "NO", price: "", qty: "", fees: "0" }]);
  const [settling, setSettling] = useState<number | null>(null);
  const [sres, setSres] = useState({ result: "YES", payout: "", fees: "0", notes: "" });

  const load = () => {
    api.get<Perf>("/analytics/performance").then(setPerf).catch(setError);
    api.get<Trade[]>("/trades").then(setTrades).catch(setError);
  };
  useEffect(load, []);

  const create = async () => {
    setError(null);
    try {
      await api.post("/trades", {
        title, opportunity_id: oppId || undefined, theoretical_profit: theo === "" ? undefined : Number(theo), notes: notes || undefined,
        legs: legs.filter((l) => l.price && l.qty).map((l) => ({ exchange: l.exchange, side: l.side, price: Number(l.price), qty: Number(l.qty), fees: Number(l.fees || 0) })),
      });
      setTitle(""); setTheo(""); setNotes(""); setOppId("");
      load();
    } catch (e) { setError(e); }
  };
  const settle = async (id: number) => {
    try {
      await api.post(`/trades/${id}/settle`, { result: sres.result, payout_received: Number(sres.payout || 0), additional_fees: Number(sres.fees || 0), notes: sres.notes || undefined });
      setSettling(null); load();
    } catch (e) { setError(e); }
  };
  const del = async (id: number) => { try { await api.del(`/trades/${id}`); load(); } catch (e) { setError(e); } };

  return (
    <section className="space-y-3 border-t-2 border-accent/30 pt-4">
      <div className="flex items-center gap-2"><Badge kind="verified">realized</Badge><h2 className="text-sm text-gray-100">Recorded performance — only trades and settlements you entered manually</h2></div>
      <p className="text-[11px] text-mute">A detected opportunity is never counted here. Realized P&L = payout received − premiums − fees you recorded.</p>
      <ErrorBox error={error} />
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
        <Metric label="Recorded trades" value={perf?.trades ?? "—"} sub={`${perf?.open ?? 0} open · ${perf?.settled ?? 0} settled`} />
        <Metric label="Realized P&L" value={usd(perf?.realized_pnl)} tone={(perf?.realized_pnl ?? 0) > 0 ? "pos" : (perf?.realized_pnl ?? 0) < 0 ? "neg" : "mute"} />
        <Metric label="Theoretical (of recorded)" value={usd(perf?.theoretical_profit_of_recorded)} sub="what the model predicted" />
        <Metric label="Capital in open positions" value={usd(perf?.capital_in_open_positions)} />
        <Metric label="Positive-P&L rate" value={perf?.win_rate === null || perf?.win_rate === undefined ? "—" : pct(perf.win_rate * 100, 0)} />
        <Metric label="Model vs realized gap" value={perf && perf.settled ? usd(perf.realized_pnl - perf.theoretical_profit_of_recorded) : "—"} />
      </div>
      <div className="grid gap-3 lg:grid-cols-2">
        <Panel title="Cumulative realized P&L"><LinesChart data={perf?.cumulative ?? []} x="ts" xFmt={date} yFmt={(v) => `$${v.toFixed(2)}`} refY={0} lines={[{ key: "cum", name: "Realized P&L", color: C.pos }]} /></Panel>
        <Panel title="Record an accepted position">
          <div className="space-y-2">
            <div className="grid grid-cols-2 gap-2">
              <Field label="Title"><input className={inputCls} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Fed Dec cut — PM YES / Kalshi NO" /></Field>
              <Field label="Opportunity id (optional)"><input className={inputCls} value={oppId} onChange={(e) => setOppId(e.target.value)} /></Field>
              <Field label="Theoretical profit $ (optional)"><NumInput value={theo} onChange={setTheo} /></Field>
              <Field label="Notes"><input className={inputCls} value={notes} onChange={(e) => setNotes(e.target.value)} /></Field>
            </div>
            <div className="text-[10px] uppercase tracking-wider text-mute">Legs actually filled</div>
            {legs.map((l, i) => (
              <div key={i} className="grid grid-cols-[1fr_70px_1fr_1fr_1fr_auto] gap-1">
                <input className={inputCls} value={l.exchange} onChange={(e) => setLegs(legs.map((x, j) => (j === i ? { ...x, exchange: e.target.value } : x)))} aria-label="exchange" />
                <input className={inputCls} value={l.side} onChange={(e) => setLegs(legs.map((x, j) => (j === i ? { ...x, side: e.target.value } : x)))} aria-label="side" />
                <NumInput value={l.price} placeholder="price" onChange={(v) => setLegs(legs.map((x, j) => (j === i ? { ...x, price: v } : x)))} />
                <NumInput value={l.qty} placeholder="qty" onChange={(v) => setLegs(legs.map((x, j) => (j === i ? { ...x, qty: v } : x)))} />
                <NumInput value={l.fees} placeholder="fees $" onChange={(v) => setLegs(legs.map((x, j) => (j === i ? { ...x, fees: v } : x)))} />
                <button className="text-mute px-1" onClick={() => setLegs(legs.filter((_, j) => j !== i))} disabled={legs.length === 1}>×</button>
              </div>
            ))}
            <div className="flex gap-2">
              <button className={btnCls} onClick={() => setLegs([...legs, { exchange: "", side: "", price: "", qty: "", fees: "0" }])}>+ leg</button>
              <button className={btnPrimary} disabled={!title || !legs.some((l) => l.price && l.qty)} onClick={create}>Record position</button>
            </div>
          </div>
        </Panel>
      </div>
      <Panel title="Recorded positions" bodyClass="p-0">
        <div className="overflow-x-auto">
          <table className="dense">
            <thead><tr><th>Position</th><th>Legs</th><th className="num">Cost</th><th className="num">Fees</th><th className="num">Theoretical</th><th>Entered</th><th>Settlement</th><th className="num">Realized P&L</th><th></th></tr></thead>
            <tbody>
              {trades.map((t) => (
                <tr key={t.id}>
                  <td className="max-w-[260px] truncate text-gray-100" title={t.title}>{t.title}{t.opportunity_id && <> · <Link className="text-accent" to={`/opportunity/${t.opportunity_id}`}>opp</Link></>}</td>
                  <td className="font-mono text-[11px] text-mute">{t.legs.map((l) => `${exName(l.exchange)} ${l.side} ${Number(l.price).toFixed(3)}×${Number(l.qty)}`).join(" | ")}</td>
                  <td className="num">{usd(t.total_cost)}</td><td className="num">{usd(t.total_fees)}</td><td className="num">{usd(t.theoretical_profit)}</td>
                  <td className="text-mute">{dt(t.entered_at)}</td>
                  <td>{t.settlement ? <span className="text-gray-300">{t.settlement.result} · {usd(t.settlement.payout_received)}</span> : settling === t.id ? (
                    <div className="flex gap-1 items-center">
                      <input className={`${inputCls} w-16`} value={sres.result} onChange={(e) => setSres({ ...sres, result: e.target.value })} aria-label="result" />
                      <input className={`${inputCls} w-20`} type="number" placeholder="payout $" value={sres.payout} onChange={(e) => setSres({ ...sres, payout: e.target.value })} />
                      <input className={`${inputCls} w-16`} type="number" placeholder="fees" value={sres.fees} onChange={(e) => setSres({ ...sres, fees: e.target.value })} />
                      <button className={btnPrimary} onClick={() => settle(t.id)}>Save</button>
                    </div>
                  ) : <button className={btnCls} onClick={() => { setSettling(t.id); setSres({ result: "YES", payout: "", fees: "0", notes: "" }); }}>Record settlement</button>}</td>
                  <td className={`num ${signClass(t.settlement?.realized_pnl)}`}>{t.settlement ? usd(t.settlement.realized_pnl) : <span className="text-mute">open</span>}</td>
                  <td><button className="text-neg text-xs" onClick={() => del(t.id)}>delete</button></td>
                </tr>
              ))}
            </tbody>
          </table>
          {!trades.length && <Empty>No recorded positions. Record only positions you actually entered.</Empty>}
        </div>
      </Panel>
    </section>
  );
}
