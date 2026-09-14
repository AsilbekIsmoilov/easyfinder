"""User activity saqlash va admin metrikalarini hisoblash."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select, union

from .auth import AppUser
from .config import settings
from .db import AppUserRecord, NotificationSubscriber, SessionLocal, Tour, TourView, UserActivity, utcnow


def touch_user(user: AppUser) -> None:
    now = utcnow()
    telegram_id = user.key.removeprefix("tg:") if user.key.startswith("tg:") else None
    with SessionLocal() as db:
        item = db.scalar(select(AppUserRecord).where(AppUserRecord.user_key == user.key))
        if item:
            item.display_name = user.display_name
            item.username = user.username
            item.photo_url = user.photo_url
            item.last_seen_at = now
            # Manba faqat birinchi marta yoziladi. Odam keyin boshqa reklama
            # havolasidan kirsa ham, uni olib kelgan birinchi manba qoladi.
            if user.source and not item.acquisition_source:
                item.acquisition_source = user.source
        else:
            db.add(AppUserRecord(
                user_key=user.key, telegram_id=telegram_id,
                display_name=user.display_name, username=user.username,
                photo_url=user.photo_url, acquisition_source=user.source,
                first_seen_at=now, last_seen_at=now,
            ))
        db.commit()


def set_acquisition_source(telegram_id: str, source: str, display_name: str, username: str | None) -> None:
    """`/start src_<nom>` yo'li: odam ilovani hali ochmagan, faqat botga yozgan.

    Yozuv bo'lmasa yaratiladi — keyin u menyu tugmasidan ilovani ochganda
    start_param bo'sh keladi va manba yo'qolgan bo'lardi. Bor bo'lsa faqat
    bo'sh manba to'ldiriladi.
    """
    now = utcnow()
    key = f"tg:{telegram_id}"
    with SessionLocal() as db:
        item = db.scalar(select(AppUserRecord).where(AppUserRecord.user_key == key))
        if item is None:
            db.add(AppUserRecord(
                user_key=key, telegram_id=telegram_id, display_name=display_name,
                username=username, acquisition_source=source, first_seen_at=now, last_seen_at=now,
            ))
        elif not item.acquisition_source:
            item.acquisition_source = source
        db.commit()


def record_activity(user: AppUser, event_type: str, **values) -> None:
    touch_user(user)
    with SessionLocal() as db:
        db.add(UserActivity(user_key=user.key, event_type=event_type, **values))
        db.commit()


def sources_summary(days: int = 7) -> dict:
    """Manbalar bo'yicha: jami foydalanuvchi va oxirgi N kunda kelganlar.

    Faqat Telegram orqali kelganlar (anonimlar kirmaydi). Manbasiz kelganlar
    "to'g'ridan-to'g'ri" qatorida — bot qidiruvidan, ulashilgan turdan yoki
    menyu tugmasidan kirganlar.
    """
    since = utcnow() - timedelta(days=days)
    with SessionLocal() as db:
        total = db.execute(
            select(AppUserRecord.acquisition_source, func.count())
            .where(AppUserRecord.telegram_id.is_not(None))
            .group_by(AppUserRecord.acquisition_source)
        ).all()
        recent = db.execute(
            select(AppUserRecord.acquisition_source, func.count())
            .where(AppUserRecord.telegram_id.is_not(None), AppUserRecord.first_seen_at >= since)
            .group_by(AppUserRecord.acquisition_source)
        ).all()
    recent_map = dict(recent)
    rows = [
        {"source": source, "total": count, "recent": recent_map.get(source, 0)}
        for source, count in total
    ]
    rows.sort(key=lambda row: (-row["total"], row["source"] or ""))
    return {"days": days, "rows": rows}


def _period_boundaries() -> tuple[datetime, datetime, datetime]:
    zone = ZoneInfo(settings.pipeline_timezone)
    now_local = datetime.now(zone)
    midnight_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    today = midnight_local.astimezone(timezone.utc).replace(tzinfo=None)
    return today, today - timedelta(days=6), today - timedelta(days=29)


def total_users() -> int:
    """Botning jami foydalanuvchilari — Telegram orqali kelgan noyob odamlar.

    Ilovani Telegram ichida ochganlar va botga /start bosganlar birga; bitta
    odam ikkala jadvalda bo'lsa Telegram ID bo'yicha bir marta sanaladi.

    Anonim (brauzerdan kirgan, `telegram_id` bo'sh) yozuvlar ATAYLAB kirmaydi.
    Ular bot foydalanuvchisi emas, ustiga kalit brauzerga bog'langan: bitta
    odam telefon, kompyuter va yashirin oynadan kirsa uchta yozuv bo'ladi va
    son sun'iy ko'tariladi.
    """
    # UNION (UNION ALL emas) takrorlarni bazaning o'zida olib tashlaydi —
    # Python'ga million qator emas, bitta raqam qaytadi.
    people = union(
        select(AppUserRecord.telegram_id).where(AppUserRecord.telegram_id.is_not(None)),
        select(NotificationSubscriber.chat_id),
    ).subquery("people")
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(people)) or 0


def analytics_summary() -> dict:
    today, week, month = _period_boundaries()
    budget_value = case(
        (
            (UserActivity.min_budget.is_not(None)) & (UserActivity.max_budget.is_not(None)),
            (UserActivity.min_budget + UserActivity.max_budget) / 2,
        ),
        else_=func.coalesce(UserActivity.max_budget, UserActivity.min_budget),
    )
    with SessionLocal() as db:
        total_users = db.scalar(select(func.count()).select_from(AppUserRecord)) or 0
        dau = db.scalar(select(func.count(func.distinct(UserActivity.user_key))).where(
            UserActivity.created_at >= today
        )) or 0
        wau = db.scalar(select(func.count(func.distinct(UserActivity.user_key))).where(
            UserActivity.created_at >= week
        )) or 0
        mau = db.scalar(select(func.count(func.distinct(UserActivity.user_key))).where(
            UserActivity.created_at >= month
        )) or 0
        countries = db.execute(
            select(UserActivity.country, func.count().label("searches"))
            .where(UserActivity.event_type == "search", UserActivity.country.is_not(None))
            .group_by(UserActivity.country).order_by(func.count().desc()).limit(10)
        ).all()
        average_budget = db.scalar(select(func.avg(budget_value)).where(
            UserActivity.event_type == "search",
            (UserActivity.min_budget.is_not(None)) | (UserActivity.max_budget.is_not(None)),
        ))
        viewed = db.execute(
            select(Tour.id, Tour.title, Tour.channel, func.count(TourView.id).label("views"))
            .join(TourView, TourView.tour_id == Tour.id)
            .group_by(Tour.id, Tour.title, Tour.channel)
            .order_by(func.count(TourView.id).desc()).limit(10)
        ).all()
        channel_views = db.execute(
            select(Tour.channel, func.count(TourView.id).label("views"))
            .join(TourView, TourView.tour_id == Tour.id)
            .group_by(Tour.channel).order_by(func.count(TourView.id).desc()).limit(20)
        ).all()
        sources = db.execute(
            select(UserActivity.source, func.count().label("clicks"))
            .where(UserActivity.event_type == "source_click", UserActivity.source.is_not(None))
            .group_by(UserActivity.source).order_by(func.count().desc())
        ).all()
        channels = db.execute(
            select(UserActivity.channel, func.count().label("clicks"))
            .where(UserActivity.event_type == "source_click", UserActivity.channel.is_not(None))
            .group_by(UserActivity.channel).order_by(func.count().desc()).limit(20)
        ).all()
    return {
        "total_users": total_users, "dau": dau, "wau": wau, "mau": mau,
        "top_searched_countries": [{"country": row[0], "searches": row[1]} for row in countries],
        "average_searched_budget": round(float(average_budget), 2) if average_budget else None,
        "most_viewed_tours": [
            {"tour_id": row[0], "title": row[1], "channel": row[2], "views": row[3]}
            for row in viewed
        ],
        "source_clicks": [{"source": row[0], "clicks": row[1]} for row in sources],
        "channel_views": [{"channel": row[0], "views": row[1]} for row in channel_views],
        "channel_clicks": [{"channel": row[0], "clicks": row[1]} for row in channels],
    }
