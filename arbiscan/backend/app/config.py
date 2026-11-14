"""Application configuration.

All credentials and tunables come from environment variables (or a local .env
file that is never committed). Nothing here is ever sent to the frontend except
the explicitly whitelisted, non-secret values exposed by ``public_settings``.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="ARBISCAN_", extra="ignore")

    # --- runtime -----------------------------------------------------------
    env: Literal["development", "production", "test"] = "development"
    log_level: str = "INFO"
    # live  = only real exchange data
    # demo  = only clearly-labelled simulated venues
    # auto  = live if any exchange answers at start-up, otherwise demo
    data_mode: Literal["live", "demo", "auto"] = "auto"
    database_url: str = "sqlite:///./arbiscan.db"
    redis_url: str | None = None
    cors_origins: str = "http://localhost:5173,http://localhost:8080"
    collector_enabled: bool = True

    # --- Polymarket (public data; no credentials required) -----------------
    polymarket_enabled: bool = True
    polymarket_gamma_url: str = "https://gamma-api.polymarket.com"
    polymarket_clob_url: str = "https://clob.polymarket.com"
    polymarket_ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    polymarket_max_events: int = 2000
    polymarket_rps: float = 8.0
    polymarket_ws_enabled: bool = True
    # Polymarket publishes a per-market ``feeSchedule`` (rate, exponent, takerOnly).
    # The formula used to turn it into a dollar fee is implemented in fees.py.
    # Leave this False until you have checked the formula against the current
    # docs: profits on fee-enabled Polymarket markets are then labelled ESTIMATED.
    polymarket_fee_formula_verified: bool = False

    # --- Kalshi (public market data; key only needed for WebSocket feed) ---
    kalshi_enabled: bool = True
    kalshi_api_url: str = "https://api.elections.kalshi.com/trade-api/v2"
    kalshi_ws_url: str = "wss://api.elections.kalshi.com/trade-api/ws/v2"
    kalshi_api_key_id: str | None = None
    kalshi_private_key_path: str | None = None
    kalshi_private_key_pem: SecretStr | None = None
    kalshi_max_markets: int = 20000
    kalshi_rps: float = 8.0

    # --- collection cadence -------------------------------------------------
    discovery_interval_s: int = 300  # full market discovery / reconciliation
    book_poll_interval_s: int = 15  # REST order-book polling for watched markets
    max_watched_books: int = 400  # books fetched each poll (matched pairs first)
    stale_after_s: int = 45  # a quote older than this is STALE
    max_close_horizon_days: int = 400  # ignore markets closing later than this

    # --- matching -----------------------------------------------------------
    match_min_candidate_score: float = 0.25
    match_max_candidates_per_market: int = 5
    match_date_tolerance_hours: int = 24
    llm_matching_enabled: bool = False  # optional hook; never sufficient on its own
    anthropic_api_key: SecretStr | None = None
    llm_model: str = "claude-opus-5-5"
    llm_max_reviews_per_cycle: int = 20

    # --- detection defaults -------------------------------------------------
    min_net_roi_pct: float = 0.0
    min_net_profit_usd: float = 0.0
    default_bankroll_usd: float = 1000.0
    scan_debounce_ms: int = 250

    # --- persistence --------------------------------------------------------
    price_history_interval_s: int = 60
    snapshot_interval_s: int = 30

    # --- notifications ------------------------------------------------------
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from: str | None = None
    smtp_use_tls: bool = True
    alert_cooldown_s: int = 300

    demo_seed: int | None = None
    demo_tick_s: float = 2.0

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def kalshi_credentials_configured(self) -> bool:
        return bool(self.kalshi_api_key_id and (self.kalshi_private_key_path or self.kalshi_private_key_pem))

    @property
    def email_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_from)

    def public_settings(self) -> dict:
        """Non-secret values that may be shown in the UI."""
        return {
            "data_mode": self.data_mode,
            "polymarket_enabled": self.polymarket_enabled,
            "polymarket_ws_enabled": self.polymarket_ws_enabled,
            "polymarket_fee_formula_verified": self.polymarket_fee_formula_verified,
            "kalshi_enabled": self.kalshi_enabled,
            "kalshi_credentials_configured": self.kalshi_credentials_configured,
            "discovery_interval_s": self.discovery_interval_s,
            "book_poll_interval_s": self.book_poll_interval_s,
            "max_watched_books": self.max_watched_books,
            "stale_after_s": self.stale_after_s,
            "llm_matching_enabled": self.llm_matching_enabled,
            "email_configured": self.email_configured,
            "redis_configured": bool(self.redis_url),
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()


