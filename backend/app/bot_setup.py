"""Telegram botning Mini App menu tugmasini sozlash.

Ishlatish:
    python -m app.bot_setup https://example.com
"""

from __future__ import annotations

import argparse
import json
import hashlib
from urllib import request

from .config import settings


def bot_api(method: str, payload: dict | None = None) -> dict:
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN .env faylida bo'sh")

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/{method}"
    data = json.dumps(payload or {}).encode("utf-8")
    req = request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with request.urlopen(req, timeout=20) as response:
        result = json.load(response)

    if not result.get("ok"):
        raise RuntimeError(result.get("description", "Telegram Bot API xatosi"))
    return result["result"]


def setup_menu(webapp_url: str) -> str:
    if not webapp_url.startswith("https://"):
        raise ValueError("Mini App URL HTTPS bo'lishi kerak")

    bot = bot_api("getMe")
    secret = hashlib.sha256(settings.telegram_bot_token.encode()).hexdigest()[:32]
    bot_api("setWebhook", {
        "url": webapp_url.rstrip("/") + "/api/telegram/webhook",
        "secret_token": secret,
        "allowed_updates": ["message", "inline_query"],
        "drop_pending_updates": False,
    })
    bot_api(
        "setChatMenuButton",
        {
            "menu_button": {
                "type": "web_app",
                "text": "Let's open !)",
                "web_app": {"url": webapp_url.rstrip("/")},
            }
        },
    )
    return bot["username"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("url", help="Mini App public HTTPS URL")
    args = parser.parse_args()
    username = setup_menu(args.url)
    print(f"Tayyor: https://t.me/{username} -> Let's open !)")
