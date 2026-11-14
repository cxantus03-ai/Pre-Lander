// Typed client for the ArbiScan backend. All secrets live on the backend; the
// browser only ever talks to /api and /ws on the same origin.

export type Num = string | number | null | undefined;

export interface Flag { level: "error" | "warn" | "info"; code: string; message: string }

export interface LegInfo {
  market_key: string; exchange: string; side: "yes" | "no"; label: string; title: string;
  outcome_label: string | null; url: string | null; best_ask: string | null; best_ask_size: string | null;
  depth: string; fee_kind: string; fee_verified: boolean; quote_age_s: number; status: string;
  received_at: string; exchange_ts: string | null;
}

export interface OppSummary {
  id: string; strategy: string; strategy_label: string; title: string; category: string;
  classification: "verified" | "potential" | "invalid"; state: "active" | "stale" | "unavailable" | "invalid" | "expired";
  legs: LegInfo[]; combined_price: string | null; guaranteed_payout_per_set: string | null; gross_pct: string | null;
  net_roi_pct: string | null; max_profit: string | null; gross_profit: string | null; fees: string | null;
  capital_required: string | null; optimal_qty: string | null; max_profitable_qty: string | null;
  liquidity_qty: string | null; top_of_book_qty: string | null; user_qty: string | null; user_profit: string | null;
  user_limiting_factor: string | null; flags: Flag[]; match_id: string | null; match_status: string | null;
  close_time: string | null; first_seen: string; last_seen: string; age_s: number; max_quote_age_s: number;
  is_simulated: boolean; exchanges: string[]; cross_exchange: boolean; market_type: "binary" | "multi";
  invalid_reason: string | null; ended_at: string | null; end_reason: string | null;
}

export interface LegExec {
  leg_id: string; exchange: string; market_key: string; side: string; label: string; qty: string;
  fills: [string, string][]; levels_consumed: number; vwap: string | null; worst_price: string;
  cost: string; fees: string; total: string; fee_verified: boolean;
}

export interface Evaluation {
  qty: string; legs: LegExec[]; total_cost: string; total_fees: string; total_outlay: string;
  guaranteed_payout: string; scenario_payouts: Record<string, string>; scenario_profits: Record<string, string>;
  gross_profit: string; worst_case_profit: string; net_roi_pct: string; outlay_by_exchange: Record<string, string>;
}

export interface Sizing {
  combined_top_price: string | null; guaranteed_payout_per_set: string | null; gross_profit_per_set_top: string | null;
  gross_pct_top: string | null; fee_adjusted_cost_per_set_top: string | null; net_profit_per_set_top: string | null;
  top_of_book_qty: string; liquidity_qty: string; optimal_qty: string; max_profitable_qty: string;
  constrained_qty: string; recommended_qty: string; limiting_factor: string; is_profitable: boolean;
  top_eval: Evaluation | null; optimal_eval: Evaluation | null; recommended_eval: Evaluation | null;
  curve: { qty: string; outlay: string; worst_profit: string }[]; reasons: string[];
}

export interface CalcResult extends Sizing {
  remaining_balances: Record<string, string>;
  constraints: Record<string, unknown>;
  disclaimer: string;
  scenarios?: string[];
}

export interface ConnectorStatus {
  name: string; display_name: string; connected: boolean; simulated: boolean; last_success: string | null;
  last_error: string | null; markets: number; books: number; streaming: boolean; stream_connected: boolean;
  notes: string[]; healthy?: boolean;
}

export interface Summary {
  mode: string; mode_reason: string; simulated: boolean; active_verified: number; active_potential: number;
  stale_or_unavailable: number; highest_net_roi: number | null; average_net_roi: number | null; highest_profit: number | null;
  total_profitable_capital: number; total_profitable_contracts: number; connected_exchanges: number; exchanges: string[];
  monitored_markets: number; books_tracked: number; matches: number; categories: string[];
  last_market_update: string | null; last_scan: string | null; connectors: ConnectorStatus[];
  counters: Record<string, number>; matching: Record<string, unknown>; disclaimer?: string;
}

export interface Book {
  market_key: string; yes_bids: [string, string][]; yes_asks: [string, string][]; no_bids: [string, string][];
  no_asks: [string, string][]; exchange_ts: string | null; received_at: string; source: string;
  depth_complete: boolean; force_stale: boolean;
}

export interface FeeInfo { kind: string; rate: string; exponent: string; multiplier: string; rounding_increment: string; verified: boolean; source: string; notes: string }

export interface MarketRow {
  key: string; exchange: string; external_id: string; title: string; event_id: string | null; event_title: string | null;
  outcome_label: string | null; yes_label: string; no_label: string; category: string; raw_category: string | null;
  status: string; url: string | null; close_time: string | null; expiration_time: string | null; payout: string;
  tick_size: string; min_order_size: string; qty_increment: string; fee: FeeInfo; volume: string; volume_24h: string;
  open_interest: string | null; liquidity: string | null; is_simulated: boolean;
  top: { yes_bid: string | null; yes_ask: string | null; no_bid: string | null; no_ask: string | null } | null;
  has_book: boolean; book_age_s: number | null; match_ids?: string[];
}

export interface MarketDetail extends MarketRow {
  description: string; rules_primary: string; rules_secondary: string; resolution_sources: string[];
  can_close_early: boolean | null; early_close_condition: string | null; extra: Record<string, unknown>;
  book: Book | null; book_stale: boolean | null; exchange_updated_at: string | null; received_at: string;
  matches?: Match[]; prices?: PriceData;
}

export interface Check { code: string; label: string; result: "pass" | "warn" | "fail" | "unknown"; detail: string; a: string | null; b: string | null }

export interface Match {
  id: string; market_a: string; market_b: string; candidate_score: number; semantic_score: number;
  orientation: "same" | "inverted"; checks: Check[]; status: string; reasons: string[]; method: string;
  llm: unknown; implication: { implying: string; implied: string; direction: string } | null;
  a?: MarketRow | MarketDetail | null; b?: MarketRow | MarketDetail | null;
  reviews?: { action: string; previous_status: string; new_status: string; orientation: string | null; note: string | null; created_at: string }[];
}

export interface PricePoint { ts: string; yes_bid: number | null; yes_ask: number | null; no_bid: number | null; no_ask: number | null; spread: number | null; yes_ask_depth: number | null; no_ask_depth: number | null }
export interface PriceData { series: Record<string, PricePoint[]>; difference: Record<string, number | string>[] }

export interface OppDetail {
  opportunity: OppSummary; sizing: Sizing; user_sizing: Sizing; markets: (MarketDetail | null)[]; match: Match | null;
  group: { title: string; mutually_exclusive: boolean | null; exhaustive: boolean | null; exhaustive_reason: string; market_keys: string[] } | null;
  history: { ts: string; net_roi: number; max_profit: number; combined_price: number | null; liquidity: number; state: string; classification: string }[];
  prices: PriceData; disclaimer: string;
}

export interface Capital {
  total_capital: string; exchange_balances: Record<string, string>; reserved_capital: string;
  max_allocation_per_trade: string | null; max_pct_per_trade: string | null; max_total_open_allocation: string | null;
  min_profit: string; min_roi_pct: string; max_slippage_pct: string | null; max_days_to_settlement: number | null;
  open_allocation: string; exchanges: string[]; warnings: string[];
}

export interface AlertRule {
  id?: number; name: string; enabled: boolean; event_types: string[]; min_net_roi: number | null; min_profit: number | null;
  min_qty: number | null; min_liquidity: number | null; price_change_threshold: number; categories: string[];
  exchanges: string[]; cross_exchange_only: boolean; verified_only: boolean; notify_in_app: boolean;
  notify_browser: boolean; notify_email: boolean; email_to: string | null; cooldown_s: number;
}

export interface Notification {
  id: number; alert_id: number | null; event_type: string; severity: string; title: string; body: string;
  opportunity_id: string | null; channels: string[]; email_status: string | null; read: boolean; created_at: string | null;
}

export interface Trade {
  id: number; opportunity_id: string | null; title: string; category: string; strategy: string;
  legs: { exchange: string; side: string; market?: string | null; price: string; qty: string; fees: string }[];
  theoretical_profit: string | null; total_cost: string; total_fees: string; entered_at: string; notes: string | null;
  settlement: { result: string; payout_received: string; additional_fees: string; realized_pnl: string; settled_at: string; notes: string | null } | null;
}

export interface StatusResp {
  mode: string; mode_reason: string; connectors: ConnectorStatus[]; runtime: Record<string, number>;
  settings: Record<string, unknown>; started_at: string; matching: Record<string, unknown>; disclaimer: string;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) { super(message); this.status = status; }
}

type Params = Record<string, string | number | boolean | null | undefined | (string | number)[]>;

export function qs(params?: Params): string {
  if (!params) return "";
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => u.append(k, String(x)));
    else u.append(k, String(v));
  }
  const s = u.toString();
  return s ? `?${s}` : "";
}

async function request<T>(method: string, path: string, body?: unknown, params?: Params): Promise<T> {
  const res = await fetch(`/api${path}${qs(params)}`, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      if (j?.detail) msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch { /* ignore */ }
    throw new ApiError(res.status, msg);
  }
  return res.json() as Promise<T>;
}

export const api = {
  get: <T,>(path: string, params?: Params) => request<T>("GET", path, undefined, params),
  post: <T,>(path: string, body?: unknown, params?: Params) => request<T>("POST", path, body ?? {}, params),
  put: <T,>(path: string, body: unknown) => request<T>("PUT", path, body),
  del: <T,>(path: string) => request<T>("DELETE", path),
};
