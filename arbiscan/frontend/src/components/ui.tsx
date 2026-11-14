import { useEffect, useState, type ReactNode } from "react";
import type { Flag } from "../api";

export function Panel({ title, right, children, className = "", bodyClass = "" }: { title?: ReactNode; right?: ReactNode; children: ReactNode; className?: string; bodyClass?: string }) {
  return (
    <section className={`border border-ink-700 bg-ink-900 rounded-sm min-w-0 ${className}`}>
      {(title || right) && (
        <header className="flex items-center justify-between gap-2 px-3 py-1.5 border-b border-ink-700 bg-ink-850">
          <h2 className="text-[11px] uppercase tracking-wider text-mute font-semibold truncate">{title}</h2>
          <div className="flex items-center gap-2 text-xs">{right}</div>
        </header>
      )}
      <div className={`p-3 ${bodyClass}`}>{children}</div>
    </section>
  );
}

export function Metric({ label, value, sub, tone = "default" }: { label: string; value: ReactNode; sub?: ReactNode; tone?: "default" | "pos" | "warn" | "neg" | "mute" }) {
  const toneCls = { default: "text-gray-100", pos: "text-pos", warn: "text-warn", neg: "text-neg", mute: "text-mute" }[tone];
  return (
    <div className="border border-ink-700 bg-ink-900 px-3 py-2 rounded-sm min-w-0">
      <div className="text-[10px] uppercase tracking-wider text-mute truncate">{label}</div>
      <div className={`font-mono text-lg tabular-nums mt-0.5 truncate ${toneCls}`}>{value}</div>
      {sub && <div className="text-[11px] text-mute-dim truncate">{sub}</div>}
    </div>
  );
}

const BADGE: Record<string, string> = {
  verified: "bg-pos-dim text-pos border-pos/40",
  potential: "bg-warn-dim text-warn border-warn/40",
  invalid: "bg-neg-dim text-neg border-neg/40",
  expired: "bg-neg-dim text-neg border-neg/40",
  active: "bg-pos-dim/60 text-pos border-pos/30",
  stale: "bg-ink-700 text-mute border-ink-500",
  unavailable: "bg-ink-700 text-mute border-ink-500",
  verified_compatible: "bg-pos-dim text-pos border-pos/40",
  likely_compatible: "bg-warn-dim text-warn border-warn/40",
  incompatible: "bg-neg-dim text-neg border-neg/40",
  insufficient_information: "bg-ink-700 text-mute border-ink-500",
  pass: "bg-pos-dim text-pos border-pos/40",
  warn: "bg-warn-dim text-warn border-warn/40",
  fail: "bg-neg-dim text-neg border-neg/40",
  unknown: "bg-ink-700 text-mute border-ink-500",
  error: "bg-neg-dim text-neg border-neg/40",
  info: "bg-ink-700 text-mute border-ink-500",
  sim: "bg-warn-dim text-warn border-warn/50",
};

export function Badge({ kind, children, title }: { kind: string; children?: ReactNode; title?: string }) {
  return (
    <span title={title} className={`inline-flex items-center px-1.5 py-px text-[10px] uppercase tracking-wide border rounded-sm whitespace-nowrap ${BADGE[kind] ?? "bg-ink-700 text-mute border-ink-500"}`}>
      {children ?? kind}
    </span>
  );
}

export function FlagList({ flags }: { flags: Flag[] }) {
  if (!flags.length) return <div className="text-xs text-mute">No warnings.</div>;
  const order = { error: 0, warn: 1, info: 2 } as const;
  return (
    <ul className="space-y-1">
      {[...flags].sort((a, b) => order[a.level] - order[b.level]).map((f, i) => (
        <li key={i} className="flex items-start gap-2 text-xs">
          <Badge kind={f.level}>{f.code.replace(/_/g, " ")}</Badge>
          <span className={f.level === "error" ? "text-neg" : f.level === "warn" ? "text-warn" : "text-mute"}>{f.message}</span>
        </li>
      ))}
    </ul>
  );
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="block min-w-0">
      <span className="block text-[10px] uppercase tracking-wider text-mute mb-1">{label}</span>
      {children}
      {hint && <span className="block text-[10px] text-mute-dim mt-0.5">{hint}</span>}
    </label>
  );
}

export const inputCls = "w-full bg-ink-950 border border-ink-600 rounded-sm px-2 py-1 text-sm font-mono text-gray-100 focus:outline-none focus:border-accent";
export const btnCls = "inline-flex items-center gap-1 px-2.5 py-1 text-xs border border-ink-500 bg-ink-800 hover:bg-ink-700 text-gray-200 rounded-sm disabled:opacity-40";
export const btnPrimary = "inline-flex items-center gap-1 px-2.5 py-1 text-xs border border-accent/60 bg-accent/15 hover:bg-accent/25 text-accent rounded-sm disabled:opacity-40";

export function NumInput({ value, onChange, placeholder, step = "any", min }: { value: string; onChange: (v: string) => void; placeholder?: string; step?: string; min?: number }) {
  return <input className={inputCls} type="number" inputMode="decimal" step={step} min={min} value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />;
}

export function Check({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: ReactNode }) {
  return (
    <label className="inline-flex items-center gap-1.5 text-xs text-gray-300 cursor-pointer select-none">
      <input type="checkbox" className="accent-[#5aa9e6]" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  return <div className="border border-neg/40 bg-neg-dim/40 text-neg text-xs px-3 py-2 rounded-sm">{String((error as Error)?.message ?? error)}</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="text-xs text-mute py-6 text-center">{children}</div>;
}

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: { id: T; label: string }[]; value: T; onChange: (t: T) => void }) {
  return (
    <div className="flex gap-px border-b border-ink-700 overflow-x-auto">
      {tabs.map((t) => (
        <button key={t.id} onClick={() => onChange(t.id)}
          className={`px-3 py-1.5 text-xs uppercase tracking-wider whitespace-nowrap border-b-2 ${value === t.id ? "border-accent text-gray-100" : "border-transparent text-mute hover:text-gray-200"}`}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function useDebounced<T>(value: T, ms = 250): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const id = window.setTimeout(() => setV(value), ms);
    return () => window.clearTimeout(id);
  }, [value, ms]);
  return v;
}

export function KV({ rows }: { rows: [ReactNode, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[minmax(0,auto)_minmax(0,1fr)] gap-x-4 gap-y-1 text-xs">
      {rows.map(([k, v], i) => (
        <div key={i} className="contents">
          <dt className="text-mute whitespace-nowrap">{k}</dt>
          <dd className="font-mono tabular-nums text-gray-200 text-right break-words min-w-0">{v}</dd>
        </div>
      ))}
    </dl>
  );
}
