import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { api, type Notification, type OppSummary, type Summary } from "./api";

interface LiveState {
  summary: Summary | null;
  opps: OppSummary[];
  wsConnected: boolean;
  version: number; // increments on every push so pages can refetch
  lastPush: number | null;
  toasts: Notification[];
  unread: number;
  dismissToast: (id: number) => void;
  refreshUnread: () => void;
  disclaimer: string;
}

const Ctx = createContext<LiveState | null>(null);

export const DEFAULT_DISCLAIMER =
  "Theoretical analysis only. Even a verified mathematical arbitrage can fail to deliver the modelled result because of execution, resolution, access, or counterparty risks. ArbiScan never places trades.";

export function LiveProvider({ children }: { children: ReactNode }) {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [opps, setOpps] = useState<OppSummary[]>([]);
  const [wsConnected, setWs] = useState(false);
  const [version, setVersion] = useState(0);
  const [lastPush, setLastPush] = useState<number | null>(null);
  const [toasts, setToasts] = useState<Notification[]>([]);
  const [unread, setUnread] = useState(0);
  const [disclaimer, setDisclaimer] = useState(DEFAULT_DISCLAIMER);
  const wsRef = useRef<WebSocket | null>(null);
  const attempt = useRef(0);

  const refreshUnread = useCallback(() => {
    api.get<{ unread: number }>("/notifications", { limit: 1 }).then((r) => setUnread(r.unread)).catch(() => {});
  }, []);

  const poll = useCallback(async () => {
    try {
      const [s, o] = await Promise.all([
        api.get<Summary>("/dashboard/summary"),
        api.get<{ items: OppSummary[] }>("/opportunities", { limit: 2000 }),
      ]);
      setSummary(s);
      if (s.disclaimer) setDisclaimer(s.disclaimer);
      setOpps(o.items);
      setVersion((v) => v + 1);
    } catch { /* backend unavailable; banner shows it */ }
  }, []);

  useEffect(() => {
    poll();
    refreshUnread();
    let closed = false;
    let timer: number | undefined;
    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(`${proto}://${location.host}/ws`);
      wsRef.current = ws;
      ws.onopen = () => { attempt.current = 0; setWs(true); };
      ws.onmessage = (ev) => {
        if (typeof ev.data !== "string" || ev.data === "pong") return;
        let msg: { type: string; data?: unknown; summary?: Summary };
        try { msg = JSON.parse(ev.data); } catch { return; }
        if (msg.type === "hello" || msg.type === "opportunities") {
          if (msg.summary) setSummary((prev) => ({ ...(prev ?? {}), ...msg.summary! }));
          if (Array.isArray(msg.data)) setOpps(msg.data as OppSummary[]);
          setVersion((v) => v + 1);
          setLastPush(Date.now());
        } else if (msg.type === "notification") {
          const n = msg.data as Notification;
          setToasts((t) => [n, ...t].slice(0, 4));
          setUnread((u) => u + 1);
          window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== n.id)), 8000);
          if (n.channels?.includes("browser") && "Notification" in window && Notification.permission === "granted") {
            try { new Notification(`ArbiScan: ${n.title}`, { body: n.body, tag: `arbiscan-${n.id}` }); } catch { /* ignore */ }
          }
        }
      };
      ws.onclose = () => {
        setWs(false);
        if (closed) return;
        attempt.current += 1;
        const delay = Math.min(30000, 1000 * 2 ** Math.min(attempt.current, 5));
        timer = window.setTimeout(connect, delay);
      };
      ws.onerror = () => ws.close();
    };
    connect();
    const ping = window.setInterval(() => { if (wsRef.current?.readyState === 1) wsRef.current.send("ping"); }, 25000);
    return () => { closed = true; window.clearTimeout(timer); window.clearInterval(ping); wsRef.current?.close(); };
  }, [poll, refreshUnread]);

  // Fallback polling while the WebSocket is down; slow summary refresh otherwise.
  useEffect(() => {
    const id = window.setInterval(() => { if (!wsConnected) poll(); }, 5000);
    const slow = window.setInterval(() => {
      api.get<Summary>("/dashboard/summary").then((s) => { setSummary(s); if (s.disclaimer) setDisclaimer(s.disclaimer); }).catch(() => {});
    }, 15000);
    return () => { window.clearInterval(id); window.clearInterval(slow); };
  }, [wsConnected, poll]);

  const dismissToast = (id: number) => setToasts((t) => t.filter((x) => x.id !== id));

  return (
    <Ctx.Provider value={{ summary, opps, wsConnected, version, lastPush, toasts, unread, dismissToast, refreshUnread, disclaimer }}>
      {children}
    </Ctx.Provider>
  );
}

export function useLive(): LiveState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useLive outside provider");
  return v;
}
