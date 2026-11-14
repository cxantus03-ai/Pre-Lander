import { useEffect, useState } from "react";
import { api, type Capital } from "../api";
import { btnPrimary, ErrorBox, Field, NumInput, Panel } from "../components/ui";
import { exName, usd } from "../format";

const FIELDS: [keyof Capital, string, string?][] = [
  ["total_capital", "Total trading capital $"],
  ["reserved_capital", "Reserved capital $", "never allocated"],
  ["max_allocation_per_trade", "Max allocation per trade $", "blank = none"],
  ["max_pct_per_trade", "Max % of capital per trade", "blank = none"],
  ["max_total_open_allocation", "Max total open allocation $", "across recorded open positions"],
  ["min_profit", "Minimum desired profit $"],
  ["min_roi_pct", "Minimum acceptable net ROI %"],
  ["max_slippage_pct", "Max slippage % vs best ask", "blank = none"],
  ["max_days_to_settlement", "Max days until settlement", "blank = none"],
];

export default function CapitalPage() {
  const [cap, setCap] = useState<Capital | null>(null);
  const [form, setForm] = useState<Record<string, string>>({});
  const [balances, setBalances] = useState<Record<string, string>>({});
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);

  const load = (c: Capital) => {
    setCap(c);
    const f: Record<string, string> = {};
    for (const [k] of FIELDS) { const v = c[k]; f[k] = v === null || v === undefined ? "" : String(Number(v)); }
    setForm(f);
    const b: Record<string, string> = {};
    for (const ex of c.exchanges) b[ex] = c.exchange_balances[ex] !== undefined ? String(Number(c.exchange_balances[ex])) : "0";
    setBalances(b);
  };
  useEffect(() => { api.get<Capital>("/capital").then(load).catch(setError); }, []);

  const save = async () => {
    setError(null); setSaved(false);
    const num = (s: string | undefined) => (s === undefined || s.trim() === "" ? null : Number(s));
    const body: Record<string, unknown> = {
      total_capital: num(form.total_capital) ?? 0,
      reserved_capital: num(form.reserved_capital) ?? 0,
      max_allocation_per_trade: num(form.max_allocation_per_trade),
      max_pct_per_trade: num(form.max_pct_per_trade),
      max_total_open_allocation: num(form.max_total_open_allocation),
      min_profit: num(form.min_profit) ?? 0,
      min_roi_pct: num(form.min_roi_pct) ?? 0,
      max_slippage_pct: num(form.max_slippage_pct),
      max_days_to_settlement: num(form.max_days_to_settlement),
      exchange_balances: Object.fromEntries(Object.entries(balances).filter(([, v]) => v.trim() !== "").map(([k, v]) => [k, Number(v)])),
    };
    try { load(await api.put<Capital>("/capital", body)); setSaved(true); } catch (e) { setError(e); }
  };

  const totalBal = Object.values(balances).reduce((a, v) => a + (Number(v) || 0), 0);
  return (
    <div className="space-y-3 max-w-5xl">
      <h1 className="text-base font-semibold text-gray-100">Capital Management</h1>
      <div className="border border-ink-700 bg-ink-900 text-xs text-mute px-3 py-2 rounded-sm leading-relaxed">
        Balances are maintained <span className="text-gray-200">manually</span>; no exchange account is connected and ArbiScan never moves funds or places orders.
        Capital is <span className="text-gray-200">not interchangeable between exchanges</span>: every opportunity is sized so each leg fits within the balance on its own exchange.
        An exchange with no balance entered is treated as $0.
      </div>
      <Panel title="Exchange balances" right={<span className="text-mute">sum {usd(totalBal)}</span>}>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
          {Object.keys(balances).map((ex) => (
            <Field key={ex} label={`${exName(ex)} available $`}><NumInput value={balances[ex]} min={0} onChange={(v) => setBalances({ ...balances, [ex]: v })} /></Field>
          ))}
        </div>
      </Panel>
      <Panel title="Limits & thresholds">
        <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
          {FIELDS.map(([k, label, hint]) => (
            <Field key={k} label={label} hint={hint}><NumInput value={form[k] ?? ""} min={0} onChange={(v) => setForm({ ...form, [k]: v })} /></Field>
          ))}
        </div>
      </Panel>
      <div className="flex items-center gap-3">
        <button className={btnPrimary} onClick={save}>Save capital settings</button>
        {saved && <span className="text-xs text-pos">Saved — opportunities re-sized.</span>}
      </div>
      <ErrorBox error={error} />
      {cap && (
        <Panel title="Current state">
          <div className="text-xs space-y-1">
            <div>Capital in recorded open positions: <span className="font-mono text-gray-100">{usd(cap.open_allocation)}</span></div>
            <div>Available for new positions: <span className="font-mono text-gray-100">{usd(Number(cap.total_capital) - Number(cap.reserved_capital) - Number(cap.open_allocation))}</span></div>
            {cap.warnings.map((w, i) => <div key={i} className="text-warn">⚠ {w}</div>)}
          </div>
        </Panel>
      )}
    </div>
  );
}
