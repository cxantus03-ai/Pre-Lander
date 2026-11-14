import { useEffect, useMemo, useState } from "react";
import { api, type CalcResult, type Capital } from "../api";
import { exName, n, pct, price, qty, signClass, usd } from "../format";
import { useLive } from "../live";
import { Badge, ErrorBox, Field, KV, NumInput, Panel, useDebounced } from "./ui";

export interface CalcInputs {
  total_capital: string;
  balances: Record<string, string>;
  max_allocation: string;
  max_pct_bankroll: string;
  min_profit: string;
  min_roi_pct: string;
  max_slippage_pct: string;
  max_qty: string;
}

export const emptyInputs = (): CalcInputs => ({
  total_capital: "", balances: {}, max_allocation: "", max_pct_bankroll: "", min_profit: "", min_roi_pct: "", max_slippage_pct: "", max_qty: "",
});

export function inputsToBody(i: CalcInputs) {
  const num = (s: string) => (s.trim() === "" ? undefined : Number(s));
  const balances: Record<string, number> = {};
  for (const [k, v] of Object.entries(i.balances)) if (v.trim() !== "") balances[k] = Number(v);
  return {
    total_capital: num(i.total_capital), exchange_balances: balances, max_allocation: num(i.max_allocation),
    max_pct_bankroll: num(i.max_pct_bankroll), min_profit: num(i.min_profit), min_roi_pct: num(i.min_roi_pct),
    max_slippage_pct: num(i.max_slippage_pct), max_qty: num(i.max_qty),
  };
}

/** Prefill from saved capital settings for the given exchanges. */
export function useCapitalPrefill(exchanges: string[], set: (f: (p: CalcInputs) => CalcInputs) => void) {
  const key = exchanges.join(",");
  useEffect(() => {
    api.get<Capital>("/capital").then((c) => {
      set((p) => {
        const balances = { ...p.balances };
        for (const ex of exchanges) if (balances[ex] === undefined) balances[ex] = c.exchange_balances[ex] ?? "0";
        return {
          ...p, balances,
          total_capital: p.total_capital || String(Number(c.total_capital)),
          max_allocation: p.max_allocation || (c.max_allocation_per_trade ? String(Number(c.max_allocation_per_trade)) : ""),
          max_pct_bankroll: p.max_pct_bankroll || (c.max_pct_per_trade ? String(Number(c.max_pct_per_trade)) : ""),
          min_profit: p.min_profit || (Number(c.min_profit) ? String(Number(c.min_profit)) : ""),
          min_roi_pct: p.min_roi_pct || (Number(c.min_roi_pct) ? String(Number(c.min_roi_pct)) : ""),
          max_slippage_pct: p.max_slippage_pct || (c.max_slippage_pct ? String(Number(c.max_slippage_pct)) : ""),
        };
      });
    }).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
}

export function CalcInputsForm({ value, onChange, exchanges }: { value: CalcInputs; onChange: (v: CalcInputs) => void; exchanges: string[] }) {
  const set = (k: keyof CalcInputs) => (v: string) => onChange({ ...value, [k]: v });
  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
      <Field label="Total capital $"><NumInput value={value.total_capital} onChange={set("total_capital")} min={0} /></Field>
      {exchanges.map((ex) => (
        <Field key={ex} label={`${exName(ex)} balance $`}>
          <NumInput value={value.balances[ex] ?? ""} min={0} onChange={(v) => onChange({ ...value, balances: { ...value.balances, [ex]: v } })} />
        </Field>
      ))}
      <Field label="Max allocation $"><NumInput value={value.max_allocation} onChange={set("max_allocation")} min={0} placeholder="none" /></Field>
      <Field label="Max % of bankroll"><NumInput value={value.max_pct_bankroll} onChange={set("max_pct_bankroll")} min={0} placeholder="none" /></Field>
      <Field label="Min $ profit"><NumInput value={value.min_profit} onChange={set("min_profit")} min={0} placeholder="0" /></Field>
      <Field label="Min net ROI %"><NumInput value={value.min_roi_pct} onChange={set("min_roi_pct")} placeholder="0" /></Field>
      <Field label="Max slippage %"><NumInput value={value.max_slippage_pct} onChange={set("max_slippage_pct")} min={0} placeholder="none" /></Field>
      <Field label="Max contracts"><NumInput value={value.max_qty} onChange={set("max_qty")} min={0} placeholder="none" /></Field>
    </div>
  );
}

export function CalcResultView({ r }: { r: CalcResult }) {
  const { disclaimer } = useLive();
  const ev = r.recommended_eval;
  const byEx = ev?.outlay_by_exchange ?? {};
  const feesByEx = useMemo(() => {
    const out: Record<string, number> = {};
    ev?.legs.forEach((l) => { out[l.exchange] = (out[l.exchange] ?? 0) + Number(l.fees); });
    return out;
  }, [ev]);
  const executable = !!ev && n(r.recommended_qty)! > 0;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        {executable ? <Badge kind="pass">feasible size found</Badge> : <Badge kind="fail">no profitable feasible size</Badge>}
        <span className="text-xs text-mute">limiting factor: <span className="text-gray-200 font-mono">{r.limiting_factor}</span></span>
      </div>
      {r.reasons.length > 0 && (
        <ul className="text-xs text-warn list-disc pl-4 space-y-0.5">{r.reasons.map((x, i) => <li key={i}>{x}</li>)}</ul>
      )}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        <Stat label="Contracts per leg" value={qty(r.recommended_qty)} />
        <Stat label="Total acquisition cost" value={usd(ev?.total_outlay)} />
        <Stat label="Min contractual payout" value={usd(ev?.guaranteed_payout)} hint="if both contracts settle compatibly" />
        <Stat label="Worst-case modelled profit" value={usd(ev?.worst_case_profit)} tone={signClass(ev?.worst_case_profit)} />
        <Stat label="Net ROI" value={pct(ev?.net_roi_pct, 3)} tone={signClass(ev?.net_roi_pct)} />
        <Stat label="Gross profit (pre-fee)" value={usd(ev?.gross_profit)} />
        <Stat label="Total fees" value={usd(ev?.total_fees)} />
        <Stat label="Max profitable size" value={qty(r.max_profitable_qty)} hint={`depth-optimal ${qty(r.optimal_qty)}`} />
      </div>
      <div className="grid md:grid-cols-2 gap-3">
        <div>
          <div className="text-[10px] uppercase tracking-wider text-mute mb-1">Per exchange</div>
          <table className="dense">
            <thead><tr><th>Exchange</th><th className="num">Spend</th><th className="num">Fees</th><th className="num">Remaining</th></tr></thead>
            <tbody>
              {Object.keys({ ...byEx, ...r.remaining_balances }).map((ex) => (
                <tr key={ex}>
                  <td>{exName(ex)}</td>
                  <td className="num">{usd(byEx[ex] ?? 0)}</td>
                  <td className="num">{usd(feesByEx[ex] ?? 0)}</td>
                  <td className={`num ${signClass(r.remaining_balances[ex])}`}>{usd(r.remaining_balances[ex])}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wider text-mute mb-1">Outcome scenarios (at recommended size)</div>
          <table className="dense">
            <thead><tr><th>Scenario</th><th className="num">Payout</th><th className="num">Net profit</th></tr></thead>
            <tbody>
              {ev ? Object.entries(ev.scenario_payouts).map(([k, v]) => (
                <tr key={k}><td className="text-gray-300">{k}</td><td className="num">{usd(v)}</td><td className={`num ${signClass(ev.scenario_profits[k])}`}>{usd(ev.scenario_profits[k])}</td></tr>
              )) : <tr><td colSpan={3} className="text-mute">—</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
      {ev && (
        <div className="overflow-x-auto">
          <div className="text-[10px] uppercase tracking-wider text-mute mb-1">Legs & fills</div>
          <table className="dense">
            <thead><tr><th>Leg</th><th>Exchange</th><th>Side</th><th className="num">Contracts</th><th className="num">VWAP</th><th className="num">Worst fill</th><th className="num">Levels</th><th className="num">Premium</th><th className="num">Fees</th><th>Fills (price × qty)</th></tr></thead>
            <tbody>
              {ev.legs.map((l) => (
                <tr key={l.leg_id + l.market_key}>
                  <td className="text-gray-300 max-w-[200px] truncate" title={l.label}>{l.label.replace(l.exchange, exName(l.exchange))}</td>
                  <td>{exName(l.exchange)}</td>
                  <td>{l.side.toUpperCase()}</td>
                  <td className="num">{qty(l.qty)}</td>
                  <td className="num">{price(l.vwap, 4)}</td>
                  <td className="num">{price(l.worst_price)}</td>
                  <td className="num">{l.levels_consumed}</td>
                  <td className="num">{usd(l.cost)}</td>
                  <td className="num">{usd(l.fees)}{!l.fee_verified && <span className="text-warn" title="Fee model not verified — estimated"> *</span>}</td>
                  <td className="font-mono text-[11px] text-mute">{l.fills.slice(0, 6).map(([p, q]) => `${price(p)}×${qty(q)}`).join("  ")}{l.fills.length > 6 ? " …" : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {ev.legs.some((l) => !l.fee_verified) && <div className="text-[11px] text-warn mt-1">* Fees on this leg are estimated (fee model not verified). Profit is an estimate.</div>}
        </div>
      )}
      <p className="text-[11px] text-mute-dim">{r.disclaimer || disclaimer}</p>
    </div>
  );
}

function Stat({ label, value, hint, tone = "text-gray-100" }: { label: string; value: string; hint?: string; tone?: string }) {
  return (
    <div className="border border-ink-700 bg-ink-950 px-2 py-1.5 rounded-sm min-w-0">
      <div className="text-[10px] uppercase tracking-wider text-mute truncate">{label}</div>
      <div className={`font-mono tabular-nums text-sm ${tone}`}>{value}</div>
      {hint && <div className="text-[10px] text-mute-dim truncate">{hint}</div>}
    </div>
  );
}

/** Interactive calculator bound to a live opportunity. */
export function OpportunityCalculator({ oppId, exchanges }: { oppId: string; exchanges: string[] }) {
  const [inputs, setInputs] = useState<CalcInputs>(emptyInputs);
  const [result, setResult] = useState<CalcResult | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const uniq = useMemo(() => [...new Set(exchanges)], [exchanges]);
  useCapitalPrefill(uniq, setInputs);
  const debounced = useDebounced(inputs, 250);
  useEffect(() => {
    let cancel = false;
    setBusy(true);
    api.post<CalcResult>(`/opportunities/${oppId}/calculate`, inputsToBody(debounced))
      .then((r) => { if (!cancel) { setResult(r); setError(null); } })
      .catch((e) => { if (!cancel) setError(e); })
      .finally(() => { if (!cancel) setBusy(false); });
    return () => { cancel = true; };
  }, [oppId, debounced]);
  return (
    <Panel title="Interactive arbitrage calculator" right={busy ? <span className="text-mute">calculating…</span> : <span className="text-mute">uses current order-book depth</span>}>
      <div className="space-y-3">
        <CalcInputsForm value={inputs} onChange={setInputs} exchanges={uniq} />
        <p className="text-[11px] text-mute">Balances are treated independently per exchange — capital on one exchange cannot fund a leg on another.</p>
        <ErrorBox error={error} />
        {result && <CalcResultView r={result} />}
      </div>
    </Panel>
  );
}

export function SizingSummary({ s }: { s: CalcResult | import("../api").Sizing }) {
  const ev = s.optimal_eval;
  return (
    <KV rows={[
      ["Combined entry price (best asks)", price(s.combined_top_price, 4)],
      ["Guaranteed payout per complete set", usd(s.guaranteed_payout_per_set)],
      ["Gross arbitrage % (top of book)", pct(s.gross_pct_top, 3)],
      ["Fee-adjusted cost per set (top)", price(s.fee_adjusted_cost_per_set_top, 4)],
      ["Net profit per set (top)", price(s.net_profit_per_set_top, 4)],
      ["Top-of-book quantity", qty(s.top_of_book_qty)],
      ["Executable liquidity (min depth)", qty(s.liquidity_qty)],
      ["Depth-optimal quantity", qty(s.optimal_qty)],
      ["Max profitable quantity", qty(s.max_profitable_qty)],
      ["Capital required (optimal)", usd(ev?.total_outlay)],
      ["Estimated fees (optimal)", usd(ev?.total_fees)],
      ["Gross theoretical profit", usd(ev?.gross_profit)],
      ["Net theoretical profit (worst case)", <span className={signClass(ev?.worst_case_profit)}>{usd(ev?.worst_case_profit)}</span>],
      ["Net ROI", <span className={signClass(ev?.net_roi_pct)}>{pct(ev?.net_roi_pct, 3)}</span>],
      ["Limiting factor", s.limiting_factor],
    ]} />
  );
}
