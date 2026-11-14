import { useEffect, useMemo, useState } from "react";
import { api, type OppSummary } from "../api";
import { OppTable } from "../components/OppTable";
import { btnCls, Check, ErrorBox, Field, inputCls, NumInput, Panel, useDebounced } from "../components/ui";
import { CATEGORIES, catLabel, exName, MATCH_STATUS_LABEL } from "../format";
import { useLive } from "../live";

const SORTS = [
  ["net_roi", "Highest net ROI"], ["profit", "Highest $ profit"], ["liquidity", "Greatest liquidity"], ["capital", "Lowest capital"],
  ["recent", "Most recently detected"], ["closing", "Soonest close"], ["exchange", "Exchange"], ["category", "Category"],
] as const;

interface Filters {
  q: string; sort: string; order: "asc" | "desc"; exchange: string[]; scope: "" | "cross" | "same"; market_type: "" | "binary" | "multi";
  category: string[]; classification: string[]; state: string[]; match_status: string[];
  min_roi: string; min_profit: string; min_liquidity: string; close_after: string; close_before: string; include_invalid: boolean;
}

const initial: Filters = {
  q: "", sort: "net_roi", order: "desc", exchange: [], scope: "", market_type: "", category: [], classification: [],
  state: [], match_status: [], min_roi: "", min_profit: "", min_liquidity: "", close_after: "", close_before: "", include_invalid: false,
};

function toggle(list: string[], v: string): string[] {
  return list.includes(v) ? list.filter((x) => x !== v) : [...list, v];
}

export default function LiveArbitrage() {
  const { version, summary } = useLive();
  const [f, setF] = useState<Filters>(() => {
    try { return { ...initial, ...JSON.parse(localStorage.getItem("arbiscan.filters") || "{}") }; } catch { return initial; }
  });
  const [extended, setExtended] = useState(false);
  const [items, setItems] = useState<OppSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<unknown>(null);
  const [showFilters, setShowFilters] = useState(true);
  const df = useDebounced(f, 250);
  const dv = useDebounced(version, 400);

  useEffect(() => { try { localStorage.setItem("arbiscan.filters", JSON.stringify(f)); } catch { /* ignore */ } }, [f]);

  useEffect(() => {
    let cancel = false;
    const params = {
      q: df.q, sort: df.sort, order: df.order, exchange: df.exchange, scope: df.scope || undefined, market_type: df.market_type || undefined,
      category: df.category, classification: df.classification, state: df.state, match_status: df.match_status,
      min_roi: df.min_roi, min_profit: df.min_profit, min_liquidity: df.min_liquidity,
      close_after: df.close_after ? new Date(df.close_after).toISOString() : undefined,
      close_before: df.close_before ? new Date(df.close_before + "T23:59:59").toISOString() : undefined,
      include_invalid: df.include_invalid || undefined, limit: 1000,
    };
    api.get<{ items: OppSummary[]; total: number }>("/opportunities", params)
      .then((r) => { if (!cancel) { setItems(r.items); setTotal(r.total); setError(null); } })
      .catch((e) => !cancel && setError(e));
    return () => { cancel = true; };
  }, [df, dv]);

  const exchanges = summary?.exchanges ?? [];
  const counts = useMemo(() => ({
    verified: items.filter((o) => o.classification === "verified" && o.state === "active").length,
    potential: items.filter((o) => o.classification === "potential" && o.state === "active").length,
  }), [items]);
  const set = <K extends keyof Filters>(k: K) => (v: Filters[K]) => setF((p) => ({ ...p, [k]: v }));

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 justify-between">
        <h1 className="text-base font-semibold text-gray-100">Live Arbitrage</h1>
        <div className="flex items-center gap-3 text-xs">
          <span className="text-pos">{counts.verified} verified</span>
          <span className="text-warn">{counts.potential} potential</span>
          <span className="text-mute">{total} shown</span>
        </div>
      </div>
      <div className="flex flex-wrap items-end gap-2">
        <div className="flex-1 min-w-[200px]"><input className={inputCls} placeholder="Search events or contracts…" value={f.q} onChange={(e) => set("q")(e.target.value)} /></div>
        <select className={`${inputCls} w-auto`} value={f.sort} onChange={(e) => set("sort")(e.target.value)} aria-label="Sort">
          {SORTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <button className={btnCls} onClick={() => set("order")(f.order === "desc" ? "asc" : "desc")}>{f.order === "desc" ? "↓ desc" : "↑ asc"}</button>
        <button className={btnCls} onClick={() => setExtended((x) => !x)}>{extended ? "Fewer columns" : "More columns"}</button>
        <button className={btnCls} onClick={() => setShowFilters((x) => !x)}>{showFilters ? "Hide filters" : "Filters"}</button>
        <button className={btnCls} onClick={() => setF(initial)}>Reset</button>
      </div>
      {showFilters && (
        <Panel>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4 text-xs">
            <div className="space-y-1.5">
              <div className="text-[10px] uppercase tracking-wider text-mute">Exchange</div>
              <div className="flex flex-wrap gap-3">{exchanges.map((e) => <Check key={e} label={exName(e)} checked={f.exchange.includes(e)} onChange={() => set("exchange")(toggle(f.exchange, e))} />)}</div>
              <div className="text-[10px] uppercase tracking-wider text-mute pt-1">Scope / market type</div>
              <div className="flex flex-wrap gap-3">
                <Check label="Cross-exchange" checked={f.scope === "cross"} onChange={(v) => set("scope")(v ? "cross" : "")} />
                <Check label="Same-exchange" checked={f.scope === "same"} onChange={(v) => set("scope")(v ? "same" : "")} />
                <Check label="Binary" checked={f.market_type === "binary"} onChange={(v) => set("market_type")(v ? "binary" : "")} />
                <Check label="Multi-outcome" checked={f.market_type === "multi"} onChange={(v) => set("market_type")(v ? "multi" : "")} />
              </div>
            </div>
            <div className="space-y-1.5">
              <div className="text-[10px] uppercase tracking-wider text-mute">Category</div>
              <div className="flex flex-wrap gap-x-3 gap-y-1">{CATEGORIES.map((c) => <Check key={c} label={catLabel(c)} checked={f.category.includes(c)} onChange={() => set("category")(toggle(f.category, c))} />)}</div>
            </div>
            <div className="space-y-1.5">
              <div className="text-[10px] uppercase tracking-wider text-mute">Verification / state</div>
              <div className="flex flex-wrap gap-x-3 gap-y-1">
                <Check label={<span className="text-pos">Verified</span>} checked={f.classification.includes("verified")} onChange={() => set("classification")(toggle(f.classification, "verified"))} />
                <Check label={<span className="text-warn">Potential</span>} checked={f.classification.includes("potential")} onChange={() => set("classification")(toggle(f.classification, "potential"))} />
                <Check label="Active" checked={f.state.includes("active")} onChange={() => set("state")(toggle(f.state, "active"))} />
                <Check label="Stale" checked={f.state.includes("stale")} onChange={() => set("state")(toggle(f.state, "stale"))} />
                <Check label="Unavailable" checked={f.state.includes("unavailable")} onChange={() => set("state")(toggle(f.state, "unavailable"))} />
                <Check label={<span className="text-neg">Include invalid</span>} checked={f.include_invalid} onChange={set("include_invalid")} />
              </div>
              <div className="text-[10px] uppercase tracking-wider text-mute pt-1">Settlement match</div>
              <div className="flex flex-wrap gap-x-3 gap-y-1">
                {Object.entries(MATCH_STATUS_LABEL).filter(([k]) => k !== "incompatible").map(([k, l]) => <Check key={k} label={l} checked={f.match_status.includes(k)} onChange={() => set("match_status")(toggle(f.match_status, k))} />)}
                <Check label="Structural (no match)" checked={f.match_status.includes("n/a")} onChange={() => set("match_status")(toggle(f.match_status, "n/a"))} />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <Field label="Min net ROI %"><NumInput value={f.min_roi} onChange={set("min_roi")} /></Field>
              <Field label="Min profit $"><NumInput value={f.min_profit} onChange={set("min_profit")} min={0} /></Field>
              <Field label="Min liquidity (contracts)"><NumInput value={f.min_liquidity} onChange={set("min_liquidity")} min={0} /></Field>
              <div />
              <Field label="Closes after"><input type="date" className={inputCls} value={f.close_after} onChange={(e) => set("close_after")(e.target.value)} /></Field>
              <Field label="Closes before"><input type="date" className={inputCls} value={f.close_before} onChange={(e) => set("close_before")(e.target.value)} /></Field>
            </div>
          </div>
        </Panel>
      )}
      <ErrorBox error={error} />
      <Panel title="Opportunities" right={<span className="text-mute">green = verified · amber = needs verification · red = expired/invalid · gray = stale/unavailable</span>} bodyClass="p-0">
        <OppTable opps={items} extended={extended} maxHeight="70vh" />
      </Panel>
    </div>
  );
}
