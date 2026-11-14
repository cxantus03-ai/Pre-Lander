import { useEffect, useState } from "react";
import { api, type StatusResp } from "../api";
import { Badge, btnPrimary, ErrorBox, Field, KV, NumInput, Panel } from "../components/ui";
import { ago } from "../format";

const RUNTIME: [string, string, number, number][] = [
  ["book_poll_interval_s", "Order-book poll interval (s)", 5, 600],
  ["discovery_interval_s", "Market discovery interval (s)", 60, 3600],
  ["stale_after_s", "Quote is stale after (s)", 5, 900],
  ["max_watched_books", "Max order books polled", 10, 2000],
];

export default function SettingsPage() {
  const [st, setSt] = useState<StatusResp | null>(null);
  const [rt, setRt] = useState<Record<string, string>>({});
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);
  const load = () => api.get<StatusResp>("/status").then((s) => {
    setSt(s);
    setRt(Object.fromEntries(Object.entries(s.runtime).map(([k, v]) => [k, String(v)])));
  }).catch(setError);
  useEffect(() => { load(); }, []);
  const save = async () => {
    setError(null); setSaved(false);
    try {
      await api.put("/settings/runtime", Object.fromEntries(RUNTIME.map(([k]) => [k, rt[k] === "" ? null : Number(rt[k])])));
      setSaved(true); load();
    } catch (e) { setError(e); }
  };
  return (
    <div className="space-y-3 max-w-5xl">
      <h1 className="text-base font-semibold text-gray-100">Settings</h1>
      <ErrorBox error={error} />
      <div className="grid gap-3 md:grid-cols-2">
        <Panel title="Data mode & connectors">
          {st && (
            <div className="space-y-3">
              <KV rows={[["Mode", <Badge kind={st.mode === "demo" ? "sim" : "pass"}>{st.mode}</Badge>], ["Reason", st.mode_reason], ["Started", ago(st.started_at)]]} />
              {st.connectors.map((c) => (
                <div key={c.name} className="border border-ink-700 bg-ink-950 p-2 rounded-sm text-xs">
                  <div className="flex justify-between"><span className="text-gray-100">{c.display_name}</span><Badge kind={c.simulated ? "sim" : c.connected ? "pass" : "fail"}>{c.simulated ? "simulated" : c.connected ? "connected" : "disconnected"}</Badge></div>
                  <div className="text-mute mt-1">{c.markets} markets · {c.books} books · stream {c.stream_connected ? "connected" : c.streaming ? "reconnecting" : "off"} · last ok {ago(c.last_success)}</div>
                  {c.last_error && <div className="text-neg mt-0.5 break-words">{c.last_error}</div>}
                  {c.notes.map((n, i) => <div key={i} className="text-mute-dim">{n}</div>)}
                </div>
              ))}
            </div>
          )}
        </Panel>
        <Panel title="Refresh cadence (within provider rate limits)">
          <div className="grid grid-cols-2 gap-2">
            {RUNTIME.map(([k, label, lo, hi]) => (
              <Field key={k} label={label} hint={`${lo}–${hi}`}><NumInput value={rt[k] ?? ""} min={lo} onChange={(v) => setRt({ ...rt, [k]: v })} /></Field>
            ))}
          </div>
          <div className="flex items-center gap-2 mt-3">
            <button className={btnPrimary} onClick={save}>Apply</button>
            {saved && <span className="text-xs text-pos">Applied.</span>}
          </div>
          <p className="text-[11px] text-mute mt-2">Kalshi order books are fetched one request per market; lower intervals with many watched books are bounded by the client-side rate limiter (429 responses trigger automatic back-off).</p>
        </Panel>
      </div>
      <Panel title="Backend configuration (read-only, non-secret)">
        {st && <KV rows={Object.entries(st.settings).map(([k, v]) => [k, String(v)])} />}
      </Panel>
      <Panel title="Connecting exchange APIs">
        <div className="text-xs text-gray-300 space-y-2 leading-relaxed">
          <p><span className="text-gray-100 font-semibold">Polymarket</span> — market discovery (Gamma API), order books (CLOB API) and the market WebSocket are public. No key, wallet or account is needed. Set <code className="text-accent">ARBISCAN_POLYMARKET_ENABLED=true</code> (default).</p>
          <p><span className="text-gray-100 font-semibold">Kalshi</span> — events, markets, series fees and order books are public REST endpoints; no key is needed. Real-time WebSocket order-book streaming requires a read-only API key: create one in your Kalshi account settings (API Keys), then set <code className="text-accent">ARBISCAN_KALSHI_API_KEY_ID</code> and <code className="text-accent">ARBISCAN_KALSHI_PRIVATE_KEY_PATH</code> (path to the downloaded RSA private key) in the <span className="text-gray-100">backend</span> environment and restart. Without it, ArbiScan polls REST books.</p>
          <p><span className="text-gray-100 font-semibold">Email alerts</span> — set <code className="text-accent">ARBISCAN_SMTP_HOST</code>, <code className="text-accent">ARBISCAN_SMTP_PORT</code>, <code className="text-accent">ARBISCAN_SMTP_USER</code>, <code className="text-accent">ARBISCAN_SMTP_PASSWORD</code>, <code className="text-accent">ARBISCAN_SMTP_FROM</code> on the backend.</p>
          <p className="text-warn">Never paste API keys, passwords, wallet seed phrases or private keys into this browser UI. All credentials belong only in the backend's environment variables / .env file. ArbiScan never asks for exchange passwords and cannot trade.</p>
        </div>
      </Panel>
    </div>
  );
}
