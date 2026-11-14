"""Notification transports: WebSocket hub (in-app + browser) and SMTP email."""
from __future__ import annotations

import asyncio
import json
import logging
import smtplib
from email.message import EmailMessage
from typing import Any

from ..config import Settings

log = logging.getLogger(__name__)


class WebSocketHub:
    def __init__(self) -> None:
        self.clients: set[Any] = set()

    async def connect(self, ws) -> None:
        await ws.accept()
        self.clients.add(ws)

    def disconnect(self, ws) -> None:
        self.clients.discard(ws)

    async def broadcast(self, message: dict) -> None:
        if not self.clients:
            return
        data = json.dumps(message, default=str)
        dead = []
        for ws in list(self.clients):
            try:
                await asyncio.wait_for(ws.send_text(data), timeout=2)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


class EmailSender:
    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def configured(self) -> bool:
        return self.settings.email_configured

    def send(self, to: str, subject: str, body: str) -> str:
        if not self.configured:
            return "not_configured"
        s = self.settings
        msg = EmailMessage()
        msg["From"] = s.smtp_from
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body + "\n\n— ArbiScan (research tool; theoretical analysis only, not trading advice)")
        try:
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
                if s.smtp_use_tls:
                    smtp.starttls()
                if s.smtp_user and s.smtp_password:
                    smtp.login(s.smtp_user, s.smtp_password.get_secret_value())
                smtp.send_message(msg)
            return "sent"
        except Exception as exc:  # noqa: BLE001
            log.warning("email send failed: %s", exc)
            return f"error: {type(exc).__name__}"
