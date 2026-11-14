import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Notification } from "../api";
import { OppTable, oppSortValue } from "../components/OppTable";
import { Badge, Metric, Panel } from "../components/ui";
import { ago, dt, exName, pct, qty, usd } from "../format";
import { useLive } from "../live";

export default function Dashboard() {
  const { summary, opps, version } = useLive();
  const [notes, setNotes] = useState<Notification[]>([]);
  useEffect(() => {
    api.get<{ items: Notification[] }>("/notifications", { limit: 8 }).then((r) => setNotes(r.items)).catch(() => {});
  }, [version]);

  const top = useMemo(() => {
    const live = opps.filter((o) => o.state === "active" && (o.classification === "verified" || o.classification === "potential"));
    const rest = opps.filter((o) => !live.includes(o));
    const sorted = [...live].sort((a, b) => {
      if (a.classification !== b.classification) return a.classification === "verified" ? -1 : 1;
      return (oppSortValue(b, "net_roi") as number) - (oppSortValue(a, "net_roi") as number);
    });
    return [...sorted, ...rest].slice(0, 10);
  }, [opps]);

  const s = summary;
  return (
    <div className="space-y-3">
      <div className="flex items-baseline justify-between gap-2 flex-wrap">
        <h1 className="text-base font-semibold text-gray-100 tracking-wide">Dashboard</h1>
        <span className="text-[11px] text-mute">last scan {ago(s?.last_scan)} · {s?.matches ?? 0} market matches</span>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2">
        <Metric label="Active verified" value={s?.active_verified ?? "—"} tone={s?.active_verified ? "pos" : "mute"} sub="all checks passed" />
        <Metric label="Potential (needs verification)" value={s?.active_potential ?? "—"} tone={s?.active_potential ? "warn" : "mute"} sub="settlement / fees unverified" />
        <Metric label="Highest net ROI" value={pct(s?.highest_net_roi)} tone={s?.highest_net_roi ? "pos" : "mute"} sub="after fees, worst case" />
        <Metric label="Average net ROI" value={pct(s?.average_net_roi)} sub="verified + potential" />
        <Metric label="Highest est. executable profit" value={usd(s?.highest_profit)} tone={s?.highest_profit ? "pos" : "mute"} sub="depth-limited, theoretical" />
        <Metric label="Total profitable capital" value={usd(s?.total_profitable_capital)} sub={`${qty(s?.total_profitable_contracts)} contract sets`} />
        <Metric label="Connected exchanges" value={s ? `${s.connected_exchanges}/${s.connectors.length}` : "—"} sub={s?.exchanges.map(exName).join(", ")} />
        <Metric label="Monitored markets" value={s?.monitored_markets?.toLocaleString() ?? "—"} sub={`${s?.categories.length ?? 0} categories`} />
        <Metric label="Order books tracked" value={s?.books_tracked ?? "—"} sub={`${s?.stale_or_unavailable ?? 0} stale/unavailable opps`} />
        <Metric label="Last market update" value={ago(s?.last_market_update)} sub={dt(s?.last_market_update)} tone={s?.last_market_update ? "default" : "mute"} />
      </div>

      <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_320px]">
        <Panel title="Top opportunities" right={<Link className="text-accent" to="/live">All live arbitrage →</Link>} bodyClass="p-0">
          <OppTable opps={top} />
        </Panel>
        <div className="space-y-3 min-w-0">
          <Panel title="Data connections">
            <ul className="space-y-2">
              {s?.connectors.map((c) => {
                const ok = c.healthy ?? c.connected;
                return (
                  <li key={c.name} className="text-xs">
                    <div className="flex items-center justify-between gap-2">
                      <span className="flex items-center gap-1.5"><span className={`h-1.5 w-1.5 rounded-full ${ok ? "bg-pos" : "bg-mute-dim"}`} />{c.display_name}</span>
                      {c.simulated ? <Badge kind="sim">simulated</Badge> : <Badge kind={ok ? "pass" : "unknown"}>{ok ? "connected" : "down"}</Badge>}
                    </div>
                    <div className="text-[11px] text-mute pl-3">
                      {c.markets.toLocaleString()} markets · {c.books} books · {c.stream_connected ? "WebSocket streaming" : c.streaming ? "stream reconnecting" : "REST polling"} · ok {ago(c.last_success)}
                    </div>
                    {c.last_error && <div className="text-[11px] text-neg pl-3 truncate" title={c.last_error}>{c.last_error}</div>}
                    {c.notes.map((n, i) => <div key={i} className="text-[11px] text-mute-dim pl-3">{n}</div>)}
                  </li>
                );
              }) ?? <li className="text-xs text-mute">—</li>}
            </ul>
          </Panel>
          <Panel title="Recent notifications" right={<Link className="text-accent" to="/alerts">Alerts →</Link>}>
            {notes.length === 0 ? <div className="text-xs text-mute">No notifications yet.</div> : (
              <ul className="space-y-1.5">
                {notes.map((n) => (
                  <li key={n.id} className="text-xs border-l-2 pl-2 border-ink-600">
                    <div className="flex justify-between gap-2"><span className={n.read ? "text-mute" : "text-gray-100"}>{n.title}</span><span className="text-mute-dim text-[10px] whitespace-nowrap">{ago(n.created_at)}</span></div>
                    <div className="text-mute truncate" title={n.body}>{n.body}</div>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </div>
      </div>
    </div>
  );
}
