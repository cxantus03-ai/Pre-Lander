import { useState, type ReactNode } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";
import { useLive } from "../live";
import { ago, exName } from "../format";

const NAV = [
  { to: "/", label: "Dashboard", key: "1" },
  { to: "/live", label: "Live Arbitrage", key: "2" },
  { to: "/markets", label: "Market Explorer", key: "3" },
  { to: "/opportunity", label: "Opportunity Details", key: "4" },
  { to: "/calculator", label: "Arbitrage Calculator", key: "5" },
  { to: "/capital", label: "Capital Management", key: "6" },
  { to: "/analytics", label: "Historical Analytics", key: "7" },
  { to: "/alerts", label: "Alerts", key: "8" },
  { to: "/settings", label: "Settings", key: "9" },
];

function ModeBanner() {
  const { summary, wsConnected } = useLive();
  if (!summary) {
    return <div className="bg-ink-800 text-mute text-xs px-4 py-1.5 border-b border-ink-700">Connecting to ArbiScan backend…</div>;
  }
  if (summary.simulated || summary.mode === "demo") {
    return (
      <div className="bg-warn-dim border-b border-warn/50 text-warn text-xs px-4 py-1.5 font-semibold tracking-wide flex flex-wrap gap-x-4 gap-y-1">
        <span>DEMO / SIMULATED DATA — not real Polymarket or Kalshi prices</span>
        <span className="font-normal text-warn/80">{summary.mode_reason}</span>
      </div>
    );
  }
  return (
    <div className="bg-ink-900 border-b border-ink-700 text-xs px-4 py-1.5 flex flex-wrap items-center gap-x-4 gap-y-1">
      <span className="text-pos font-semibold tracking-wide">LIVE DATA</span>
      {summary.connectors.map((c) => {
        const ok = c.healthy ?? c.connected;
        return (
          <span key={c.name} className="flex items-center gap-1.5">
            <span className={`h-1.5 w-1.5 rounded-full ${ok ? "bg-pos" : "bg-mute-dim"}`} />
            <span className={ok ? "text-gray-200" : "text-mute"}>{c.display_name}</span>
            <span className="text-mute">{c.connected ? (c.stream_connected ? "streaming" : "REST polling") : "disconnected"}</span>
          </span>
        );
      })}
      <span className="text-mute">last update {ago(summary.last_market_update)}</span>
      <span className={wsConnected ? "text-mute" : "text-warn"}>{wsConnected ? "push: live" : "push: offline (polling)"}</span>
    </div>
  );
}

function Toasts() {
  const { toasts, dismissToast } = useLive();
  if (!toasts.length) return null;
  return (
    <div className="fixed bottom-3 right-3 z-50 space-y-2 w-[min(360px,calc(100vw-24px))]">
      {toasts.map((t) => (
        <div key={t.id} className={`border bg-ink-850 px-3 py-2 text-xs shadow-lg rounded-sm ${t.severity === "success" ? "border-pos/50" : t.severity === "warning" ? "border-warn/50" : "border-ink-500"}`}>
          <div className="flex justify-between gap-2">
            <span className={`font-semibold ${t.severity === "success" ? "text-pos" : t.severity === "warning" ? "text-warn" : "text-gray-100"}`}>{t.title}</span>
            <button className="text-mute hover:text-gray-200" onClick={() => dismissToast(t.id)} aria-label="Dismiss">×</button>
          </div>
          <div className="text-mute mt-0.5 break-words">{t.body}</div>
          {t.opportunity_id && <Link className="text-accent mt-1 inline-block" to={`/opportunity/${t.opportunity_id}`}>Open analysis →</Link>}
        </div>
      ))}
    </div>
  );
}

export function Layout({ children }: { children: ReactNode }) {
  const { unread, summary, disclaimer } = useLive();
  const [open, setOpen] = useState(false);
  const loc = useLocation();
  const nav = (
    <nav className="flex flex-col py-2">
      {NAV.map((n) => (
        <NavLink key={n.to} to={n.to} end={n.to === "/"} onClick={() => setOpen(false)}
          className={({ isActive }) => {
            const active = isActive || (n.to === "/opportunity" && loc.pathname.startsWith("/opportunity"));
            return `flex items-center gap-2 px-3 py-1.5 text-[13px] border-l-2 ${active ? "border-accent bg-ink-800 text-gray-100" : "border-transparent text-mute hover:text-gray-200 hover:bg-ink-850"}`;
          }}>
          <span className="font-mono text-[10px] text-mute-dim w-3">{n.key}</span>
          <span className="flex-1">{n.label}</span>
          {n.to === "/alerts" && unread > 0 && <span className="font-mono text-[10px] bg-accent/20 text-accent px-1.5 rounded-sm">{unread > 99 ? "99+" : unread}</span>}
          {n.to === "/live" && summary && summary.active_verified + summary.active_potential > 0 && (
            <span className="font-mono text-[10px] text-pos">{summary.active_verified}<span className="text-mute">/</span><span className="text-warn">{summary.active_potential}</span></span>
          )}
        </NavLink>
      ))}
    </nav>
  );
  return (
    <div className="min-h-screen flex flex-col bg-ink-950 text-gray-200">
      <ModeBanner />
      <div className="flex flex-1 min-h-0">
        <aside className="hidden lg:flex w-52 shrink-0 flex-col border-r border-ink-700 bg-ink-900">
          <div className="px-3 py-3 border-b border-ink-700">
            <div className="font-mono font-bold tracking-widest text-gray-100">ARBI<span className="text-pos">SCAN</span></div>
            <div className="text-[10px] text-mute uppercase tracking-wider">Prediction-market arbitrage research</div>
          </div>
          {nav}
          <div className="mt-auto px-3 py-2 text-[10px] text-mute-dim border-t border-ink-700">Read-only research tool · never places trades</div>
        </aside>
        <div className="flex-1 min-w-0 flex flex-col">
          <div className="lg:hidden flex items-center justify-between px-4 py-2 border-b border-ink-700 bg-ink-900">
            <div className="font-mono font-bold tracking-widest">ARBI<span className="text-pos">SCAN</span></div>
            <button className="text-xs border border-ink-500 px-2 py-1 rounded-sm" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
              Menu{unread > 0 ? ` (${unread})` : ""}
            </button>
          </div>
          {open && <div className="lg:hidden border-b border-ink-700 bg-ink-900">{nav}</div>}
          <main className="flex-1 min-w-0 p-3 sm:p-4">{children}</main>
          <footer className="border-t border-ink-700 px-4 py-2 text-[10px] text-mute-dim leading-relaxed">
            {disclaimer} Exchanges: {summary?.exchanges.map(exName).join(", ") || "—"}. Not investment advice.
          </footer>
        </div>
      </div>
      <Toasts />
    </div>
  );
}
