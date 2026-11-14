import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, type Check as CheckT, type Match, type MarketDetail, type MarketRow } from "../api";
import { PriceHistoryChart } from "../components/charts";
import { OrderBookView } from "../components/OrderBook";
import { Badge, btnCls, btnPrimary, Check, Empty, ErrorBox, Field, inputCls, KV, Panel, Tabs, useDebounced } from "../components/ui";
import { ago, CATEGORIES, catLabel, date, dt, exName, MATCH_STATUS_LABEL, price, qty } from "../format";
import { useLive } from "../live";

type Tab = "markets" | "matches";

export default function MarketExplorer() {
  const [sp, setSp] = useSearchParams();
  const tab = (sp.get("tab") as Tab) || "markets";
  return (
    <div className="space-y-3">
      <h1 className="text-base font-semibold text-gray-100">Market Explorer</h1>
      <Tabs<Tab> tabs={[{ id: "markets", label: "Markets" }, { id: "matches", label: "Match review" }]} value={tab} onChange={(t) => setSp({ tab: t })} />
      {tab === "markets" ? <Markets /> : <Matches />}
    </div>
  );
}

function Markets() {
  const { summary } = useLive();
  const [q, setQ] = useState("");
  const [exchange, setExchange] = useState<string>("");
  const [category, setCategory] = useState<string>("");
  const [status, setStatus] = useState<string>("");
  const [matched, setMatched] = useState<string>("");
  const [hasBook, setHasBook] = useState<string>("");
  const [sort, setSort] = useState("volume");
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<{ items: MarketRow[]; total: number } | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const dq = useDebounced(q, 300);
  const limit = 50;
  useEffect(() => { setOffset(0); }, [dq, exchange, category, status, matched, hasBook, sort]);
  useEffect(() => {
    api.get<{ items: MarketRow[]; total: number }>("/markets", {
      q: dq, exchange: exchange ? [exchange] : [], category: category ? [category] : [], status: status ? [status] : [],
      matched: matched === "" ? undefined : matched === "yes", has_book: hasBook === "" ? undefined : hasBook === "yes", sort, offset, limit,
    }).then((r) => { setData(r); setError(null); }).catch(setError);
  }, [dq, exchange, category, status, matched, hasBook, sort, offset]);

  const sel = (v: string, set: (s: string) => void, opts: [string, string][], label: string) => (
    <select className={`${inputCls} w-auto`} value={v} onChange={(e) => set(e.target.value)} aria-label={label}>
      {opts.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
    </select>
  );
  return (
    <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_520px]">
      <div className="space-y-2 min-w-0">
        <div className="flex flex-wrap gap-2">
          <input className={`${inputCls} flex-1 min-w-[180px]`} placeholder="Search title, event or exchange id…" value={q} onChange={(e) => setQ(e.target.value)} />
          {sel(exchange, setExchange, [["", "All exchanges"], ...(summary?.exchanges ?? []).map((e) => [e, exName(e)] as [string, string])], "Exchange")}
          {sel(category, setCategory, [["", "All categories"], ...CATEGORIES.map((c) => [c, catLabel(c)] as [string, string])], "Category")}
          {sel(status, setStatus, [["", "Any status"], ["open", "Open"], ["suspended", "Suspended"], ["closed", "Closed"], ["unknown", "Unknown"]], "Status")}
          {sel(matched, setMatched, [["", "Matched: any"], ["yes", "Matched"], ["no", "Unmatched"]], "Matched")}
          {sel(hasBook, setHasBook, [["", "Book: any"], ["yes", "Has book"], ["no", "No book"]], "Book")}
          {sel(sort, setSort, [["volume", "Sort: volume"], ["close", "Sort: closing"], ["title", "Sort: title"]], "Sort")}
        </div>
        <ErrorBox error={error} />
        <Panel bodyClass="p-0" title={`${data?.total.toLocaleString() ?? "…"} markets`} right={
          <div className="flex items-center gap-2">
            <button className={btnCls} disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>‹ Prev</button>
            <span className="text-mute font-mono">{data ? `${offset + 1}-${Math.min(offset + limit, data.total)}` : ""}</span>
            <button className={btnCls} disabled={!data || offset + limit >= data.total} onClick={() => setOffset(offset + limit)}>Next ›</button>
          </div>}>
          <div className="overflow-auto max-h-[70vh]">
            <table className="dense">
              <thead><tr><th>Market</th><th>Exchange</th><th>Category</th><th>Status</th><th className="num">YES bid/ask</th><th className="num">NO bid/ask</th><th className="num">Volume</th><th className="num">Closes</th><th>Fees</th><th>Book</th><th>Matches</th></tr></thead>
              <tbody>
                {data?.items.map((m) => (
                  <tr key={m.key} className={`cursor-pointer ${selected === m.key ? "bg-ink-800" : ""}`} onClick={() => setSelected(m.key)}>
                    <td className="max-w-[340px]"><div className="truncate text-gray-100" title={m.title}>{m.title}</div><div className="text-[10px] text-mute truncate">{m.outcome_label || m.event_title || m.external_id}</div></td>
                    <td>{exName(m.exchange)}{m.is_simulated && <> <Badge kind="sim">sim</Badge></>}</td>
                    <td className="text-mute">{catLabel(m.category)}</td>
                    <td><Badge kind={m.status === "open" ? "pass" : m.status === "suspended" ? "warn" : "unknown"}>{m.status}</Badge></td>
                    <td className="num">{price(m.top?.yes_bid)} / {price(m.top?.yes_ask)}</td>
                    <td className="num">{price(m.top?.no_bid)} / {price(m.top?.no_ask)}</td>
                    <td className="num">{qty(m.volume)}</td>
                    <td className="num">{date(m.close_time)}</td>
                    <td>{m.fee.verified ? <span className="text-mute">{m.fee.kind}</span> : <span className="text-warn">{m.fee.kind} (est.)</span>}</td>
                    <td className="text-mute">{m.has_book ? `${m.book_age_s?.toFixed(0)}s` : "—"}</td>
                    <td className="num">{m.match_ids?.length || ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {data && !data.items.length && <Empty>No markets match these filters.</Empty>}
          </div>
        </Panel>
      </div>
      <div className="min-w-0">{selected ? <MarketPanel marketKey={selected} onClose={() => setSelected(null)} /> : <Panel title="Market detail"><Empty>Select a market to see its rules, order book and price history.</Empty></Panel>}</div>
    </div>
  );
}

export function MarketPanel({ marketKey, onClose }: { marketKey: string; onClose?: () => void }) {
  const [m, setM] = useState<MarketDetail | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const load = () => api.get<MarketDetail>("/markets/detail", { key: marketKey }).then((r) => { setM(r); setError(null); }).catch(setError);
  useEffect(() => { setM(null); load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [marketKey]);
  const refresh = async () => {
    setBusy(true);
    try { await api.post("/markets/refresh-book", {}, { key: marketKey }); await load(); } catch (e) { setError(e); } finally { setBusy(false); }
  };
  return (
    <Panel title={m ? `${exName(m.exchange)} · ${m.external_id}` : "Market"} right={<>
      <button className={btnPrimary} disabled={busy} onClick={refresh}>{busy ? "Fetching…" : "Refresh book"}</button>
      {onClose && <button className={btnCls} onClick={onClose}>Close</button>}
    </>}>
      <ErrorBox error={error} />
      {!m ? <div className="text-xs text-mute">Loading…</div> : (
        <div className="space-y-3">
          <div>
            <div className="text-sm text-gray-100">{m.title}</div>
            {m.event_title && m.event_title !== m.title && <div className="text-xs text-mute">Event: {m.event_title}</div>}
            <div className="flex flex-wrap gap-1.5 mt-1">
              <Badge kind={m.status === "open" ? "pass" : "warn"}>{m.status}</Badge>
              {m.is_simulated && <Badge kind="sim">simulated</Badge>}
              <Badge kind={m.fee.verified ? "info" : "warn"}>{m.fee.kind}{m.fee.verified ? "" : " · estimated"}</Badge>
              {m.url && <a className="text-accent text-xs" href={m.url} target="_blank" rel="noreferrer noopener">Official market page ↗</a>}
            </div>
          </div>
          <KV rows={[
            ["YES / NO label", `${m.yes_label} / ${m.no_label}`], ["Close", dt(m.close_time)], ["Expiration", dt(m.expiration_time)],
            ["Payout per contract", `$${m.payout}`], ["Tick / min order / increment", `${m.tick_size} / ${m.min_order_size} / ${m.qty_increment}`],
            ["Volume (24h)", `${qty(m.volume)} (${qty(m.volume_24h)})`], ["Open interest", qty(m.open_interest)],
            ["Fee source", <span className="text-[11px]">{m.fee.source}</span>],
            ["Resolution sources", m.resolution_sources.join("; ") || "—"], ["Can close early", m.can_close_early === null ? "—" : String(m.can_close_early)],
            ["Exchange updated", ago(m.exchange_updated_at)],
          ]} />
          <OrderBookView book={m.book} stale={m.book_stale} />
          <div>
            <div className="text-[10px] uppercase tracking-wider text-mute mb-1">Price history (stored)</div>
            <PriceHistoryChart prices={m.prices} />
          </div>
          <RulesText m={m} />
        </div>
      )}
    </Panel>
  );
}

export function RulesText({ m }: { m: MarketDetail | null | undefined }) {
  if (!m) return <div className="text-xs text-mute">Market not loaded.</div>;
  return (
    <div className="space-y-2 text-xs">
      {m.rules_primary && <div><div className="text-[10px] uppercase tracking-wider text-mute">Rules</div><p className="whitespace-pre-wrap text-gray-300 leading-relaxed">{m.rules_primary}</p></div>}
      {m.rules_secondary && <div><div className="text-[10px] uppercase tracking-wider text-mute">Secondary rules</div><p className="whitespace-pre-wrap text-gray-300 leading-relaxed">{m.rules_secondary}</p></div>}
      {m.description && m.description !== m.rules_primary && <div><div className="text-[10px] uppercase tracking-wider text-mute">Description</div><p className="whitespace-pre-wrap text-gray-400 leading-relaxed">{m.description}</p></div>}
      {m.early_close_condition && <div><div className="text-[10px] uppercase tracking-wider text-mute">Early close</div><p className="text-gray-300">{m.early_close_condition}</p></div>}
    </div>
  );
}

export function ChecksTable({ checks }: { checks: CheckT[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="dense">
        <thead><tr><th>Check</th><th>Result</th><th>Detail</th><th>Market A</th><th>Market B</th></tr></thead>
        <tbody>
          {checks.map((c, i) => (
            <tr key={i}>
              <td className="text-gray-300">{c.label}</td>
              <td><Badge kind={c.result} /></td>
              <td className={`whitespace-normal min-w-[220px] ${c.result === "fail" ? "text-neg" : c.result === "warn" ? "text-warn" : "text-mute"}`}>{c.detail}</td>
              <td className="font-mono text-[11px] whitespace-normal min-w-[120px] text-gray-300">{c.a ?? "—"}</td>
              <td className="font-mono text-[11px] whitespace-normal min-w-[120px] text-gray-300">{c.b ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Matches() {
  const [status, setStatus] = useState<string[]>([]);
  const [q, setQ] = useState("");
  const dq = useDebounced(q, 300);
  const [list, setList] = useState<Match[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    api.get<{ items: Match[] }>("/matches", { status, q: dq, limit: 1000 }).then((r) => { setList(r.items); setError(null); }).catch(setError);
  }, [status, dq, tick]);
  return (
    <div className="grid gap-3 xl:grid-cols-[minmax(0,460px)_minmax(0,1fr)]">
      <div className="space-y-2 min-w-0">
        <input className={inputCls} placeholder="Search matched markets…" value={q} onChange={(e) => setQ(e.target.value)} />
        <div className="flex flex-wrap gap-3">
          {Object.entries(MATCH_STATUS_LABEL).map(([k, l]) => (
            <Check key={k} label={l} checked={status.includes(k)} onChange={() => setStatus((s) => (s.includes(k) ? s.filter((x) => x !== k) : [...s, k]))} />
          ))}
        </div>
        <ErrorBox error={error} />
        <Panel title={`${list.length} candidate matches`} bodyClass="p-0">
          <div className="max-h-[65vh] overflow-auto divide-y divide-ink-800">
            {list.map((m) => (
              <button key={m.id} onClick={() => setSelected(m.id)} className={`w-full text-left px-3 py-2 hover:bg-ink-850 ${selected === m.id ? "bg-ink-800" : ""}`}>
                <div className="flex items-center gap-2"><Badge kind={m.status}>{MATCH_STATUS_LABEL[m.status]}</Badge>{m.method === "manual" && <Badge kind="info">manual</Badge>}{m.orientation === "inverted" && <Badge kind="warn">inverted</Badge>}<span className="ml-auto font-mono text-[10px] text-mute">sim {m.semantic_score.toFixed(2)}</span></div>
                <div className="text-xs text-gray-200 truncate mt-1">{exName(m.a?.exchange)}: {m.a?.title}</div>
                <div className="text-xs text-gray-400 truncate">{exName(m.b?.exchange)}: {m.b?.title}</div>
              </button>
            ))}
            {!list.length && <Empty>No matches yet. Matching runs after each market discovery.</Empty>}
          </div>
        </Panel>
        <ManualMatchForm onCreated={() => setTick((t) => t + 1)} />
      </div>
      <div className="min-w-0">{selected ? <MatchDetail id={selected} onChanged={() => setTick((t) => t + 1)} /> : <Panel title="Settlement comparison"><Empty>Select a match to compare both contracts' rules side by side.</Empty></Panel>}</div>
    </div>
  );
}

function MatchDetail({ id, onChanged }: { id: string; onChanged: () => void }) {
  const [m, setM] = useState<Match | null>(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState<unknown>(null);
  const load = () => api.get<Match>(`/matches/${id}`).then(setM).catch(setError);
  useEffect(() => { setM(null); load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [id]);
  const act = async (action: string, orientation?: string) => {
    try {
      await api.post(`/matches/${id}/review`, { action, orientation, note: note || undefined });
      setNote("");
      await load();
      onChanged();
    } catch (e) { setError(e); }
  };
  if (!m) return <Panel title="Settlement comparison"><ErrorBox error={error} /><div className="text-xs text-mute">Loading…</div></Panel>;
  const a = m.a as MarketDetail | null, b = m.b as MarketDetail | null;
  return (
    <Panel title="Settlement comparison" right={<Badge kind={m.status}>{MATCH_STATUS_LABEL[m.status]}</Badge>}>
      <div className="space-y-3">
        <ErrorBox error={error} />
        <div className="text-xs text-mute">
          Orientation: <span className="text-gray-200">{m.orientation === "same" ? "YES ↔ YES (complement = YES on one + NO on the other)" : "YES on A ≡ NO on B (inverted)"}</span>
          {" · "}method {m.method} · similarity {m.semantic_score.toFixed(2)}
        </div>
        {m.implication && <div className="text-xs text-warn">Thresholds differ: {m.implication.implying} implies {m.implication.implied} — tracked only as a potential cross-market mispricing.</div>}
        <ul className="text-xs list-disc pl-4 space-y-0.5 text-gray-300">{m.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
        <ChecksTable checks={m.checks} />
        <div className="grid md:grid-cols-2 gap-3">
          {[a, b].map((x, i) => (
            <div key={i} className="border border-ink-700 bg-ink-950 p-2 rounded-sm min-w-0">
              <div className="text-[10px] uppercase tracking-wider text-mute">Market {i === 0 ? "A" : "B"} · {exName(x?.exchange)}</div>
              <div className="text-sm text-gray-100 mb-1">{x?.title}</div>
              <div className="text-[11px] text-mute mb-2">{x?.external_id} · closes {dt(x?.close_time)} {x?.url && <a className="text-accent" href={x.url} target="_blank" rel="noreferrer noopener">↗</a>}</div>
              <RulesText m={x} />
            </div>
          ))}
        </div>
        <div className="space-y-2 border-t border-ink-700 pt-3">
          <Field label="Review note (optional)"><input className={inputCls} value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. read both rulebooks; same source & cutoff" /></Field>
          <div className="flex flex-wrap gap-2">
            <button className="px-2.5 py-1 text-xs border border-pos/50 bg-pos-dim text-pos rounded-sm" onClick={() => act("approve")}>Approve as verified</button>
            <button className="px-2.5 py-1 text-xs border border-neg/50 bg-neg-dim text-neg rounded-sm" onClick={() => act("reject")}>Reject</button>
            <button className={btnCls} onClick={() => act("correct", m.orientation === "same" ? "inverted" : "same")}>Flip orientation → {m.orientation === "same" ? "inverted" : "same"}</button>
            <button className={btnCls} onClick={() => act("reset")}>Reset to automatic</button>
          </div>
          <p className="text-[11px] text-mute-dim">Approve only after reading both official rulebooks. An automated similarity score is never sufficient proof of settlement equivalence.</p>
        </div>
        {m.reviews && m.reviews.length > 0 && (
          <div>
            <div className="text-[10px] uppercase tracking-wider text-mute mb-1">Review history</div>
            <ul className="text-xs space-y-0.5">{m.reviews.map((r, i) => <li key={i} className="text-mute">{dt(r.created_at)} · {r.action}: {r.previous_status} → {r.new_status}{r.note ? ` — ${r.note}` : ""}</li>)}</ul>
          </div>
        )}
      </div>
    </Panel>
  );
}

function ManualMatchForm({ onCreated }: { onCreated: () => void }) {
  const [a, setA] = useState("");
  const [b, setB] = useState("");
  const [orientation, setOrientation] = useState("same");
  const [approve, setApprove] = useState(false);
  const [note, setNote] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [ok, setOk] = useState<string | null>(null);
  const submit = async () => {
    setError(null); setOk(null);
    try {
      const r = await api.post<Match>("/matches", { market_a: a.trim(), market_b: b.trim(), orientation, approve, note: note || undefined });
      setOk(`Created match ${r.id} (${MATCH_STATUS_LABEL[r.status]})`);
      onCreated();
    } catch (e) { setError(e); }
  };
  return (
    <Panel title="Create manual match">
      <div className="space-y-2">
        <Field label="Market A key" hint="format exchange:external_id, e.g. polymarket:559651"><input className={inputCls} value={a} onChange={(e) => setA(e.target.value)} /></Field>
        <Field label="Market B key"><input className={inputCls} value={b} onChange={(e) => setB(e.target.value)} /></Field>
        <div className="flex flex-wrap items-center gap-3">
          <select className={`${inputCls} w-auto`} value={orientation} onChange={(e) => setOrientation(e.target.value)}><option value="same">Same orientation</option><option value="inverted">Inverted</option></select>
          <Check label="Approve as verified" checked={approve} onChange={setApprove} />
        </div>
        <Field label="Note"><input className={inputCls} value={note} onChange={(e) => setNote(e.target.value)} /></Field>
        <button className={btnPrimary} onClick={submit} disabled={!a || !b}>Create match</button>
        <ErrorBox error={error} />
        {ok && <div className="text-xs text-pos">{ok}</div>}
      </div>
    </Panel>
  );
}
