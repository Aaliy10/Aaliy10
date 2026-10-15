"""Phone alerts via a Telegram bot.

Alerts must never interrupt trading, so sending failures are logged, not raised.
"""
from __future__ import annotations

import logging
import os

import requests

log = logging.getLogger("aubot")

TELEGRAM_API = "https://api.telegram.org"
MAX_MESSAGE = 4096  # Telegram's limit per message


class NullNotifier:
    """Used when Telegram is not configured."""

    def send(self, text: str) -> bool:
        return False


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, session: requests.Session | None = None,
                 timeout: float = 10.0, api_url: str = TELEGRAM_API):
        self.token = token
        self.chat_id = chat_id
        self.session = session or requests.Session()
        self.timeout = timeout
        self.api_url = api_url

    @classmethod
    def from_env(cls) -> "TelegramNotifier | None":
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        return cls(token, chat_id) if token and chat_id else None

    def send(self, text: str) -> bool:
        """Send a message; returns False (and logs a warning) if it could not be delivered."""
        try:
            resp = self.session.post(
                f"{self.api_url}/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": text[:MAX_MESSAGE],
                      "disable_web_page_preview": True},
                timeout=self.timeout,
            )
            body = resp.json()
            if not body.get("ok"):
                log.warning("Telegram alert not sent: %s", body.get("description", resp.status_code))
                return False
            return True
        except Exception as exc:
            # request errors include the URL, which contains the bot token
            log.warning("Telegram alert not sent: %s", str(exc).replace(self.token, "***"))
            return False


def find_chat_ids(token: str, session: requests.Session | None = None,
                  api_url: str = TELEGRAM_API) -> list[tuple[str, str]]:
    """(chat id, name) for each chat that has recently messaged the bot."""
    session = session or requests.Session()
    try:
        resp = session.get(f"{api_url}/bot{token}/getUpdates", timeout=10.0)
        body = resp.json()
    except Exception as exc:
        raise RuntimeError(f"Could not reach Telegram: {str(exc).replace(token, '***')}") from None
    if not body.get("ok"):
        raise RuntimeError(f"Telegram rejected the bot token: {body.get('description', resp.status_code)}")
    chats: dict[str, str] = {}
    for update in body.get("result", []):
        message = update.get("message") or update.get("channel_post") or {}
        chat = message.get("chat")
        if chat:
            name = chat.get("title") or chat.get("first_name") or chat.get("username") or ""
            chats[str(chat["id"])] = name
    return list(chats.items())
