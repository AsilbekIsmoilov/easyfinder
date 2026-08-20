"""Telegram bot uchun obuna va faol turlar statistik notificationlari."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from html import escape
import hashlib
import logging
import time

from sqlalchemy import select

from .bot_setup import bot_api
from .config import settings
from .db import NotificationSubscriber, SessionLocal, Tour  # noqa: F401  (Tour update hisobotida)

log = logging.getLogger(__name__)

FLAGS = {
    "Turkiya": "🇹🇷", "BAA": "🇦🇪", "Misr": "🇪🇬", "Xitoy": "🇨🇳",
    "Tailand": "🇹🇭", "Vetnam": "🇻🇳", "Gruziya": "🇬🇪", "Ozarbayjon": "🇦🇿",
    "Maldiv orollari": "🇲🇻", "Shri-Lanka": "🇱🇰", "Saudiya Arabistoni": "🇸🇦",
    "Indoneziya": "🇮🇩", "Qatar": "🇶🇦", "Gretsiya": "🇬🇷", "Kipr": "🇨🇾",
    "Fransiya": "🇫🇷", "Italiya": "🇮🇹", "Ispaniya": "🇪🇸", "Chexiya": "🇨🇿",
}


def webhook_secret() -> str:
    return hashlib.sha256(settings.telegram_bot_token.encode()).hexdigest()[:32]


def subscribe(chat_id: str, display_name: str, username: str | None = None) -> None:
    with SessionLocal() as db:
        item = db.scalar(select(NotificationSubscriber).where(NotificationSubscriber.chat_id == chat_id))
        if item:
            item.display_name = display_name[:128]
            item.username = username
            item.enabled = True
        else:
            db.add(NotificationSubscriber(
                chat_id=chat_id, display_name=display_name[:128],
                username=username, enabled=True,
            ))
        db.commit()


def _button() -> dict:
    if settings.telegram_webapp_url:
        return {"text": "✈️ Turlarni ko‘rish", "web_app": {"url": settings.telegram_webapp_url.rstrip("/")}}
    return {"text": "✈️ Turlarni ko‘rish", "url": "https://t.me/turbozorbot?startapp"}


def active_tour_statistics() -> dict:
    cutoff = (date.today() + timedelta(days=4)).isoformat()
    with SessionLocal() as db:
        tours = db.scalars(select(Tour).where(Tour.departure_date >= cutoff)).all()

    countries: dict[str, dict] = defaultdict(lambda: {"count": 0, "min_price": None})
    cheapest = None
    for tour in tours:
        names = [part.strip() for part in (tour.country or "").split("+") if part.strip()]
        for country in dict.fromkeys(names):
            countries[country]["count"] += 1
            if tour.price_currency in {"USD", "EUR"} and tour.price_amount is not None and tour.price_amount >= 100:
                current = countries[country]["min_price"]
                if current is None or tour.price_amount < current[0]:
                    countries[country]["min_price"] = (tour.price_amount, tour.price_currency)
        if tour.price_currency in {"USD", "EUR"} and tour.price_amount is not None and tour.price_amount >= 100:
            if cheapest is None or tour.price_amount < cheapest.price_amount:
                cheapest = tour
    ordered = sorted(countries.items(), key=lambda item: (-item[1]["count"], item[0]))
    return {"active": len(tours), "countries": ordered, "cheapest": cheapest}


def _money(amount: float, currency: str) -> str:
    shown = f"{amount:,.0f}" if float(amount).is_integer() else f"{amount:,.2f}"
    symbol = "$" if currency == "USD" else "€" if currency == "EUR" else currency
    return f"{symbol}{shown}"


def statistics_message(*, reason: str, scraped: int = 0, processed: int = 0, created: int = 0) -> str:
    stats = active_tour_statistics()
    if reason == "start":
        header = "👋 <b>TourFinder(Beta)’ga xush kelibsiz!</b>"
        intro = "Siz uchun Telegram kanallaridagi dolzarb sayohat takliflarini bir joyga jamladik."
    else:
        header = "✅ <b>TourFinder(Beta) yangilandi</b>"
        intro = f"Yangi postlar: <b>{scraped}</b> · Qayta ishlangan: <b>{processed}</b> · Yangilangan turlar: <b>{created}</b>"

    lines = [header, "", intro, "", f"📊 <b>Faol turlar: {stats['active']} ta</b>", "", "🌍 <b>Davlatlar bo‘yicha:</b>"]
    if stats["countries"]:
        for country, data in stats["countries"]:
            flag = FLAGS.get(country, "▫️")
            price = data["min_price"]
            price_text = f" · {_money(*price)} dan" if price else " · narx aniqlanmagan"
            lines.append(f"{flag} {escape(country)} — <b>{data['count']} ta</b>{price_text}")
    else:
        lines.append("Hozircha faol tur mavjud emas.")

    cheapest = stats["cheapest"]
    if cheapest:
        route = " · ".join(filter(None, (cheapest.country, cheapest.city)))
        lines.extend(["", "💸 <b>Eng arzon taklif:</b>", f"{escape(route)} — <b>{_money(cheapest.price_amount, cheapest.price_currency)}</b>"])
    lines.extend(["", "👇 Batafsil ma’lumot va bron qilish uchun Mini App’ni oching."])
    return "\n".join(lines)


def send_statistics(chat_id: str, *, reason: str, scraped: int = 0, processed: int = 0, created: int = 0) -> None:
    bot_api("sendMessage", {
        "chat_id": chat_id,
        "text": statistics_message(reason=reason, scraped=scraped, processed=processed, created=created),
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": {"inline_keyboard": [[_button()]]},
    })


def handle_bot_update(update: dict) -> None:
    message = update.get("message") or {}
    text = (message.get("text") or "").strip()
    if not text.startswith("/start"):
        return
    chat = message.get("chat") or {}
    sender = message.get("from") or {}
    chat_id = str(chat.get("id") or "")
    if not chat_id:
        return
    display_name = " ".join(filter(None, (sender.get("first_name"), sender.get("last_name")))) or "User"
    subscribe(chat_id, display_name, sender.get("username"))
    send_statistics(chat_id, reason="start")


def _admin_chats() -> list[str]:
    """Operativ xabarlar kimga ketadi.

    ADMIN_CHAT_ID sozlanmagan bo'lsa xabar yuborilmaydi — kredit tugagani
    yoki qaysi tur yangilangani oddiy foydalanuvchiga kerak emas.
    """
    raw = (settings.admin_chat_id or "").strip()
    return [part.strip() for part in raw.split(",") if part.strip()]


def notify_limit_reached(reason: str, *, stage: str, test: bool = False) -> None:
    """Claude ishlamay qolganda adminni ogohlantiradi."""
    chats = _admin_chats()
    if not chats:
        log.warning("ADMIN_CHAT_ID sozlanmagan — limit haqida xabar yuborilmadi: %s", reason)
        return

    label = "Create (20:00)" if stage == "create" else "Update (21:00)"
    short = escape(reason[:300])
    prefix = "🧪 <b>SINOV XABARI</b>\n\n" if test else ""
    text = prefix + (
        f"🛑 <b>{label} to'xtatildi</b>\n\n"
        f"Claude API javob bermadi, shuning uchun postlar tahlil qilinmadi.\n\n"
        f"<code>{short}</code>\n\n"
        f"Postlar navbatda qoldi — muammo hal bo'lgach avtomatik qayta ishlanadi. "
        f"Katalogga sifatsiz ma'lumot yozilmadi."
    )
    for chat_id in chats:
        try:
            bot_api("sendMessage", {
                "chat_id": chat_id, "text": text,
                "parse_mode": "HTML", "disable_web_page_preview": True,
            })
        except Exception:
            log.exception("limit ogohlantirishi yuborilmadi chat=%s", chat_id)


FIELD_LABELS = {
    "country": "davlat",
    "city": "shahar",
    "price_amount": "narx",
    "price_currency": "valyuta",
    "departure_date": "ketish sanasi",
    "duration_days": "davomiylik",
}


def _field_value(field: str, value) -> str:
    if value in (None, ""):
        return "—"
    if field == "duration_days":
        return f"{value} kun"
    if field == "price_amount":
        return f"{float(value):,.0f}".replace(",", " ")
    return str(value)


def notify_updated_tours(
    post_count: int,
    changed: list,
    *,
    added: int = 0,
    removed: int = 0,
    test: bool = False,
) -> None:
    """Tahrir natijasida nima o'zgarganini adminga yuboradi.

    `changed` — pipeline.TourChange ro'yxati: har birida qaysi maydon
    nimadan nimaga o'zgargani yozilgan.

    `test=True` bo'lsa xabar boshiga aniq belgi qo'yiladi. Sinov xabari
    haqiqiysidan farqlanmasa, chatda soxta hisobot qolib ketadi.
    """
    chats = _admin_chats()
    if not chats:
        log.warning("ADMIN_CHAT_ID sozlanmagan — update hisoboti yuborilmadi")
        return

    if not post_count:
        text = "🔄 <b>Update (21:00)</b>\n\nTahrirlangan post topilmadi."
    else:
        lines = [
            "🔄 <b>Update (21:00)</b>",
            "",
            f"Tahrirlangan post: <b>{post_count}</b>",
            f"O'zgargan tur: <b>{len(changed)}</b> · yangi: <b>{added}</b> · "
            f"olib tashlangan: <b>{removed}</b>",
        ]
        if changed:
            lines.append("")
            for item in changed[:25]:
                route = escape(item.route)
                url = getattr(item, "url", None)
                # Yo'nalish nomi asl Telegram postiga havola bo'ladi — bosib,
                # kanalda nima o'zgarganini o'z ko'zi bilan tekshirish uchun.
                title = f'<a href="{escape(url)}">{route}</a>' if url else route
                channel = getattr(item, "channel", None)
                suffix = f" <i>@{escape(channel)}</i>" if channel else ""
                lines.append(f"<b>{title}</b>{suffix}")
                for field, (old, new) in item.changes.items():
                    label = FIELD_LABELS.get(field, field)
                    lines.append(
                        f"   {label}: {escape(_field_value(field, old))} → "
                        f"<b>{escape(_field_value(field, new))}</b>"
                    )
                lines.append("")
            if len(changed) > 25:
                lines.append(f"… va yana {len(changed) - 25} ta tur")
        elif added or removed:
            lines.append("")
            lines.append("Mavjud turlarning qiymati o'zgarmadi, lekin variantlar soni o'zgardi.")
        else:
            lines.append("")
            lines.append(
                "Postlar o'zgargan, lekin katalogdagi turlarga ta'sir qilmadi "
                "(sana o'tgan yoki majburiy maydon yetishmayapti)."
            )
        text = "\n".join(lines)

    if test:
        text = (
            "🧪 <b>SINOV XABARI — qiymatlar soxta</b>\n"
            "<i>Bu haqiqiy o'zgarish emas, faqat format namunasi.</i>\n\n"
        ) + text

    for chat_id in chats:
        try:
            bot_api("sendMessage", {
                "chat_id": chat_id, "text": text,
                "parse_mode": "HTML", "disable_web_page_preview": True,
            })
        except Exception:
            log.exception("update hisoboti yuborilmadi chat=%s", chat_id)


def notify_pipeline(*, scraped: int, processed: int, created: int, failed: bool = False) -> tuple[int, int]:
    with SessionLocal() as db:
        subscribers = db.scalars(select(NotificationSubscriber).where(NotificationSubscriber.enabled.is_(True))).all()
    sent = failed_count = 0
    for index, subscriber in enumerate(subscribers):
        try:
            if failed:
                bot_api("sendMessage", {
                    "chat_id": subscriber.chat_id,
                    "text": "⚠️ <b>TourFinder(Beta) yangilanishida vaqtinchalik xatolik yuz berdi.</b>\n\nKeyingi urinish jadval bo‘yicha avtomatik bajariladi.",
                    "parse_mode": "HTML",
                })
            else:
                send_statistics(subscriber.chat_id, reason="pipeline", scraped=scraped, processed=processed, created=created)
            sent += 1
        except Exception as exc:
            failed_count += 1
            message = str(exc).lower()
            log.warning("notification yuborilmadi chat=%s: %s", subscriber.chat_id, exc)
            if any(value in message for value in ("blocked", "chat not found", "forbidden", "403")):
                with SessionLocal() as db:
                    saved = db.get(NotificationSubscriber, subscriber.id)
                    if saved:
                        saved.enabled = False
                        db.commit()
        if index and index % 25 == 0:
            time.sleep(1)
    log.info("notification sent=%s failed=%s", sent, failed_count)
    return sent, failed_count