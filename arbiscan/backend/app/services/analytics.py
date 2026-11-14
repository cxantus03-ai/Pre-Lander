"""Historical analytics (pandas).

Theoretical figures come only from detected opportunities; realized figures
come only from trades the user records manually. The two are never mixed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import select

from ..db import session_scope
from ..models import OpportunityRow, OpportunitySnapshot, PriceHistory, RecordedTrade
from .persistence import as_utc


def _range(start: datetime | None, end: datetime | None) -> tuple[datetime, datetime]:
    end = as_utc(end) or datetime.now(timezone.utc)
    start = as_utc(start) or end - timedelta(days=30)
    return start, end


def opportunity_frame(start=None, end=None, exchange=None, category=None, strategy=None, include_simulated=True) -> pd.DataFrame:
    start, end = _range(start, end)
    with session_scope() as s:
        q = select(OpportunityRow).where(OpportunityRow.first_seen >= start, OpportunityRow.first_seen <= end)
        if category:
            q = q.where(OpportunityRow.category == category)
        if strategy:
            q = q.where(OpportunityRow.strategy == strategy)
        if not include_simulated:
            q = q.where(OpportunityRow.is_simulated.is_(False))
        rows = list(s.scalars(q))
        data = [{
            "id": r.id, "strategy": r.strategy, "category": r.category, "classification": r.classification, "state": r.state,
            "exchanges": r.exchanges or [], "exchange_pair": r.exchange_pair, "first_seen": as_utc(r.first_seen),
            "last_seen": as_utc(r.last_seen), "ended_at": as_utc(r.ended_at), "invalid_reason": r.invalid_reason,
            "peak_net_roi": r.peak_net_roi or 0.0, "peak_profit": r.peak_profit or 0.0, "is_simulated": r.is_simulated,
        } for r in rows]
    df = pd.DataFrame(data)
    if df.empty:
        return df
    for col in ("first_seen", "last_seen", "ended_at"):
        df[col] = pd.to_datetime(df[col], utc=True)
    if exchange:
        df = df[df["exchanges"].apply(lambda xs: exchange in xs)]
    return df


def opportunity_analytics(**filters) -> dict:
    df = opportunity_frame(**filters)
    if df.empty:
        return {"total": 0, "per_day": [], "by_exchange_pair": [], "by_category": [], "by_strategy": [], "duration_histogram": [],
                "average_net_roi": None, "highest_net_roi": None, "average_duration_s": None, "disappeared": 0,
                "invalid_fees": 0, "invalid_settlement": 0, "total_theoretical_profit": 0.0, "verified": 0, "potential": 0}
    profitable = df[df["classification"].isin(["verified", "potential"])].copy()
    profitable["day"] = profitable["first_seen"].dt.strftime("%Y-%m-%d")
    per_day = profitable.groupby(["day", "classification"]).size().unstack(fill_value=0).reset_index()
    per_day_roi = profitable.groupby("day")["peak_net_roi"].agg(["mean", "max"]).reset_index()
    per_day = per_day.merge(per_day_roi, on="day", how="left").rename(columns={"mean": "avg_net_roi", "max": "max_net_roi"})
    ended = profitable.dropna(subset=["ended_at"]).copy()
    ended["duration_s"] = (ended["ended_at"] - ended["first_seen"]).dt.total_seconds().clip(lower=0)
    bins = [0, 10, 30, 60, 300, 900, 3600, 86400, float("inf")]
    labels = ["<10s", "10-30s", "30-60s", "1-5m", "5-15m", "15-60m", "1-24h", ">24h"]
    hist = pd.cut(ended["duration_s"], bins=bins, labels=labels, right=False).value_counts().reindex(labels, fill_value=0) if not ended.empty else pd.Series(0, index=labels)
    return {
        "total": int(len(df)),
        "verified": int((df["classification"] == "verified").sum()),
        "potential": int((df["classification"] == "potential").sum()),
        "per_day": per_day.fillna(0).to_dict(orient="records"),
        "average_net_roi": float(profitable["peak_net_roi"].mean()) if not profitable.empty else None,
        "highest_net_roi": float(profitable["peak_net_roi"].max()) if not profitable.empty else None,
        "average_duration_s": float(ended["duration_s"].mean()) if not ended.empty else None,
        "disappeared": int((profitable["state"] == "expired").sum()),
        "invalid_fees": int(df["invalid_reason"].isin(["fees", "liquidity"]).sum()),
        "invalid_settlement": int((df["invalid_reason"] == "settlement").sum()),
        "total_theoretical_profit": float(profitable["peak_profit"].sum()),
        "by_exchange_pair": profitable.groupby("exchange_pair").size().sort_values(ascending=False).reset_index(name="count").to_dict(orient="records"),
        "by_category": profitable.groupby("category").agg(count=("id", "size"), theoretical_profit=("peak_profit", "sum")).reset_index().sort_values("count", ascending=False).to_dict(orient="records"),
        "by_strategy": profitable.groupby("strategy").size().reset_index(name="count").to_dict(orient="records"),
        "duration_histogram": [{"bucket": k, "count": int(v)} for k, v in hist.items()],
    }


def price_history(market_keys: list[str], start=None, end=None, max_points: int = 600) -> dict:
    start, end = _range(start, end)
    with session_scope() as s:
        rows = list(s.scalars(select(PriceHistory).where(PriceHistory.market_key.in_(market_keys), PriceHistory.ts >= start, PriceHistory.ts <= end).order_by(PriceHistory.ts)))
        data = [{"market_key": r.market_key, "ts": as_utc(r.ts), "yes_bid": r.yes_bid, "yes_ask": r.yes_ask, "no_bid": r.no_bid,
                 "no_ask": r.no_ask, "yes_ask_depth": r.yes_ask_depth, "no_ask_depth": r.no_ask_depth} for r in rows]
    df = pd.DataFrame(data)
    out: dict = {"series": {}, "difference": []}
    if df.empty:
        return out
    for col in ("yes_bid", "yes_ask", "no_bid", "no_ask", "yes_ask_depth", "no_ask_depth"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["spread"] = df["yes_ask"] - df["yes_bid"]
    for key, g in df.groupby("market_key"):
        g = g.iloc[:: max(1, len(g) // max_points)]
        out["series"][key] = [{"ts": r.ts.isoformat(), **{c: (None if pd.isna(getattr(r, c)) else float(getattr(r, c)))
                                for c in ("yes_bid", "yes_ask", "no_bid", "no_ask", "spread", "yes_ask_depth", "no_ask_depth")}}
                              for r in g.itertuples()]
    if len(market_keys) == 2:
        a, b = market_keys
        pa = df[df.market_key == a].set_index("ts")[["yes_ask", "no_ask"]].resample("1min").last()
        pb = df[df.market_key == b].set_index("ts")[["yes_ask", "no_ask"]].resample("1min").last()
        j = pa.join(pb, lsuffix="_a", rsuffix="_b", how="inner").dropna()
        j["yes_diff"] = j["yes_ask_a"] - j["yes_ask_b"]
        j["cost_a_yes_b_no"] = j["yes_ask_a"] + j["no_ask_b"]
        j["cost_b_yes_a_no"] = j["yes_ask_b"] + j["no_ask_a"]
        out["difference"] = [{"ts": ts.isoformat(), **{k: float(v) for k, v in row.items()}} for ts, row in j.tail(max_points).iterrows()]
    return out


def signal_frequency(start=None, end=None) -> list[dict]:
    """Profitable snapshots per hour (frequency of arbitrage signals)."""
    start, end = _range(start, end)
    with session_scope() as s:
        rows = s.execute(select(OpportunitySnapshot.ts, OpportunitySnapshot.classification).where(
            OpportunitySnapshot.ts >= start, OpportunitySnapshot.ts <= end, OpportunitySnapshot.classification.in_(["verified", "potential"]))).all()
    if not rows:
        return []
    df = pd.DataFrame(rows, columns=["ts", "classification"])
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    g = df.groupby([pd.Grouper(key="ts", freq="1h"), "classification"]).size().unstack(fill_value=0).reset_index()
    g["ts"] = g["ts"].dt.strftime("%Y-%m-%dT%H:00:00Z")
    return g.to_dict(orient="records")


def performance(start=None, end=None) -> dict:
    """Realized results from MANUALLY recorded trades only."""
    start, end = _range(start, end)
    with session_scope() as s:
        trades = list(s.scalars(select(RecordedTrade).where(RecordedTrade.entered_at >= start - timedelta(days=3650))))
        rows = []
        for t in trades:
            st = t.settlement
            rows.append({
                "id": t.id, "title": t.title, "category": t.category, "entered_at": as_utc(t.entered_at),
                "cost": float(t.total_cost), "fees": float(t.total_fees), "theoretical_profit": float(t.theoretical_profit) if t.theoretical_profit is not None else None,
                "settled": st is not None, "realized_pnl": float(st.realized_pnl) if st else None, "result": st.result if st else None,
            })
    df = pd.DataFrame(rows)
    if df.empty:
        return {"trades": 0, "open": 0, "settled": 0, "realized_pnl": 0.0, "theoretical_profit_of_recorded": 0.0, "capital_in_open_positions": 0.0, "cumulative": []}
    settled = df[df.settled]
    cum = settled.sort_values("entered_at").assign(cum=lambda d: d["realized_pnl"].cumsum()) if not settled.empty else settled
    return {
        "trades": int(len(df)),
        "open": int((~df.settled).sum()),
        "settled": int(df.settled.sum()),
        "realized_pnl": float(settled["realized_pnl"].sum()) if not settled.empty else 0.0,
        "theoretical_profit_of_recorded": float(df["theoretical_profit"].fillna(0).sum()),
        "capital_in_open_positions": float((df.loc[~df.settled, "cost"] + df.loc[~df.settled, "fees"]).sum()),
        "win_rate": float((settled["realized_pnl"] > 0).mean()) if not settled.empty else None,
        "cumulative": [{"ts": r.entered_at.isoformat(), "cum": float(r.cum)} for r in cum.itertuples()] if not settled.empty else [],
    }
