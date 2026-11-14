import { memo } from "react";
import { useNavigate } from "react-router-dom";
import type { OppSummary } from "../api";
import { catLabel, date, duration, exName, MATCH_STATUS_LABEL, n, pct, price, qty, signClass, usd } from "../format";
import { Badge, Empty } from "./ui";

export function legPair(o: OppSummary) {
  if (o.legs.length !== 2) return null;
  const yes = o.legs.find((l) => l.side === "yes") ?? o.legs[0];
  const no = o.legs.find((l) => l !== yes) ?? o.legs[1];
  return { yes, no };
}

function freshnessKind(age: number): string {
  if (age < 15) return "text-pos";
  if (age < 45) return "text-warn";
  return "text-mute";
}

const Row = memo(function Row({ o, extended, onOpen }: { o: OppSummary; extended: boolean; onOpen: (id: string) => void }) {
  const pair = legPair(o);
  const rowTone = o.state === "expired" || o.classification === "invalid" ? "opacity-60" : o.state !== "active" ? "opacity-75" : "";
  const roiCls = o.classification === "verified" && o.state === "active" ? "text-pos" : o.classification === "potential" ? "text-warn" : signClass(o.net_roi_pct);
  return (
    <tr className={`cursor-pointer ${rowTone}`} onClick={() => onOpen(o.id)}>
      <td className="max-w-[340px]">
        <div className="flex items-center gap-1.5">
          <Badge kind={o.classification} />
          {o.state !== "active" && <Badge kind={o.state} />}
          {o.is_simulated && <Badge kind="sim">sim</Badge>}
          <span className="truncate text-gray-100" title={o.title}>{o.title}</span>
        </div>
        <div className="text-[10px] text-mute truncate">{o.strategy_label}</div>
      </td>
      {pair ? (
        <>
          <td className="text-gray-300">{exName(pair.yes.exchange)} <span className="text-mute text-[10px]">{pair.yes.side.toUpperCase()}</span></td>
          <td className="num">{price(pair.yes.best_ask)}</td>
          <td className="text-gray-300">{exName(pair.no.exchange)} <span className="text-mute text-[10px]">{pair.no.side.toUpperCase()}</span></td>
          <td className="num">{price(pair.no.best_ask)}</td>
        </>
      ) : (
        <>
          <td className="text-gray-300" colSpan={2}>{o.legs.length} legs · {[...new Set(o.exchanges)].map(exName).join(", ")}</td>
          <td className="text-mute text-[11px]" colSpan={2}>Σ asks <span className="font-mono text-gray-200">{price(o.combined_price)}</span> vs payout {price(o.guaranteed_payout_per_set, 0)}</td>
        </>
      )}
      <td className={`num font-semibold ${roiCls}`}>{pct(o.net_roi_pct)}</td>
      <td className={`num ${signClass(o.max_profit)}`}>{usd(o.max_profit)}</td>
      <td className="num text-gray-300">{qty(o.liquidity_qty)}</td>
      {extended && (
        <>
          <td className="text-gray-300">{catLabel(o.category)}</td>
          <td className="num text-gray-300">{date(o.close_time)}</td>
          <td className="num">{usd(o.fees)}</td>
          <td className="num">{qty(o.optimal_qty)}</td>
          <td>{o.match_status ? <Badge kind={o.match_status}>{MATCH_STATUS_LABEL[o.match_status] ?? o.match_status}</Badge> : <span className="text-mute text-[11px]">structural</span>}</td>
          <td className={`num ${freshnessKind(o.max_quote_age_s)}`}>{duration(o.max_quote_age_s)}</td>
          <td className="num">{usd(o.capital_required)}</td>
          <td className="num text-gray-300">{duration(o.age_s)}</td>
          <td className="text-mute text-[11px]">{o.cross_exchange ? "cross" : "same"} · {o.market_type}</td>
        </>
      )}
    </tr>
  );
});

export function OppTable({ opps, extended = false, maxHeight }: { opps: OppSummary[]; extended?: boolean; maxHeight?: string }) {
  const nav = useNavigate();
  if (!opps.length) return <Empty>No opportunities match. Detected opportunities appear here as soon as order books show a positive modelled edge.</Empty>;
  return (
    <div className="overflow-auto" style={{ maxHeight }}>
      <table className="dense">
        <thead>
          <tr>
            <th>Event</th><th>YES Exchange</th><th className="num">YES Price</th><th>NO Exchange</th><th className="num">NO Price</th>
            <th className="num">Net ROI</th><th className="num">Max Profit</th><th className="num">Liquidity</th>
            {extended && (<><th>Category</th><th className="num">Closes</th><th className="num">Fees</th><th className="num">Avail Qty</th><th>Settlement</th><th className="num">Freshness</th><th className="num">Capital Req</th><th className="num">Age</th><th>Type</th></>)}
          </tr>
        </thead>
        <tbody>
          {opps.map((o) => <Row key={o.id} o={o} extended={extended} onOpen={(id) => nav(`/opportunity/${id}`)} />)}
        </tbody>
      </table>
    </div>
  );
}

export function oppSortValue(o: OppSummary, sort: string): number | string {
  switch (sort) {
    case "profit": return n(o.max_profit) ?? -Infinity;
    case "liquidity": return n(o.liquidity_qty) ?? -Infinity;
    case "capital": return n(o.capital_required) ?? Infinity;
    case "recent": return new Date(o.first_seen).getTime();
    case "closing": return o.close_time ? new Date(o.close_time).getTime() : Infinity;
    case "exchange": return [...o.exchanges].sort().join(",");
    case "category": return o.category;
    default: return n(o.net_roi_pct) ?? -Infinity;
  }
}
