"""Inline rejim: @izyfinderbot <so'rov> — turni istalgan chatda ulashish.

Foydalanuvchi oilaviy yoki do'stlar chatida `@izyfinderbot antalya` deb yozadi
va tur kartasini o'sha yerda ulashadi. Har ulashish bot nomi bilan ketadi,
ya'ni bu tabiiy tarqalish kanali.

Telegram cheklovlari, e'tiborga olingan:
  - inline natijada `web_app` tugmasi ishlamaydi, faqat `url` bo'ladi
  - rasm URL'i ochiq HTTPS bo'lishi shart (nisbiy yo'l ishlamaydi)
  - bir so'rovga ko'pi bilan 50 ta natija
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy import or_, select

from .bot_setup import bot_api
from .config import settings
from .db import SessionLocal, Tour

log = logging.getLogger(__name__)

MAX_RESULTS = 30
CACHE_SECONDS = 60


def _bot_username() -> str:
    """Deep link uchun bot username'i. Bir marta olinib keshlanadi."""
    global _CACHED_USERNAME
    if _CACHED_USERNAME is None:
        try:
            _CACHED_USERNAME = bot_api("getMe")["username"]
        except Exception:
            log.exception("getMe ishlamadi, deep link tuzilmaydi")
            _CACHED_USERNAME = ""
    return _CACHED_USERNAME


_CACHED_USERNAME: str | None = None


def _public_url(path: str | None) -> str | None:
    """Nisbiy media yo'lini ochiq HTTPS manzilga aylantiradi."""
    if not path:
        return None
    if path.startswith("http"):
        return path
    base = (settings.media_base_url or settings.telegram_webapp_url or "").rstrip("/")
    if not base.startswith("https://"):
        return None
    return base + path


def _money(amount: float | None, currency: str | None) -> str:
    if not amount or not currency:
        return "narx ko'rsatilmagan"
    symbol = {"USD": "$", "EUR": "€"}.get(currency, currency + " ")
    return f"{symbol}{amount:,.0f}".replace(",", " ")


def _search(query: str) -> list[Tour]:
    """Faol turlar orasidan qidiradi. Bo'sh so'rovda eng arzonlari chiqadi."""
    cutoff = (date.today() + timedelta(days=4)).isoformat()
    conditions = [
        Tour.departure_date >= cutoff,
        Tour.country.is_not(None), Tour.country != "",
        Tour.price_amount.is_not(None), Tour.price_amount > 0,
        Tour.price_currency.is_not(None), Tour.price_currency != "",
    ]

    term = query.strip()
    if term:
        like = f"%{term}%"
        conditions.append(or_(
            Tour.country.ilike(like),
            Tour.city.ilike(like),
            Tour.title.ilike(like),
        ))

    with SessionLocal() as db:
        return db.scalars(
            select(Tour)
            .where(*conditions)
            .order_by(Tour.price_amount.asc(), Tour.departure_date.asc())
            .limit(MAX_RESULTS)
        ).all()


def _result(tour: Tour) -> dict:
    route = " · ".join(filter(None, (tour.country, tour.city))) or "Tur"
    price = _money(tour.price_amount, tour.price_currency)
    days = f" · {tour.duration_days} kun" if tour.duration_days else ""

    lines = [
        f"✈️ <b>{route}</b>",
        "",
        f"💰 {price}",
        f"📅 Jo'nash: {tour.departure_date}{days}",
    ]
    if tour.channel:
        lines.append(f"📣 Manba: @{tour.channel}")
    if tour.url:
        lines.append(f"\n🔗 <a href=\"{tour.url}\">Asl e'lonni ochish</a>")
    message_text = "\n".join(lines)

    username = _bot_username()
    buttons = []
    if username:
        buttons.append([{
            "text": "🔎 Katalogni ochish",
            "url": f"https://t.me/{username}?startapp=tour_{tour.id}",
        }])

    item = {
        "type": "article",
        "id": str(tour.id),
        "title": f"{route} — {price}",
        "description": f"Jo'nash {tour.departure_date}{days}"
                       + (f" · @{tour.channel}" if tour.channel else ""),
        "input_message_content": {
            "message_text": message_text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
    }
    thumb = _public_url(tour.photo_url)
    if thumb:
        item["thumbnail_url"] = thumb
    if buttons:
        item["reply_markup"] = {"inline_keyboard": buttons}
    return item


def handle_inline_query(query: dict) -> None:
    """Telegram'dan kelgan inline_query ga javob qaytaradi."""
    query_id = query.get("id")
    if not query_id:
        return

    text = (query.get("query") or "").strip()
    try:
        tours = _search(text)
    except Exception:
        log.exception("inline qidiruv xatosi: %r", text)
        tours = []

    payload = {
        "inline_query_id": query_id,
        "results": [_result(t) for t in tours],
        "cache_time": CACHE_SECONDS,
        "is_personal": False,
    }

    # Natija bo'lmasa foydalanuvchiga botni ochish taklif qilinadi.
    if not tours:
        payload["button"] = {
            "text": "Hech narsa topilmadi — katalogni ochish",
            "start_parameter": "inline_empty",
        }

    try:
        bot_api("answerInlineQuery", payload)
    except Exception:
        log.exception("answerInlineQuery yuborilmadi (natija: %d)", len(tours))
