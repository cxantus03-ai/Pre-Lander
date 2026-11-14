import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError, type OppDetail } from "../api";
import { OpportunityCalculator, SizingSummary } from "../components/Calculator";
import { C, LinesChart, PriceHistoryChart, ProfitCurve } from "../components/charts";
import { OppTable } from "../components/OppTable";
import { OrderBookView } from "../components/OrderBook";
import { Badge, Empty, ErrorBox, FlagList, KV, Panel } from "../components/ui";
import { ago, catLabel, dt, duration, exName, MATCH_STATUS_LABEL, price, qty, signClass, time, usd } from "../format";
import { useLive } from "../live";
import { ChecksTable, RulesText } from "./MarketExplorer";

export default function OpportunityDetails() {
  const { id } = useParams();
  const { opps, version, disclaimer } = useLive();
  const nav = useNavigate();
  const [d, setD] = useState<OppDetail | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [gone, setGone] = useState(false);

  useEffect(() => { setD(null); setGone(false); setError(null); }, [id]);
  useEffect(() => {
    if (!id) return;
    let cancel = false;
    api.get<OppDetail>(`/opportunities/${id}`)
      .then((r) => { if (!cancel) { setD(r); setGone(false); setError(null); } })
      .catch((e) => { if (cancel) return; if (e instanceof ApiError && e.status === 404) setGone(true); else setError(e); });
    return () => { cancel = true; };
  }, [id, version]);

  if (!id) {
    return (
      <div className="space-y-3">
        <h1 className="text-base font-semibold text-gray-100">Opportunity Details</h1>
        <Panel title="Select an opportunity" bodyClass="p-0"><OppTable opps={opps.slice(0, 50)} /></Panel>
      </div>
    );
  }
  if (gone && !d) {
    return (
      <Panel title="Opportunity no longer tracked">
        <p className="text-sm text-neg">This opportunity has disappeared (prices moved, books changed, or it expired) and is no longer held in memory.</p>
        <p className="text-xs text-mute mt-1">Its history remains in Historical Analytics. <Link className="text-accent" to="/live">Back to live arbitrage →</Link></p>
      </Panel>
    );
  }
  if (!d) return <div className="text-xs text-mute">{error ? <ErrorBox error={error} /> : "Loading…"}</div>;

  const o = d.opportunity;
  const s = d.sizing;
  const ev = s.optimal_eval;
  const errors = o.flags.filter((f) => f.level === "error");
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-start gap-2 justify-between">
        <div className="min-w-0">
          <button className="text-xs text-mute hover:text-gray-200" onClick={() => nav(-1)}>‹ back</button>
          <h1 className="text-base font-semibold text-gray-100 break-words">{o.title}</h1>
          <div className="flex flex-wrap items-center gap-1.5 mt-1">
            <Badge kind={o.classification}>{o.classification === "verified" ? "verified arbitrage" : o.classification === "potential" ? "potential — needs verification" : "invalid"}</Badge>
            <Badge kind={o.state} />
            {o.is_simulated && <Badge kind="sim">simulated data</Badge>}
            {o.match_status && <Badge kind={o.match_status}>{MATCH_STATUS_LABEL[o.match_status]}</Badge>}
            <span className="text-xs text-mute">{o.strategy_label} · {catLabel(o.category)} · first seen {ago(o.first_seen)} · quotes up to {duration(o.max_quote_age_s)} old</span>
          </div>
        </div>
        <div className="text-right">
          <div className={`font-mono text-2xl ${signClass(ev?.worst_case_profit)}`}>{usd(ev?.worst_case_profit)}</div>
          <div className="text-[11px] text-mute">worst-case modelled profit at depth-optimal size · net ROI <span className={signClass(ev?.net_roi_pct)}>{price(ev?.net_roi_pct, 3)}%</span></div>
        </div>
      </div>

      {(o.state !== "active" || errors.length > 0) && (
        <div className="border border-neg/40 bg-neg-dim/30 text-neg text-xs px-3 py-2 rounded-sm">
          Not currently executable as modelled: {errors.map((f) => f.message).join("; ") || `state is ${o.state}`}{o.end_reason ? ` — ${o.end_reason}` : ""}.
        </div>
      )}
      <div className="border border-ink-700 bg-ink-900 text-[11px] text-mute px-3 py-2 rounded-sm">{d.disclaimer || disclaimer}</div>

      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_380px]">
        <div className="space-y-3 min-w-0">
          <Panel title="Positions (legs)" bodyClass="p-0">
            <div className="overflow-x-auto">
              <table className="dense">
                <thead><tr><th>Leg</th><th>Exchange</th><th>Buy</th><th className="num">Best ask</th><th className="num">Size @ ask</th><th className="num">Depth</th><th>Fees</th><th>Status</th><th className="num">Quote age</th><th>Quote time</th></tr></thead>
                <tbody>
                  {o.legs.map((l, i) => (
                    <tr key={i}>
                      <td className="max-w-[260px] truncate text-gray-200" title={l.title}>{l.label.replace(l.exchange, exName(l.exchange))}</td>
                      <td>{exName(l.exchange)}</td>
                      <td className={l.side === "yes" ? "text-pos" : "text-neg"}>{l.side.toUpperCase()}</td>
                      <td className="num">{price(l.best_ask)}</td>
                      <td className="num">{qty(l.best_ask_size)}</td>
                      <td className="num">{qty(l.depth)}</td>
                      <td>{l.fee_verified ? <span className="text-mute">{l.fee_kind}</span> : <span className="text-warn">{l.fee_kind} (est.)</span>}</td>
                      <td><Badge kind={l.status === "open" ? "pass" : "warn"}>{l.status}</Badge></td>
                      <td className={`num ${l.quote_age_s > 45 ? "text-neg" : l.quote_age_s > 15 ? "text-warn" : "text-gray-300"}`}>{duration(l.quote_age_s)}</td>
                      <td className="font-mono text-[11px] text-mute" title={l.exchange_ts ? `exchange timestamp ${l.exchange_ts}` : "no exchange timestamp"}>{time(l.received_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>

          <Panel title="Flags & freshness warnings"><FlagList flags={o.flags} /></Panel>

          <Panel title="Profit vs position size (order-book depth)"><ProfitCurve curve={s.curve} /></Panel>

          <div className="grid gap-3 md:grid-cols-2">
            <Panel title="Opportunity history">
              <LinesChart data={d.history} x="ts" xFmt={time} lines={[{ key: "net_roi", name: "Net ROI %", color: C.pos }, { key: "max_profit", name: "Max profit $", color: C.accent }]} height={180} />
            </Panel>
            <Panel title="Combined entry price">
              <LinesChart data={d.history} x="ts" xFmt={time} yFmt={(v) => v.toFixed(3)} refY={Number(s.guaranteed_payout_per_set ?? 1)} lines={[{ key: "combined_price", name: "Σ best asks", color: C.warn }]} height={180} />
            </Panel>
          </div>
          <Panel title="Historical prices (stored samples)"><PriceHistoryChart prices={d.prices} /></Panel>
        </div>

        <div className="space-y-3 min-w-0">
          <Panel title="Arbitrage analysis"><SizingSummary s={s} />
            {s.reasons.length > 0 && <ul className="mt-2 text-[11px] text-warn list-disc pl-4">{s.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>}
          </Panel>
          <Panel title="Capital per exchange (optimal size)">
            {ev ? <KV rows={Object.entries(ev.outlay_by_exchange).map(([k, v]) => [exName(k), usd(v)])} /> : <Empty>No profitable size.</Empty>}
          </Panel>
          <Panel title="Scenario payouts (optimal size)">
            {ev ? (
              <table className="dense"><thead><tr><th>Outcome</th><th className="num">Payout</th><th className="num">Profit</th></tr></thead>
                <tbody>{Object.entries(ev.scenario_payouts).map(([k, v]) => <tr key={k}><td className="whitespace-normal">{k}</td><td className="num">{usd(v)}</td><td className={`num ${signClass(ev.scenario_profits[k])}`}>{usd(ev.scenario_profits[k])}</td></tr>)}</tbody>
              </table>
            ) : <Empty>—</Empty>}
          </Panel>
          {d.group && (
            <Panel title="Outcome set">
              <KV rows={[["Event", d.group.title], ["Mutually exclusive", String(d.group.mutually_exclusive)], ["Exhaustive (verified)", String(d.group.exhaustive)], ["Note", d.group.exhaustive_reason]]} />
            </Panel>
          )}
        </div>
      </div>

      <OpportunityCalculator oppId={o.id} exchanges={o.legs.map((l) => l.exchange)} />

      {d.match && (
        <Panel title="Settlement comparison (cross-exchange)" right={<Badge kind={d.match.status}>{MATCH_STATUS_LABEL[d.match.status]}</Badge>}>
          <div className="space-y-2">
            <ul className="text-xs list-disc pl-4 text-gray-300">{d.match.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
            <ChecksTable checks={d.match.checks} />
            <Link className="text-xs text-accent" to={`/markets?tab=matches`}>Review / approve this match in Market Explorer →</Link>
          </div>
        </Panel>
      )}

      <div className="grid gap-3 md:grid-cols-2">
        {d.markets.map((m, i) => (
          <Panel key={i} title={m ? `${exName(m.exchange)} · ${m.external_id}` : "Market unavailable"} right={m?.url ? <a className="text-accent" href={m.url} target="_blank" rel="noreferrer noopener">Official page ↗</a> : undefined}>
            {m ? (
              <div className="space-y-3">
                <div className="text-sm text-gray-100">{m.title}</div>
                <KV rows={[
                  ["Event", m.event_title ?? "—"], ["Outcome", m.outcome_label ?? "—"], ["Closes", dt(m.close_time)], ["Expires", dt(m.expiration_time)],
                  ["YES bid / ask", `${price(m.book?.yes_bids[0]?.[0])} / ${price(m.book?.yes_asks[0]?.[0])}`],
                  ["NO bid / ask", `${price(m.book?.no_bids[0]?.[0])} / ${price(m.book?.no_asks[0]?.[0])}`],
                  ["Payout", `$${m.payout}`], ["Fee model", `${m.fee.kind}${m.fee.verified ? "" : " (estimated)"}`],
                  ["Resolution sources", m.resolution_sources.join("; ") || "—"],
                ]} />
                <OrderBookView book={m.book} stale={m.book_stale} focusSide={o.legs[i]?.side} />
                <RulesText m={m} />
              </div>
            ) : <Empty>Market data unavailable.</Empty>}
          </Panel>
        ))}
      </div>
    </div>
  );
}
