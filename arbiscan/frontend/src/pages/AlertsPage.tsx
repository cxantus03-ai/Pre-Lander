import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type AlertRule, type Notification } from "../api";
import { Badge, btnCls, btnPrimary, Check, Empty, ErrorBox, Field, inputCls, NumInput, Panel } from "../components/ui";
import { CATEGORIES, catLabel, dt, exName } from "../format";
import { useLive } from "../live";

const blank = (): AlertRule => ({
  name: "", enabled: true, event_types: ["new_verified"], min_net_roi: null, min_profit: null, min_qty: null, min_liquidity: null,
  price_change_threshold: 0.03, categories: [], exchanges: [], cross_exchange_only: false, verified_only: false,
  notify_in_app: true, notify_browser: true, notify_email: false, email_to: null, cooldown_s: 300,
});

export default function AlertsPage() {
  const { summary, version, refreshUnread } = useLive();
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [events, setEvents] = useState<Record<string, string>>({});
  const [emailConfigured, setEmailConfigured] = useState(false);
  const [notes, setNotes] = useState<Notification[]>([]);
  const [edit, setEdit] = useState<AlertRule | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [perm, setPerm] = useState<string>(typeof Notification !== "undefined" ? Notification.permission : "unsupported");

  const load = () => {
    api.get<AlertRule[]>("/alerts/rules").then(setRules).catch(setError);
    api.get<{ items: Notification[] }>("/notifications", { limit: 200 }).then((r) => setNotes(r.items)).catch(setError);
  };
  useEffect(() => {
    api.get<{ event_types: Record<string, string>; public: Record<string, unknown> }>("/settings").then((s) => {
      setEvents(s.event_types); setEmailConfigured(Boolean(s.public.email_configured));
    }).catch(setError);
  }, []);
  useEffect(load, [version]);

  const save = async () => {
    if (!edit) return;
    setError(null);
    const body = { ...edit, email_to: edit.email_to || null };
    delete (body as Partial<AlertRule>).id;
    try {
      if (edit.id) await api.put(`/alerts/rules/${edit.id}`, body); else await api.post("/alerts/rules", body);
      setEdit(null); load();
    } catch (e) { setError(e); }
  };
  const remove = async (id: number) => { try { await api.del(`/alerts/rules/${id}`); load(); } catch (e) { setError(e); } };
  const toggleRule = async (r: AlertRule) => {
    const body = { ...r, enabled: !r.enabled }; delete (body as Partial<AlertRule>).id;
    delete (body as Record<string, unknown>).created_at;
    try { await api.put(`/alerts/rules/${r.id}`, body); load(); } catch (e) { setError(e); }
  };
  const markAll = async () => { await api.post("/notifications/read", []); load(); refreshUnread(); };
  const requestPerm = async () => {
    if (typeof Notification === "undefined") return;
    const p = await Notification.requestPermission();
    setPerm(p);
    if (p === "granted") new Notification("ArbiScan", { body: "Browser notifications enabled." });
  };

  const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);
  const num = (v: string) => (v === "" ? null : Number(v));
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-base font-semibold text-gray-100">Alerts</h1>
        <div className="flex items-center gap-2 text-xs">
          <span className="text-mute">Browser notifications: <span className={perm === "granted" ? "text-pos" : "text-warn"}>{perm}</span></span>
          {perm !== "granted" && perm !== "unsupported" && <button className={btnPrimary} onClick={requestPerm}>Enable desktop notifications</button>}
          <button className={btnPrimary} onClick={() => setEdit(blank())}>+ New rule</button>
        </div>
      </div>
      <ErrorBox error={error} />
      {edit && (
        <Panel title={edit.id ? `Edit rule #${edit.id}` : "New alert rule"} right={<button className={btnCls} onClick={() => setEdit(null)}>Cancel</button>}>
          <div className="space-y-3">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
              <Field label="Name"><input className={inputCls} value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></Field>
              <Field label="Min net ROI %"><NumInput value={edit.min_net_roi?.toString() ?? ""} onChange={(v) => setEdit({ ...edit, min_net_roi: num(v) })} /></Field>
              <Field label="Min $ profit"><NumInput value={edit.min_profit?.toString() ?? ""} onChange={(v) => setEdit({ ...edit, min_profit: num(v) })} min={0} /></Field>
              <Field label="Min contract qty"><NumInput value={edit.min_qty?.toString() ?? ""} onChange={(v) => setEdit({ ...edit, min_qty: num(v) })} min={0} /></Field>
              <Field label="Min liquidity (contracts)"><NumInput value={edit.min_liquidity?.toString() ?? ""} onChange={(v) => setEdit({ ...edit, min_liquidity: num(v) })} min={0} /></Field>
              <Field label="Price change threshold ($)"><NumInput value={String(edit.price_change_threshold)} onChange={(v) => setEdit({ ...edit, price_change_threshold: Number(v) || 0.03 })} min={0} /></Field>
              <Field label="Cooldown (s)" hint="dedup window per opportunity"><NumInput value={String(edit.cooldown_s)} onChange={(v) => setEdit({ ...edit, cooldown_s: Number(v) || 300 })} min={10} /></Field>
              <Field label="Email to" hint={emailConfigured ? "SMTP configured" : "SMTP not configured on backend"}><input className={inputCls} value={edit.email_to ?? ""} onChange={(e) => setEdit({ ...edit, email_to: e.target.value })} /></Field>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-wider text-mute mb-1">Trigger on</div>
              <div className="flex flex-wrap gap-x-4 gap-y-1">{Object.entries(events).map(([k, l]) => <Check key={k} label={l} checked={edit.event_types.includes(k)} onChange={() => setEdit({ ...edit, event_types: toggle(edit.event_types, k) })} />)}</div>
            </div>
            <div className="grid md:grid-cols-3 gap-3">
              <div>
                <div className="text-[10px] uppercase tracking-wider text-mute mb-1">Categories (none = all)</div>
                <div className="flex flex-wrap gap-x-3 gap-y-1">{CATEGORIES.map((c) => <Check key={c} label={catLabel(c)} checked={edit.categories.includes(c)} onChange={() => setEdit({ ...edit, categories: toggle(edit.categories, c) })} />)}</div>
              </div>
              <div>
                <div className="text-[10px] uppercase tracking-wider text-mute mb-1">Exchanges (none = all)</div>
                <div className="flex flex-wrap gap-x-3 gap-y-1">{(summary?.exchanges ?? []).map((x) => <Check key={x} label={exName(x)} checked={edit.exchanges.includes(x)} onChange={() => setEdit({ ...edit, exchanges: toggle(edit.exchanges, x) })} />)}</div>
                <div className="flex flex-col gap-1 mt-2">
                  <Check label="Cross-exchange only" checked={edit.cross_exchange_only} onChange={(v) => setEdit({ ...edit, cross_exchange_only: v })} />
                  <Check label="Verified opportunities only" checked={edit.verified_only} onChange={(v) => setEdit({ ...edit, verified_only: v })} />
                </div>
              </div>
              <div>
                <div className="text-[10px] uppercase tracking-wider text-mute mb-1">Delivery</div>
                <div className="flex flex-col gap-1">
                  <Check label="Enabled" checked={edit.enabled} onChange={(v) => setEdit({ ...edit, enabled: v })} />
                  <Check label="In-app" checked={edit.notify_in_app} onChange={(v) => setEdit({ ...edit, notify_in_app: v })} />
                  <Check label="Browser desktop" checked={edit.notify_browser} onChange={(v) => setEdit({ ...edit, notify_browser: v })} />
                  <Check label="Email" checked={edit.notify_email} onChange={(v) => setEdit({ ...edit, notify_email: v })} />
                </div>
              </div>
            </div>
            <button className={btnPrimary} onClick={save} disabled={!edit.name || !edit.event_types.length}>Save rule</button>
          </div>
        </Panel>
      )}
      <Panel title="Alert rules" bodyClass="p-0">
        <div className="overflow-x-auto">
          <table className="dense">
            <thead><tr><th>Rule</th><th>Status</th><th>Events</th><th>Thresholds</th><th>Scope</th><th>Channels</th><th className="num">Cooldown</th><th></th></tr></thead>
            <tbody>
              {rules.map((r) => (
                <tr key={r.id}>
                  <td className="text-gray-100">{r.name}</td>
                  <td><button onClick={() => toggleRule(r)}><Badge kind={r.enabled ? "pass" : "unknown"}>{r.enabled ? "on" : "off"}</Badge></button></td>
                  <td className="whitespace-normal min-w-[200px] text-[11px] text-gray-300">{r.event_types.map((e) => events[e] ?? e).join(", ")}</td>
                  <td className="text-[11px] text-mute">{[r.min_net_roi !== null && `ROI≥${r.min_net_roi}%`, r.min_profit !== null && `$≥${r.min_profit}`, r.min_qty !== null && `qty≥${r.min_qty}`, r.min_liquidity !== null && `liq≥${r.min_liquidity}`].filter(Boolean).join(" ") || "—"}</td>
                  <td className="text-[11px] text-mute">{[r.verified_only && "verified only", r.cross_exchange_only && "cross only", r.categories.length && r.categories.join("/"), r.exchanges.length && r.exchanges.map(exName).join("/")].filter(Boolean).join(" · ") || "all"}</td>
                  <td className="text-[11px] text-mute">{[r.notify_in_app && "in-app", r.notify_browser && "browser", r.notify_email && `email${r.email_to ? `→${r.email_to}` : ""}`].filter(Boolean).join(", ")}</td>
                  <td className="num">{r.cooldown_s}s</td>
                  <td className="space-x-2"><button className="text-accent text-xs" onClick={() => setEdit({ ...r })}>edit</button><button className="text-neg text-xs" onClick={() => r.id && remove(r.id)}>delete</button></td>
                </tr>
              ))}
            </tbody>
          </table>
          {!rules.length && <Empty>No alert rules.</Empty>}
        </div>
      </Panel>
      <Panel title={`Notifications (${notes.filter((n) => !n.read).length} unread)`} right={<button className={btnCls} onClick={markAll}>Mark all read</button>} bodyClass="p-0">
        <div className="max-h-[50vh] overflow-auto">
          <table className="dense">
            <thead><tr><th>Time</th><th>Type</th><th>Title</th><th>Detail</th><th>Channels</th><th></th></tr></thead>
            <tbody>
              {notes.map((n) => (
                <tr key={n.id} className={n.read ? "opacity-60" : ""}>
                  <td className="text-mute">{dt(n.created_at)}</td>
                  <td><Badge kind={n.severity === "success" ? "pass" : n.severity === "warning" ? "warn" : "info"}>{n.event_type.replace(/_/g, " ")}</Badge></td>
                  <td className="text-gray-100">{n.title}</td>
                  <td className="text-mute max-w-[420px] truncate" title={n.body}>{n.body}</td>
                  <td className="text-[11px] text-mute">{n.channels.join(", ")}{n.email_status ? ` (email: ${n.email_status})` : ""}</td>
                  <td>{n.opportunity_id && <Link className="text-accent text-xs" to={`/opportunity/${n.opportunity_id}`}>open</Link>}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!notes.length && <Empty>No notifications yet.</Empty>}
        </div>
      </Panel>
    </div>
  );
}
