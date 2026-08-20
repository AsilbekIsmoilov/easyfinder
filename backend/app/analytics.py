"""User activity saqlash va admin metrikalarini hisoblash."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select

from .auth import AppUser
from .config import settings
from .db import AppUserRecord, SessionLocal, Tour, TourView, UserActivity, utcnow


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
        else:
            db.add(AppUserRecord(
                user_key=user.key, telegram_id=telegram_id,
                display_name=user.display_name, username=user.username,
                photo_url=user.photo_url, first_seen_at=now, last_seen_at=now,
            ))
        db.commit()


def record_activity(user: AppUser, event_type: str, **values) -> None:
    touch_user(user)
    with SessionLocal() as db:
        db.add(UserActivity(user_key=user.key, event_type=event_type, **values))
        db.commit()


def _period_boundaries() -> tuple[datetime, datetime, datetime]:
    zone = ZoneInfo(settings.pipeline_timezone)
    now_local = datetime.now(zone)
    midnight_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    today = midnight_local.astimezone(timezone.utc).replace(tzinfo=None)
    return today, today - timedelta(days=6), today - timedelta(days=29)


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
