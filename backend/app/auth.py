from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl

from fastapi import HTTPException, Request

from .config import settings


@dataclass(frozen=True)
class AppUser:
    key: str
    display_name: str
    username: str | None = None
    photo_url: str | None = None
    profile_url: str | None = None


def current_user(request: Request) -> AppUser:
    """O'qish va yengil amallar uchun: Telegram yoki anonim viewer.

    Anonim kalit brauzerdan keladi va uni soxtalashtirish mumkin. Shuning
    uchun u faqat ko'rish, statistika va shaxsiy sozlamalar uchun yetarli.
    Kontent yaratadigan amallarda `require_telegram_user` ishlatiladi.
    """
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    if init_data:
        return _telegram_user(init_data)

    anonymous = request.headers.get("X-Viewer-ID", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{12,80}", anonymous):
        raise HTTPException(status_code=401, detail="Telegram initData yoki viewer ID kerak")
    return AppUser(key=f"anon:{anonymous}", display_name="Anonim")


def require_telegram_user(request: Request) -> AppUser:
    """Faqat imzolangan Telegram foydalanuvchisi.

    Like, comment va feedback shu yerdan o'tadi. Anonim kalit brauzer
    tomonidan yaratiladi va uni istalgan odam o'zgartira oladi — bunday
    kalit bilan soxta layk qo'yish yoki boshqa nom ostida kanalga comment
    yozish mumkin bo'lardi.
    """
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    if not init_data:
        raise HTTPException(
            status_code=401,
            detail="Bu amal uchun Telegram orqali kirish kerak",
        )
    return _telegram_user(init_data)


def _telegram_user(init_data: str) -> AppUser:
    if not settings.telegram_bot_token:
        raise HTTPException(status_code=503, detail="Bot token sozlanmagan")
    fields = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = fields.pop("hash", "")
    data_check = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(b"WebAppData", settings.telegram_bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    if not received_hash or not hmac.compare_digest(expected, received_hash):
        raise HTTPException(status_code=401, detail="Telegram initData noto'g'ri")
    try:
        auth_date = int(fields.get("auth_date", "0"))
        user = json.loads(fields["user"])
    except (ValueError, KeyError, json.JSONDecodeError):
        raise HTTPException(status_code=401, detail="Telegram foydalanuvchi ma'lumoti noto'g'ri")
    if abs(time.time() - auth_date) > 86400:
        raise HTTPException(status_code=401, detail="Telegram initData eskirgan")
    first = str(user.get("first_name", "")).strip()
    last = str(user.get("last_name", "")).strip()
    username = str(user.get("username", "")).strip()
    name = " ".join(value for value in (first, last) if value) or (f"@{username}" if username else "Telegram user")
    user_id = int(user["id"])
    photo_url = str(user.get("photo_url", "")).strip() or None
    profile_url = f"https://t.me/{username}" if username else f"tg://user?id={user_id}"
    return AppUser(
        key=f"tg:{user_id}", display_name=name[:128], username=username or None,
        photo_url=photo_url, profile_url=profile_url,
    )