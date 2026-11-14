"""End-to-end API tests against the running app in DEMO mode."""
import os
import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    db = tmp_path_factory.mktemp("db") / "t.db"
    url = os.environ.get("ARBISCAN_TEST_DATABASE_URL") or f"sqlite:///{db}"  # e.g. a throwaway PostgreSQL database
    s = Settings(data_mode="demo", database_url=url, demo_tick_s=0.2, demo_seed=7, book_poll_interval_s=5,
                 scan_debounce_ms=20, polymarket_enabled=False, kalshi_enabled=False)
    app = create_app(s)
    with TestClient(app) as c:
        for _ in range(100):
            if c.get("/api/dashboard/summary").json()["books_tracked"] > 0 and c.get("/api/matches").json()["total"] > 0:
                break
            time.sleep(0.1)
        yield c


def test_health_and_mode(client):
    r = client.get("/api/health").json()
    assert r["ok"] and r["mode"] == "demo"
    st = client.get("/api/status").json()
    assert st["connectors"][0]["simulated"] is True
    assert "kalshi_private_key_pem" not in str(st) and "smtp_password" not in str(st)


def test_dashboard_marks_simulated(client):
    d = client.get("/api/dashboard/summary").json()
    assert d["simulated"] is True and d["monitored_markets"] >= 20


def test_markets_and_detail(client):
    items = client.get("/api/markets", params={"limit": 5}).json()["items"]
    assert items and all(i["is_simulated"] for i in items)
    d = client.get("/api/markets/detail", params={"key": items[0]["key"]}).json()
    assert d["key"] == items[0]["key"] and "rules_primary" in d


def test_matches_and_manual_review(client):
    ms = client.get("/api/matches").json()["items"]
    target = next(m for m in ms if m["status"] == "likely_compatible")
    r = client.post(f"/api/matches/{target['id']}/review", json={"action": "approve", "note": "rules read"}).json()
    assert r["status"] == "verified_compatible" and r["method"] == "manual"
    r = client.post(f"/api/matches/{target['id']}/review", json={"action": "reset"}).json()
    assert r["method"] == "auto"
    assert client.get(f"/api/matches/{target['id']}").json()["reviews"]


def test_opportunities_and_calculator(client):
    found = None
    for _ in range(150):
        items = client.get("/api/opportunities", params={"include_invalid": True}).json()["items"]
        found = next((o for o in items if o["state"] in ("active", "stale", "unavailable", "invalid")), None)
        if found:
            break
        time.sleep(0.1)
    assert found, "demo should produce candidate structures"
    det = client.get(f"/api/opportunities/{found['id']}")
    if det.status_code == 404:
        pytest.skip("opportunity pruned between calls")
    body = det.json()
    assert "settlement" in body["disclaimer"] or "resolution" in body["disclaimer"]
    calc = client.post(f"/api/opportunities/{found['id']}/calculate",
                       json={"total_capital": 600, "exchange_balances": {"demo_poly": 500, "demo_kalshi": 50}})
    if calc.status_code == 409:
        pytest.skip("book changed")
    c = calc.json()
    spent = (c.get("recommended_eval") or {}).get("outlay_by_exchange", {})
    assert float(spent.get("demo_kalshi", 0)) <= 50.0
    assert "remaining_balances" in c


def test_manual_calculator(client):
    body = {"structure": "binary", "legs": [
        {"exchange": "polymarket", "side": "yes", "levels": [[0.43, 100], [0.44, 200]]},
        {"exchange": "kalshi", "side": "no", "levels": [[0.54, 50], [0.55, 150]], "fee_kind": "kalshi_quadratic"}],
        "constraints": {"exchange_balances": {"polymarket": 500, "kalshi": 100}}}
    r = client.post("/api/calculator/manual", json=body).json()
    assert float(r["recommended_eval"]["outlay_by_exchange"]["kalshi"]) <= 100
    assert float(r["recommended_qty"]) > 0
    # no-arb case
    body["legs"][0]["levels"] = [[0.52, 100]]
    body["legs"][1]["levels"] = [[0.51, 100]]
    r = client.post("/api/calculator/manual", json=body).json()
    assert r["recommended_qty"] == "0" and not r["is_profitable"]


def test_input_validation(client):
    assert client.put("/api/capital", json={"total_capital": -5}).status_code == 422
    assert client.post("/api/calculator/manual", json={"legs": [{"exchange": "x", "side": "yes", "levels": [[0, 1]]}]}).status_code == 422
    assert client.post("/api/alerts/rules", json={"name": "x", "event_types": ["nope"]}).status_code == 422


def test_capital_roundtrip(client):
    r = client.put("/api/capital", json={"total_capital": 600, "exchange_balances": {"demo_poly": 500, "demo_kalshi": 100},
                                         "max_slippage_pct": 2}).json()
    assert r["exchange_balances"]["demo_kalshi"] == "100"
    assert client.get("/api/capital").json()["total_capital"].startswith("600")


def test_alert_rule_crud_and_notifications(client):
    r = client.post("/api/alerts/rules", json={"name": "ROI", "event_types": ["roi_threshold", "new_potential"], "min_net_roi": 0.1}).json()
    assert r["id"]
    assert any(a["id"] == r["id"] for a in client.get("/api/alerts/rules").json())
    assert client.delete(f"/api/alerts/rules/{r['id']}").json()["ok"]
    assert "items" in client.get("/api/notifications").json()


def test_trades_theoretical_vs_realized(client):
    t = client.post("/api/trades", json={"title": "Test pair", "theoretical_profit": "3.00", "legs": [
        {"exchange": "demo_poly", "side": "YES", "price": "0.43", "qty": "100"},
        {"exchange": "demo_kalshi", "side": "NO", "price": "0.54", "qty": "100", "fees": "0.50"}]}).json()
    assert t["total_cost"].startswith("97")
    perf = client.get("/api/analytics/performance").json()
    assert perf["realized_pnl"] == 0.0 and perf["open"] == 1  # nothing realized until settled
    s = client.post(f"/api/trades/{t['id']}/settle", json={"result": "YES", "payout_received": "100"}).json()
    assert s["settlement"]["realized_pnl"].startswith("2.5")
    perf = client.get("/api/analytics/performance").json()
    assert abs(perf["realized_pnl"] - 2.5) < 1e-9


def test_analytics_endpoints(client):
    a = client.get("/api/analytics/opportunities").json()
    assert "THEORETICAL" in a["note"]
    assert isinstance(client.get("/api/analytics/signals").json(), list)


def test_runtime_settings_clamped(client):
    r = client.put("/api/settings/runtime", json={"book_poll_interval_s": 5, "stale_after_s": 30}).json()
    assert r["book_poll_interval_s"] == 5 and r["stale_after_s"] == 30
    assert client.put("/api/settings/runtime", json={"book_poll_interval_s": 1}).status_code == 422


def test_websocket_hello(client):
    with client.websocket_connect("/ws") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "hello" and msg["mode"] == "demo"
