import { useEffect, useMemo, useState } from "react";
import { api, type CalcResult } from "../api";
import { CalcInputsForm, CalcResultView, emptyInputs, inputsToBody, OpportunityCalculator, type CalcInputs } from "../components/Calculator";
import { btnCls, btnPrimary, Check, ErrorBox, Field, inputCls, NumInput, Panel, Tabs, useDebounced } from "../components/ui";
import { exName, pct } from "../format";
import { useLive } from "../live";

type Mode = "live" | "manual";

export default function CalculatorPage() {
  const [mode, setMode] = useState<Mode>("live");
  return (
    <div className="space-y-3">
      <h1 className="text-base font-semibold text-gray-100">Arbitrage Calculator</h1>
      <p className="text-xs text-mute max-w-3xl">Sizes positions against real order-book depth and per-exchange fee schedules. Quantities target equal payout across outcomes; the optimiser stops adding contracts once the next order-book level would reduce the worst-case modelled profit.</p>
      <Tabs<Mode> tabs={[{ id: "live", label: "Live opportunity" }, { id: "manual", label: "Manual legs" }]} value={mode} onChange={setMode} />
      {mode === "live" ? <LiveCalc /> : <ManualCalc />}
    </div>
  );
}

function LiveCalc() {
  const { opps } = useLive();
  const sorted = useMemo(() => [...opps].sort((a, b) => Number(b.net_roi_pct ?? -1e9) - Number(a.net_roi_pct ?? -1e9)), [opps]);
  const [id, setId] = useState<string>("");
  // pick the best opportunity initially, and move on if the selected one disappears
  useEffect(() => { if (sorted.length && !sorted.some((o) => o.id === id)) setId(sorted[0].id); }, [sorted, id]);
  const opp = sorted.find((o) => o.id === id);
  return (
    <div className="space-y-3">
      <Field label="Opportunity">
        <select className={inputCls} value={id} onChange={(e) => setId(e.target.value)}>
          {!sorted.length && <option value="">No live opportunities right now</option>}
          {sorted.map((o) => <option key={o.id} value={o.id}>[{o.classification}/{o.state}] {pct(o.net_roi_pct)} · {o.title.slice(0, 90)}</option>)}
          {id && !opp && <option value={id}>(no longer live) {id}</option>}
        </select>
      </Field>
      {opp ? <OpportunityCalculator key={opp.id} oppId={opp.id} exchanges={opp.legs.map((l) => l.exchange)} /> : <Panel><div className="text-xs text-mute">Select an opportunity, or use manual mode to enter your own prices and depth.</div></Panel>}
    </div>
  );
}

interface LegForm {
  exchange: string; side: "yes" | "no"; label: string; levels: { price: string; size: string }[];
  fee_kind: string; fee_rate: string; fee_exponent: string; fee_multiplier: string; qty_increment: string; min_order_size: string;
}

const newLeg = (exchange: string, side: "yes" | "no", p: string, s: string, fee = "zero"): LegForm => ({
  exchange, side, label: "", levels: [{ price: p, size: s }], fee_kind: fee, fee_rate: fee === "polymarket_schedule" ? "0.04" : fee === "percent_notional" ? "0.02" : "0",
  fee_exponent: "1", fee_multiplier: "1", qty_increment: "1", min_order_size: "1",
});

function ManualCalc() {
  const [structure, setStructure] = useState<"binary" | "complete_set" | "all_no">("binary");
  const [exhaustive, setExhaustive] = useState(false);
  const [legs, setLegs] = useState<LegForm[]>([
    { ...newLeg("polymarket", "yes", "0.43", "100"), levels: [{ price: "0.43", size: "100" }, { price: "0.44", size: "200" }] },
    { ...newLeg("kalshi", "no", "0.54", "50", "kalshi_quadratic"), levels: [{ price: "0.54", size: "50" }, { price: "0.55", size: "150" }] },
  ]);
  const [inputs, setInputs] = useState<CalcInputs>(() => ({ ...emptyInputs(), total_capital: "600", balances: { polymarket: "500", kalshi: "100" } }));
  const [result, setResult] = useState<CalcResult | null>(null);
  const [error, setError] = useState<unknown>(null);
  const exchanges = useMemo(() => [...new Set(legs.map((l) => l.exchange.trim()).filter(Boolean))], [legs]);
  const payload = useMemo(() => ({
    structure, exhaustive,
    legs: legs.map((l) => ({
      exchange: l.exchange.trim() || "x", side: l.side, label: l.label,
      levels: l.levels.filter((x) => x.price !== "" && x.size !== "").map((x) => [Number(x.price), Number(x.size)]),
      fee_kind: l.fee_kind, fee_rate: Number(l.fee_rate || 0), fee_exponent: Number(l.fee_exponent || 1), fee_multiplier: Number(l.fee_multiplier || 1),
      fee_rounding: l.fee_kind === "polymarket_schedule" ? 0.0001 : 0.01, qty_increment: Number(l.qty_increment || 1), min_order_size: Number(l.min_order_size || 0),
    })),
    constraints: inputsToBody(inputs),
  }), [structure, exhaustive, legs, inputs]);
  const dp = useDebounced(payload, 250);
  useEffect(() => {
    let cancel = false;
    api.post<CalcResult>("/calculator/manual", dp).then((r) => { if (!cancel) { setResult(r); setError(null); } }).catch((e) => !cancel && setError(e));
    return () => { cancel = true; };
  }, [dp]);

  const upd = (i: number, patch: Partial<LegForm>) => setLegs((ls) => ls.map((l, j) => (j === i ? { ...l, ...patch } : l)));
  return (
    <div className="space-y-3">
      <Panel title="Structure">
        <div className="flex flex-wrap items-center gap-3">
          <select className={inputCls.replace("w-full", "w-auto")} value={structure} onChange={(e) => setStructure(e.target.value as typeof structure)}>
            <option value="binary">Binary complement (YES + NO, 2 legs)</option>
            <option value="complete_set">Multi-outcome: buy YES on every outcome</option>
            <option value="all_no">Multi-outcome: buy NO on every outcome</option>
          </select>
          {structure !== "binary" && <Check label="Outcome set verified exhaustive (exactly one wins)" checked={exhaustive} onChange={setExhaustive} />}
          <button className={btnCls} onClick={() => setLegs((ls) => [...ls, newLeg("kalshi", structure === "all_no" ? "no" : "yes", "0.30", "100", "kalshi_quadratic")])}>+ Add leg</button>
        </div>
        {structure === "complete_set" && !exhaustive && <p className="text-[11px] text-warn mt-2">Without a verified exhaustive set a "none of the listed outcomes" scenario pays $0, so no arbitrage is possible.</p>}
      </Panel>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {legs.map((l, i) => (
          <Panel key={i} title={`Leg ${i + 1}`} right={legs.length > 2 && <button className="text-neg" onClick={() => setLegs((ls) => ls.filter((_, j) => j !== i))}>remove</button>}>
            <div className="grid grid-cols-2 gap-2">
              <Field label="Exchange"><input className={inputCls} value={l.exchange} onChange={(e) => upd(i, { exchange: e.target.value })} /></Field>
              <Field label="Buy side"><select className={inputCls} value={l.side} onChange={(e) => upd(i, { side: e.target.value as "yes" | "no" })}><option value="yes">YES</option><option value="no">NO</option></select></Field>
              <Field label="Label"><input className={inputCls} value={l.label} onChange={(e) => upd(i, { label: e.target.value })} placeholder="optional" /></Field>
              <Field label="Fee model">
                <select className={inputCls} value={l.fee_kind} onChange={(e) => upd(i, { fee_kind: e.target.value })}>
                  <option value="zero">No fee</option><option value="kalshi_quadratic">Kalshi quadratic (0.07·C·P·(1−P))</option>
                  <option value="polymarket_schedule">Polymarket schedule</option><option value="percent_notional">% of notional</option>
                </select>
              </Field>
              {l.fee_kind !== "zero" && <Field label={l.fee_kind === "kalshi_quadratic" ? "Fee rate (0 = 0.07)" : "Fee rate"}><NumInput value={l.fee_rate} onChange={(v) => upd(i, { fee_rate: v })} min={0} /></Field>}
              {l.fee_kind === "kalshi_quadratic" && <Field label="Fee multiplier"><NumInput value={l.fee_multiplier} onChange={(v) => upd(i, { fee_multiplier: v })} min={0} /></Field>}
              {l.fee_kind === "polymarket_schedule" && <Field label="Exponent"><NumInput value={l.fee_exponent} onChange={(v) => upd(i, { fee_exponent: v })} min={0} /></Field>}
              <Field label="Qty increment"><NumInput value={l.qty_increment} onChange={(v) => upd(i, { qty_increment: v })} min={0} /></Field>
              <Field label="Min order size"><NumInput value={l.min_order_size} onChange={(v) => upd(i, { min_order_size: v })} min={0} /></Field>
            </div>
            <div className="mt-2">
              <div className="text-[10px] uppercase tracking-wider text-mute mb-1">Ask levels (price, contracts)</div>
              {l.levels.map((lv, k) => (
                <div key={k} className="flex gap-1 mb-1">
                  <NumInput value={lv.price} onChange={(v) => upd(i, { levels: l.levels.map((x, m) => (m === k ? { ...x, price: v } : x)) })} placeholder="price" />
                  <NumInput value={lv.size} onChange={(v) => upd(i, { levels: l.levels.map((x, m) => (m === k ? { ...x, size: v } : x)) })} placeholder="size" />
                  <button className="text-mute px-1" onClick={() => upd(i, { levels: l.levels.filter((_, m) => m !== k) })} disabled={l.levels.length === 1}>×</button>
                </div>
              ))}
              <button className={btnCls} onClick={() => upd(i, { levels: [...l.levels, { price: "", size: "" }] })}>+ level</button>
            </div>
          </Panel>
        ))}
      </div>
      <Panel title="Capital constraints" right={<span className="text-mute">balances per exchange: {exchanges.map(exName).join(", ")}</span>}>
        <CalcInputsForm value={inputs} onChange={setInputs} exchanges={exchanges} />
      </Panel>
      <Panel title="Result" right={<button className={btnPrimary} onClick={() => setInputs({ ...inputs })}>Recalculate</button>}>
        <ErrorBox error={error} />
        {result ? <CalcResultView r={result} /> : <div className="text-xs text-mute">Calculating…</div>}
      </Panel>
    </div>
  );
}
