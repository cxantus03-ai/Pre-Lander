import type { Num } from "./api";

export function n(x: Num): number | null {
  if (x === null || x === undefined || x === "") return null;
  const v = typeof x === "number" ? x : parseFloat(x);
  return Number.isFinite(v) ? v : null;
}

export function usd(x: Num, digits = 2): string {
  const v = n(x);
  if (v === null) return "—";
  return (v < 0 ? "-$" : "$") + Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function pct(x: Num, digits = 2): string {
  const v = n(x);
  if (v === null) return "—";
  return `${v.toFixed(digits)}%`;
}

export function price(x: Num, digits = 3): string {
  const v = n(x);
  if (v === null) return "—";
  return v.toFixed(digits);
}

export function qty(x: Num): string {
  const v = n(x);
  if (v === null) return "—";
  return v.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

export function ago(iso: string | null | undefined): string {
  if (!iso) return "—";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  return duration(s) + " ago";
}

export function duration(s: number | null | undefined): string {
  if (s === null || s === undefined || !Number.isFinite(s)) return "—";
  if (s < 0) s = 0;
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
  return `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`;
}

export function dt(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("en-US", { month: "short", day: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function date(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

export function time(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

const EX_NAMES: Record<string, string> = {
  polymarket: "Polymarket",
  kalshi: "Kalshi",
  demo_poly: "SIM Poly",
  demo_kalshi: "SIM Kalshi",
};
export function exName(code: string | null | undefined): string {
  if (!code) return "—";
  return EX_NAMES[code] ?? code;
}

export function signClass(x: Num): string {
  const v = n(x);
  if (v === null) return "text-mute";
  if (v > 0) return "text-pos";
  if (v < 0) return "text-neg";
  return "text-mute";
}

export const CATEGORIES = ["politics", "sports", "economics", "crypto", "weather", "entertainment", "science_tech", "finance", "other"];
export const catLabel = (c: string) => (c === "science_tech" ? "Science/Tech" : c.charAt(0).toUpperCase() + c.slice(1));

export const MATCH_STATUS_LABEL: Record<string, string> = {
  verified_compatible: "Verified compatible",
  likely_compatible: "Likely — needs review",
  incompatible: "Incompatible",
  insufficient_information: "Insufficient info",
};
