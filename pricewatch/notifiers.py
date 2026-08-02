"""
Where alerts go.

Adding a channel (Discord, email, webhook, desktop popup) means writing one
class with `send(alert) -> bool` and registering it in NOTIFIERS.
"""

from __future__ import annotations

from typing import Dict, Optional, Protocol

import requests

from .rules import Alert

DEFAULT_TIMEOUT = 20


class Notifier(Protocol):
    name: str

    def send(self, alert: Alert) -> bool:
        """Deliver the alert. Return True on success."""
        ...


class ConsoleNotifier:
    """Prints to stdout. Useful for testing a config before wiring Telegram."""

    name = "console"

    def send(self, alert: Alert) -> bool:
        print(f"[ALERT] {alert.message}")
        return True


class TelegramNotifier:
    """Sends to a Telegram chat via a bot token."""

    name = "telegram"

    def __init__(self, bot_token: str, chat_id: str, timeout: int = DEFAULT_TIMEOUT):
        if not bot_token or not chat_id:
            raise ValueError("TelegramNotifier needs both bot_token and chat_id")
        self.bot_token = bot_token
        self.chat_id = str(chat_id)
        self.timeout = timeout

    def send(self, alert: Alert) -> bool:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        try:
            response = requests.post(
                url,
                json={
                    "chat_id": self.chat_id,
                    "text": f"📈 {alert.message}",
                    "disable_web_page_preview": True,
                },
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            print(f"! telegram: request failed: {exc}")
            return False

        if response.status_code != 200:
            print(f"! telegram: HTTP {response.status_code}: {response.text[:200]}")
            return False
        return True


class WebhookNotifier:
    """POSTs the alert as JSON to any URL. Covers Discord, Slack, n8n, etc."""

    name = "webhook"

    def __init__(self, url: str, timeout: int = DEFAULT_TIMEOUT):
        if not url:
            raise ValueError("WebhookNotifier needs a url")
        self.url = url
        self.timeout = timeout

    def send(self, alert: Alert) -> bool:
        try:
            response = requests.post(
                self.url,
                json={
                    "symbol": alert.symbol,
                    "rule": alert.rule,
                    "price": alert.price,
                    "message": alert.message,
                },
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            print(f"! webhook: request failed: {exc}")
            return False

        if response.status_code >= 300:
            print(f"! webhook: HTTP {response.status_code}")
            return False
        return True


NOTIFIERS: Dict[str, type] = {
    ConsoleNotifier.name: ConsoleNotifier,
    TelegramNotifier.name: TelegramNotifier,
    WebhookNotifier.name: WebhookNotifier,
}


def build_notifier(spec: dict) -> Notifier:
    """
    Build a notifier from a config fragment such as:
        {"type": "console"}
        {"type": "telegram", "bot_token": "...", "chat_id": "..."}
        {"type": "webhook", "url": "https://..."}
    """
    spec = dict(spec)
    notifier_type = spec.pop("type", None)
    if notifier_type is None:
        raise ValueError("Notifier is missing required field 'type'")

    try:
        notifier_class = NOTIFIERS[notifier_type]
    except KeyError:
        known = ", ".join(sorted(NOTIFIERS))
        raise ValueError(
            f"Unknown notifier '{notifier_type}'. Available: {known}"
        ) from None

    try:
        return notifier_class(**spec)
    except TypeError as exc:
        raise ValueError(
            f"Bad parameters for notifier '{notifier_type}': {exc}"
        ) from None
