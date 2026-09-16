"""Telegram botning Mini App menu tugmasini sozlash.

Ishlatish:
    python -m app.bot_setup https://example.com
"""

from __future__ import annotations

import argparse
import logging
import json
import hashlib
from urllib import error, request

from .config import settings

log = logging.getLogger(__name__)


def bot_api(method: str, payload: dict | None = None) -> dict:
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN .env faylida bo'sh")

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/{method}"
    data = json.dumps(payload or {}).encode("utf-8")
    req = request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with request.urlopen(req, timeout=20) as response:
            result = json.load(response)
    except error.HTTPError as exc:
        # Telegram xato sababini javob tanasida yozadi ("chat not found" kabi).
        # Uni o'qimasak, faqat "HTTP Error 400" qoladi va nima bo'lganini
        # topish uchun so'rovni qo'lda takrorlashga to'g'ri keladi.
        try:
            detail = json.load(exc).get("description", "")
        except Exception:
            detail = ""
        raise RuntimeError(f"{method}: {detail or exc}") from exc

    if not result.get("ok"):
        raise RuntimeError(result.get("description", "Telegram Bot API xatosi"))
    return result["result"]


_CACHED_USERNAME: str | None = None


def bot_username() -> str:
    """Botning @username'i — tokendan getMe orqali olinadi va keshlanadi.

    Qotirib yozilmaydi: bot almashtirilganda faqat TELEGRAM_BOT_TOKEN
    o'zgaradi, kod va havolalar o'zi to'g'rilanadi. Xato bo'lsa bo'sh satr
    qaytadi — chaqiruvchi havolasiz ishlashga tayyor bo'lishi kerak.
    """
    global _CACHED_USERNAME
    if _CACHED_USERNAME is None:
        try:
            _CACHED_USERNAME = bot_api("getMe")["username"]
        except Exception:
            log.exception("getMe ishlamadi — bot username aniqlanmadi")
            return ""
    return _CACHED_USERNAME


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
    # Buyruqlar Telegram'dagi "/" menyusida ko'rinadi.
    public_commands = [
        {"command": "start", "description": "Katalogni ochish"},
        {"command": "help", "description": "Bot nima qiladi"},
        {"command": "stop", "description": "Xabarlarni to'xtatish"},
    ]
    bot_api("setMyCommands", {"commands": public_commands})
    # Admin buyruqlari faqat admin chatida ko'rinadi — ommaviy menyuda kerak emas.
    admin_commands = public_commands + [
        {"command": "channels", "description": "Kanallar bo'yicha hisobot"},
        {"command": "sources", "description": "Manbalar bo'yicha foydalanuvchilar"},
        {"command": "add", "description": "Kanal qo'shish"},
        {"command": "block", "description": "Kanalni bloklash"},
        {"command": "all_users", "description": "Bazadagi hamma foydalanuvchi"},
    ]
    for chat_id in [part.strip() for part in settings.admin_chat_id.split(",") if part.strip()]:
        try:
            bot_api("setMyCommands", {
                "commands": admin_commands,
                "scope": {"type": "chat", "chat_id": chat_id},
            })
        except RuntimeError as exc:
            # Yangi botga admin hali /start bosmagan bo'lsa Telegram bu chatni
            # bilmaydi va "chat not found" qaytaradi. Bu sozlashni to'xtatmasin:
            # menyu tugmasi va webhook muhimroq, admin menyusi esa /start dan
            # keyin shu buyruqni qayta yurgizish bilan qo'yiladi.
            log.warning("admin menyusi qo'yilmadi (%s): %s", chat_id, exc)
    bot_api(
        "setChatMenuButton",
        {
            "menu_button": {
                "type": "web_app",
                "text": "Ilovani ochish",
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
    print(f"Tayyor: https://t.me/{username} -> Ilovani ochish")
