import type { Book } from "../api";
import { ago, n, price, qty } from "../format";
import { Badge } from "./ui";

function Ladder({ title, levels, tone, highlight }: { title: string; levels: [string, string][]; tone: "bid" | "ask"; highlight?: boolean }) {
  const rows = levels.slice(0, 12);
  const max = Math.max(1, ...rows.map(([, s]) => n(s) ?? 0));
  let cum = 0;
  return (
    <div className={`min-w-0 ${highlight ? "ring-1 ring-accent/50" : ""}`}>
      <div className="text-[10px] uppercase tracking-wider text-mute px-1 py-0.5 flex justify-between">
        <span>{title}</span><span>size · cum</span>
      </div>
      {rows.length === 0 && <div className="text-[11px] text-mute px-1 py-1">empty</div>}
      {rows.map(([p, s], i) => {
        const size = n(s) ?? 0;
        cum += size;
        return (
          <div key={i} className="relative font-mono text-[11px] tabular-nums flex justify-between px-1 leading-5">
            <div className={`absolute inset-y-0 ${tone === "bid" ? "right-0 bg-pos/10" : "right-0 bg-neg/10"}`} style={{ width: `${(size / max) * 100}%` }} />
            <span className={`relative ${tone === "bid" ? "text-pos" : "text-neg"}`}>{price(p)}</span>
            <span className="relative text-gray-300">{qty(size)} <span className="text-mute-dim">· {qty(cum)}</span></span>
          </div>
        );
      })}
    </div>
  );
}

export function OrderBookView({ book, focusSide, stale }: { book: Book | null | undefined; focusSide?: "yes" | "no"; stale?: boolean | null }) {
  if (!book) return <div className="text-xs text-mute">No order book loaded for this market yet.</div>;
  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 text-[10px] text-mute mb-1">
        <span>source {book.source}</span>
        <span>received {ago(book.received_at)}</span>
        {book.exchange_ts && <span>exchange ts {new Date(book.exchange_ts).toLocaleTimeString()}</span>}
        {stale && <Badge kind="stale">stale</Badge>}
        {book.force_stale && <Badge kind="error">feed disconnected</Badge>}
        {!book.depth_complete && <Badge kind="warn">incomplete depth</Badge>}
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div className="space-y-2">
          <Ladder title="YES asks (buy YES)" levels={book.yes_asks} tone="ask" highlight={focusSide === "yes"} />
          <Ladder title="YES bids" levels={book.yes_bids} tone="bid" />
        </div>
        <div className="space-y-2">
          <Ladder title="NO asks (buy NO)" levels={book.no_asks} tone="ask" highlight={focusSide === "no"} />
          <Ladder title="NO bids" levels={book.no_bids} tone="bid" />
        </div>
      </div>
    </div>
  );
}
