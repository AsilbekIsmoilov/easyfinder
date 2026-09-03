from __future__ import annotations

from datetime import date, timedelta
import hashlib
import hmac
import mimetypes
from time import perf_counter
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from sqlalchemy import case, delete, func, literal, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects.mysql import insert as mysql_insert, match as mysql_match
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .auth import current_user, require_telegram_user
from .analytics import analytics_summary, record_activity, touch_user
from .notifications import handle_bot_update, subscribe, webhook_secret
from .db import (
    RawPost, SessionLocal, Tour, TourComment, TourFeedback, TourLike, TourView, UserActivity, UserPreference,
    cleanup_expired_tours, init_db,
)
from .telegram_comments import send_source_comment
from .services import cache_get, cache_set, rate_allowed, redis_client
from .config import settings

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
MEDIA_DIR = Path(__file__).resolve().parents[1] / "media"
FILTER_CACHE_TTL = settings.cache_ttl_seconds

def cors_origins() -> list[str]:
    """API ni faqat o'z domenimiz va Telegram web mijozi chaqira olsin.

    Ochiq `*` bilan istalgan sayt foydalanuvchi brauzeridan API ga so'rov
    yubora olardi. TELEGRAM_WEBAPP_URL sozlanmagan bo'lsa (lokal ishlab
    chiqish) cheklov qo'llanmaydi.
    """
    configured = (settings.telegram_webapp_url or "").strip().rstrip("/")
    if not configured:
        return ["*"]
    return [configured, "https://web.telegram.org"]


app = FastAPI(title="EasyFinder API")
admin_key_header = APIKeyHeader(name="X-Admin-Key", scheme_name="AdminKey", auto_error=False)
app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=5)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)


@app.middleware("http")
async def process_time_header(request: Request, call_next):
    started = perf_counter()
    response = await call_next(request)
    response.headers["X-Process-Time-Ms"] = f"{(perf_counter() - started) * 1000:.2f}"
    return response


# Monitoring xizmatlari odatda HEAD yuboradi. Faqat GET ro'yxatdan o'tgan
# bo'lsa, HEAD statik fayllar mount'iga tushib 404 qaytaradi va sayt "o'lik"
# ko'rinadi.
@app.api_route("/api/health", methods=["GET", "HEAD"])
def health() -> dict:
    database_ok = False
    try:
        with SessionLocal() as db:
            db.execute(select(literal(1)))
            database_ok = True
    except Exception:
        pass
    redis_ok = bool(redis_client())
    if not database_ok:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return {"status": "ok", "database": database_ok, "redis": redis_ok}

def cutoff_date() -> str:
    return (date.today() + timedelta(days=4)).isoformat()

def strict_tour_conditions() -> list:
    """Mini App faqat uchta majburiy maydon aniq bo'lgan turlarni ko'rsatadi:
    yo'nalish (davlat yoki shahar), narx va ketish sanasi.

    Davomiylik ixtiyoriy — aniqlanmasa kartada bo'sh turadi. Qaytish sanasi,
    ovqatlanish va mehmonxona umuman ajratilmaydi.
    """
    return [
        Tour.country.is_not(None), Tour.country != "",
        Tour.price_amount.is_not(None), Tour.price_amount > 0,
        Tour.price_currency.is_not(None), Tour.price_currency != "",
        Tour.departure_date.is_not(None), Tour.departure_date != "",
        # Kanal bir turni qayta e'lon qilganda faqat eng yangisi ko'rsatiladi.
        Tour.is_duplicate.is_(False),
    ]

@app.on_event("startup")
def _startup() -> None:
    if settings.run_startup_migrations:
        init_db()
        cleanup_expired_tours(date.today().isoformat())


def _public_media_url(value: str | None) -> str | None:
    if value and settings.media_base_url and value.startswith("/media/"):
        return settings.media_base_url.rstrip("/") + value
    return value

def _serialize(t: Tour, original_text: str | None = None, stats: dict | None = None, comment_available: bool = False) -> dict:
    # Qaytish sanasi, ovqatlanish va mehmonxona chiqarilmaydi — ular endi
    # ajratilmaydi va UI da ham ko'rsatilmaydi.
    details = t.details or {}
    return {
        "id": t.id,
        "title": t.title,
        "country": t.country,
        "city": t.city,
        "price_amount": t.price_amount,
        "price_currency": t.price_currency,
        "departure_date": t.departure_date,
        "duration_days": t.duration_days,
        "includes": t.includes or [],
        "contact": t.contact,
        "summary": t.summary,
        "details": details,
        "source": t.source,
        "channel": t.channel,
        "url": t.url,
        "photo_url": _public_media_url(t.photo_url),
        "posted_at": t.posted_at.isoformat() if t.posted_at else None,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "original_text": original_text,
        "stats": stats or {"views": 0, "likes": 0, "comments": 0, "liked": False},
        "comment_available": bool(comment_available),
    }


class PreferenceInput(BaseModel):
    destination: str = Field(min_length=2, max_length=128)
    max_budget: float = Field(gt=0, le=1_000_000)
    travel_date: date


class SearchActivityInput(BaseModel):
    country: str | None = None
    min_budget: float | None = Field(default=None, ge=0)
    max_budget: float | None = Field(default=None, ge=0)


@app.post("/api/session")
def register_session(request: Request) -> dict:
    user = current_user(request)
    record_activity(user, "app_open")
    if user.key.startswith("tg:"):
        subscribe(user.key.removeprefix("tg:"), user.display_name, user.username)
    return {"registered": True, "notifications": user.key.startswith("tg:")}


@app.post("/api/telegram/webhook", include_in_schema=False)
def telegram_webhook(update: dict, request: Request) -> dict:
    supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not supplied or not hmac.compare_digest(supplied, webhook_secret()):
        raise HTTPException(status_code=403, detail="Invalid Telegram webhook secret")
    handle_bot_update(update)
    return {"ok": True}

@app.get("/api/preferences")
def get_preferences(request: Request) -> dict:
    user = current_user(request)
    with SessionLocal() as db:
        item = db.scalar(select(UserPreference).where(UserPreference.user_key == user.key))
    if not item:
        return {"completed": False}
    return {
        "completed": bool(item.completed), "destination": item.destination,
        "max_budget": item.max_budget, "travel_date": item.travel_date,
    }


@app.post("/api/preferences")
def save_preferences(payload: PreferenceInput, request: Request) -> dict:
    user = current_user(request)
    destination = payload.destination.strip()
    with SessionLocal() as db:
        item = db.scalar(select(UserPreference).where(UserPreference.user_key == user.key))
        if item:
            item.destination = destination
            item.max_budget = payload.max_budget
            item.travel_date = payload.travel_date.isoformat()
            item.completed = True
        else:
            db.add(UserPreference(
                user_key=user.key, destination=destination, max_budget=payload.max_budget,
                travel_date=payload.travel_date.isoformat(), completed=True,
            ))
        db.commit()
    record_activity(user, "onboarding", country=destination, max_budget=payload.max_budget)
    return {"completed": True, "destination": destination, "max_budget": payload.max_budget,
            "travel_date": payload.travel_date.isoformat()}

@app.delete("/api/preferences")
def delete_preferences(request: Request) -> dict:
    user = current_user(request)
    with SessionLocal() as db:
        deleted = db.execute(delete(UserPreference).where(UserPreference.user_key == user.key)).rowcount
        db.commit()
    return {"deleted": bool(deleted)}

@app.post("/api/activity/search")
def register_search(payload: SearchActivityInput, request: Request) -> dict:
    user = current_user(request)
    record_activity(
        user, "search", country=payload.country,
        min_budget=payload.min_budget, max_budget=payload.max_budget,
    )
    return {"registered": True}


@app.post("/api/activity/source-click/{tour_id}")
def register_source_click(tour_id: int, request: Request) -> dict:
    user = current_user(request)
    with SessionLocal() as db:
        tour = _require_tour(db, tour_id)
        source, channel = tour.source, tour.channel
    record_activity(user, "source_click", tour_id=tour_id, source=source, channel=channel)
    return {"registered": True}


def _require_admin(admin_key: str | None = Depends(admin_key_header)) -> str:
    if not settings.admin_job_key or admin_key != settings.admin_job_key:
        raise HTTPException(status_code=403, detail="Admin key noto'g'ri")
    return admin_key


@app.get("/api/admin/analytics", dependencies=[Depends(_require_admin)])
def admin_analytics() -> dict:
    return analytics_summary()

@app.get("/api/tours")
def list_tours(
    request: Request,
    q: str | None = Query(default=None, description="Tur, shahar, davlat yoki kanal"),
    country: list[str] | None = Query(default=None),
    source: list[str] | None = Query(default=None),
    channel: list[str] | None = Query(default=None),
    min_price: float | None = Query(default=None, ge=0),
    max_price: float | None = Query(default=None, ge=0),
    departure_from: date | None = None,
    departure_to: date | None = None,
    include_original: bool = False,
    limit: int = Query(default=100, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict:
    cache_seed = "&".join(f"{key}={value}" for key, value in sorted(request.query_params.multi_items()))
    cache_key = "tours:v4:" + hashlib.sha256(cache_seed.encode()).hexdigest()
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    conditions = [Tour.departure_date >= cutoff_date(), *strict_tour_conditions()]
    if q:
        term = q.strip()
        like = f"%{term}%"
        if SessionLocal.kw["bind"].dialect.name == "mysql" and len(term) >= 3:
            conditions.append(or_(
                mysql_match(Tour.title, Tour.country, Tour.city, Tour.summary, against=term).in_boolean_mode(),
                Tour.channel.ilike(like),
            ))
        else:
            conditions.append(or_(
                Tour.title.ilike(like), Tour.country.ilike(like), Tour.city.ilike(like),
                Tour.summary.ilike(like), Tour.channel.ilike(like),
            ))
    if country:
        conditions.append(Tour.country.in_(country))
    if source:
        conditions.append(Tour.source.in_(source))
    if channel:
        conditions.append(Tour.channel.in_(channel))
    if min_price is not None:
        conditions.extend((Tour.price_amount.is_not(None), Tour.price_amount >= min_price))
    if max_price is not None:
        conditions.extend((Tour.price_amount.is_not(None), Tour.price_amount <= max_price))
    if departure_from is not None:
        conditions.append(Tour.departure_date >= departure_from.isoformat())
    if departure_to is not None:
        conditions.append(Tour.departure_date <= departure_to.isoformat())

    order = (Tour.posted_at.is_(None), Tour.posted_at.desc(), Tour.id.desc())
    original_column = RawPost.text if include_original else literal(None).label("text")
    stmt = (
        select(Tour, original_column, RawPost.comment_available)
        .join(RawPost, RawPost.id == Tour.raw_post_id, isouter=True)
        .where(*conditions)
        .order_by(*order)
        .offset(offset)
        .limit(limit)
    )
    count_stmt = select(func.count()).select_from(Tour).where(*conditions)

    with SessionLocal() as db:
        rows = db.execute(stmt).all()
        total = db.scalar(count_stmt) or 0

    stats = _interaction_stats([tour.id for tour, _, _ in rows])
    data = {
        "items": [_serialize(tour, raw, stats.get(tour.id), available) for tour, raw, available in rows],
        "count": total,
        "limit_per_channel": None,
        "cutoff_date": cutoff_date(),
        "next_offset": offset + len(rows) if offset + len(rows) < total else None,
    }
    cache_set(cache_key, data, FILTER_CACHE_TTL)
    return data

@app.get("/api/hot-offers")
def hot_offers(limit: int = Query(default=10, ge=3, le=20)) -> dict:
    base = [Tour.departure_date >= cutoff_date(), *strict_tour_conditions()]
    family_terms = ("%oilaviy%", "%oila%", "%family%", "%семейн%", "%семья%", "%bolalar%", "%детей%", "%детск%")

    def fetch_group(db, extra_conditions: list, order_by: tuple) -> list[tuple]:
        return db.execute(
            select(Tour, RawPost.comment_available)
            .join(RawPost, RawPost.id == Tour.raw_post_id, isouter=True)
            .where(*base, *extra_conditions)
            .order_by(*order_by)
            .limit(limit)
        ).all()

    with SessionLocal() as db:
        cheap = fetch_group(db, [
            Tour.price_amount.is_not(None), Tour.price_amount >= 100,
            Tour.price_currency.in_(("USD", "EUR")),
        ], (Tour.price_amount.asc(), Tour.departure_date.asc()))
        soon = fetch_group(db, [], (Tour.departure_date.asc(), Tour.price_amount.asc()))
        family_condition = or_(*[
            or_(Tour.title.ilike(term), Tour.summary.ilike(term), RawPost.text.ilike(term))
            for term in family_terms
        ])
        family = fetch_group(db, [family_condition], (Tour.departure_date.asc(), Tour.price_amount.asc()))

    all_ids = list({tour.id for rows in (cheap, soon, family) for tour, _ in rows})
    stats = _interaction_stats(all_ids)

    def serialize_group(rows: list[tuple]) -> list[dict]:
        return [_serialize(tour, None, stats.get(tour.id), available) for tour, available in rows]

    return {
        "cheap": serialize_group(cheap),
        "soon": serialize_group(soon),
        "family": serialize_group(family),
    }

def _interaction_stats(tour_ids: list[int], user_key: str | None = None) -> dict[int, dict]:
    result = {tour_id: {"views": 0, "likes": 0, "comments": 0, "liked": False} for tour_id in tour_ids}
    if not tour_ids:
        return result

    view_counts = (
        select(TourView.tour_id, func.count(TourView.id).label("views"))
        .where(TourView.tour_id.in_(tour_ids)).group_by(TourView.tour_id).subquery()
    )
    like_counts = (
        select(
            TourLike.tour_id,
            func.count(TourLike.id).label("likes"),
            func.max(case((TourLike.user_key == user_key, 1), else_=0)).label("liked") if user_key else literal(0).label("liked"),
        )
        .where(TourLike.tour_id.in_(tour_ids)).group_by(TourLike.tour_id).subquery()
    )
    comment_counts = (
        select(TourComment.tour_id, func.count(TourComment.id).label("comments"))
        .where(TourComment.tour_id.in_(tour_ids)).group_by(TourComment.tour_id).subquery()
    )
    feedback_counts = (
        select(TourFeedback.tour_id, func.count(TourFeedback.id).label("feedback"))
        .where(TourFeedback.tour_id.in_(tour_ids)).group_by(TourFeedback.tour_id).subquery()
    )
    stmt = (
        select(
            Tour.id,
            func.coalesce(view_counts.c.views, 0),
            func.coalesce(like_counts.c.likes, 0),
            func.coalesce(comment_counts.c.comments, 0) + func.coalesce(feedback_counts.c.feedback, 0),
            func.coalesce(like_counts.c.liked, 0),
        )
        .outerjoin(view_counts, view_counts.c.tour_id == Tour.id)
        .outerjoin(like_counts, like_counts.c.tour_id == Tour.id)
        .outerjoin(comment_counts, comment_counts.c.tour_id == Tour.id)
        .outerjoin(feedback_counts, feedback_counts.c.tour_id == Tour.id)
        .where(Tour.id.in_(tour_ids))
    )
    with SessionLocal() as db:
        for tour_id, views, likes, comments, liked in db.execute(stmt):
            result[tour_id] = {
                "views": int(views), "likes": int(likes), "comments": int(comments),
                "liked": bool(liked),
            }
    return result

@app.get("/api/recommendations")
def recommendations(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """Onboarding, search va view tarixiga asoslangan explainable ranking."""
    user = current_user(request)
    with SessionLocal() as db:
        preference = db.scalar(select(UserPreference).where(
            UserPreference.user_key == user.key, UserPreference.completed.is_(True)
        ))
        if not preference:
            return {"items": [], "count": 0, "next_offset": None, "onboarding_required": True}

        rows = db.execute(
            select(Tour, RawPost.text, RawPost.comment_available)
            .join(RawPost, RawPost.id == Tour.raw_post_id, isouter=True)
            .where(Tour.departure_date >= cutoff_date())
            .limit(600)
        ).all()
        search_signals = dict(db.execute(
            select(UserActivity.country, func.count())
            .where(UserActivity.user_key == user.key, UserActivity.event_type == "search",
                   UserActivity.country.is_not(None))
            .group_by(UserActivity.country)
        ).all())
        view_signals = dict(db.execute(
            select(Tour.country, func.count())
            .join(TourView, TourView.tour_id == Tour.id)
            .where(TourView.viewer_key == user.key, Tour.country.is_not(None))
            .group_by(Tour.country)
        ).all())
        viewed_ids = set(db.scalars(select(TourView.tour_id).where(TourView.viewer_key == user.key)).all())

    desired = preference.destination.casefold()
    desired_date = date.fromisoformat(preference.travel_date)
    ranked = []
    for tour, original, available in rows:
        score = 0.0
        reasons = []
        destination_match = False
        budget_match = False
        date_distance = 10_000
        searchable = " ".join(filter(None, (tour.country, tour.city, tour.title, tour.summary))).casefold()
        if desired in searchable:
            destination_match = True
            score += 45
            reasons.append("destination")
        elif any(len(part) >= 3 and part in searchable for part in desired.replace(",", " ").split()):
            destination_match = True
            score += 24
            reasons.append("destination_related")

        if tour.price_amount is not None and tour.price_currency in {"USD", "EUR"}:
            ratio = tour.price_amount / max(preference.max_budget, 1)
            if ratio <= 1:
                budget_match = True
                score += 25 - abs(1 - ratio) * 8
                reasons.append("budget")
            elif ratio <= 1.15:
                score += 8
                reasons.append("near_budget")

        try:
            departure = date.fromisoformat(tour.departure_date or "")
            date_distance = abs((departure - desired_date).days)
            score += max(0, 24 - date_distance * 0.8)
            if date_distance <= 45:
                reasons.append("date")
        except ValueError:
            pass

        country = tour.country or ""
        history_weight = int(search_signals.get(country, 0)) * 3 + int(view_signals.get(country, 0)) * 2
        if history_weight:
            score += min(history_weight, 18)
            reasons.append("history")
        if tour.id in viewed_ids:
            score -= 3
        if tour.photo_url:
            score += 2
        ranked.append((score, tour.posted_at or tour.created_at, tour, original, available, reasons,
                       destination_match, budget_match, date_distance))

    # Uchta javob ketma-ket amaliy filter bo'ladi. Bir bosqich nol natija bersa,
    # oldingi muvaffaqiyatli bosqich saqlanadi va foydalanuvchi bo'sh ekran ko'rmaydi.
    destination_rows = [row for row in ranked if row[6]]
    if destination_rows:
        ranked = destination_rows
    budget_rows = [row for row in ranked if row[7]]
    if budget_rows:
        ranked = budget_rows
    date_rows = [row for row in ranked if row[8] <= 45]
    if date_rows:
        ranked = date_rows
    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
    page = ranked[offset:offset + limit]
    stats = _interaction_stats([row[2].id for row in page], user.key)
    items = []
    for score, _, tour, original, available, reasons, _, _, _ in page:
        item = _serialize(tour, original, stats.get(tour.id), available)
        item["recommendation"] = {"score": round(score, 2), "reasons": reasons}
        items.append(item)
    return {
        "items": items, "count": len(ranked), "onboarding_required": False,
        "next_offset": offset + len(items) if offset + len(items) < len(ranked) else None,
    }

def _require_tour(db, tour_id: int) -> Tour:
    tour = db.get(Tour, tour_id)
    if not tour:
        raise HTTPException(status_code=404, detail="Tur topilmadi")
    return tour


class CommentInput(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    reply_to_id: int | None = Field(default=None, ge=1)


class ViewBatch(BaseModel):
    tour_ids: list[int] = Field(min_length=1, max_length=50)


def _enforce_rate(user_key: str, scope: str, limit: int, window: int = 60) -> None:
    if not rate_allowed(f"{scope}:{user_key}", limit, window):
        raise HTTPException(status_code=429, detail="Juda ko'p so'rov. Birozdan keyin qayta urinib ko'ring.")


@app.get("/api/interactions")
def interaction_status(request: Request, tour_ids: str = Query(min_length=1, max_length=800)) -> dict:
    user = current_user(request)
    try:
        ids = list(dict.fromkeys(int(value) for value in tour_ids.split(",") if value))[:100]
    except ValueError:
        raise HTTPException(status_code=422, detail="tour_ids noto'g'ri")
    return {"items": _interaction_stats(ids, user.key)}


@app.post("/api/views/batch")
def register_views_batch(payload: ViewBatch, request: Request) -> dict:
    user = current_user(request)
    _enforce_rate(user.key, "views", 20)
    ids = list(dict.fromkeys(payload.tour_ids))[:50]
    with SessionLocal() as db:
        valid_ids = set(db.scalars(select(Tour.id).where(Tour.id.in_(ids))).all())
        values = [{"tour_id": tour_id, "viewer_key": user.key} for tour_id in valid_ids]
        if values:
            dialect = db.bind.dialect.name
            if dialect == "mysql":
                db.execute(mysql_insert(TourView).values(values).prefix_with("IGNORE"))
            elif dialect == "sqlite":
                db.execute(sqlite_insert(TourView).values(values).on_conflict_do_nothing(index_elements=["tour_id", "viewer_key"]))
            else:
                for value in values:
                    db.add(TourView(**value))
            db.commit()
    return {"accepted": len(valid_ids)}

@app.get("/api/tours/{tour_id}")
def get_tour(tour_id: int, request: Request) -> dict:
    user = current_user(request)
    with SessionLocal() as db:
        row = db.execute(
            select(Tour, RawPost.text, RawPost.comment_available)
            .join(RawPost, RawPost.id == Tour.raw_post_id, isouter=True)
            .where(Tour.id == tour_id)
        ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Tur topilmadi")
    return _serialize(row[0], row[1], _interaction_stats([tour_id], user.key)[tour_id], row[2])


@app.post("/api/tours/{tour_id}/view")
def register_view(tour_id: int, request: Request) -> dict:
    user = current_user(request)
    _enforce_rate(user.key, "view-single", 60)
    with SessionLocal() as db:
        _require_tour(db, tour_id)
        db.add(TourView(tour_id=tour_id, viewer_key=user.key))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
    return _interaction_stats([tour_id], user.key)[tour_id]


@app.post("/api/tours/{tour_id}/like")
def toggle_like(tour_id: int, request: Request) -> dict:
    user = require_telegram_user(request)
    _enforce_rate(user.key, "like", 30)
    with SessionLocal() as db:
        _require_tour(db, tour_id)
        existing = db.scalar(select(TourLike).where(TourLike.tour_id == tour_id, TourLike.user_key == user.key))
        if existing:
            db.delete(existing)
        else:
            db.add(TourLike(tour_id=tour_id, user_key=user.key))
        db.commit()
    return _interaction_stats([tour_id], user.key)[tour_id]


@app.get("/api/tours/{tour_id}/comments")
def list_comments(tour_id: int, request: Request) -> dict:
    current_user(request)
    with SessionLocal() as db:
        tour = _require_tour(db, tour_id)
        raw = db.get(RawPost, tour.raw_post_id)
        comment_available = bool(raw and raw.comment_available)
        comments = db.scalars(
            select(TourComment).where(TourComment.tour_id == tour_id)
            .order_by(TourComment.created_at.asc()).limit(100)
        ).all()
    return {"comment_available": comment_available, "items": [
        {"id": item.id, "display_name": item.display_name, "username": item.username,
         "photo_url": item.photo_url, "profile_url": item.profile_url, "text": item.text,
         "delivery_status": item.delivery_status, "created_at": item.created_at.isoformat()}
        for item in comments
    ]}


@app.post("/api/tours/{tour_id}/comments")
async def create_comment(tour_id: int, payload: CommentInput, request: Request) -> dict:
    user = require_telegram_user(request)
    _enforce_rate(user.key, "comment", 10, 300)
    text_value = payload.text.strip()
    if not text_value:
        raise HTTPException(status_code=422, detail="Comment bo'sh bo'lmasligi kerak")
    with SessionLocal() as db:
        tour = _require_tour(db, tour_id)
        raw = db.get(RawPost, tour.raw_post_id)
        if not raw or not raw.comment_available:
            raise HTTPException(status_code=409, detail="Bu kanal postida comment yozish imkoni mavjud emas")
        item = TourComment(
            tour_id=tour_id, user_key=user.key, display_name=user.display_name,
            username=user.username, photo_url=user.photo_url, profile_url=user.profile_url,
            text=text_value, delivery_status="local_only",
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        comment_id = item.id
        source_id = raw.source_id if raw and raw.source == "telegram" else None

    external_id = None
    if source_id and ":" in source_id:
        channel, message_id = source_id.rsplit(":", 1)
        if message_id.isdigit():
            external_id = await send_source_comment(channel, int(message_id), user.display_name, text_value)
    if not external_id:
        with SessionLocal() as db:
            saved = db.get(TourComment, comment_id)
            if saved:
                db.delete(saved)
                db.commit()
        raise HTTPException(status_code=409, detail="Kanalda comment yuborish imkoni mavjud emas")
    with SessionLocal() as db:
        saved = db.get(TourComment, comment_id)
        saved.delivery_status = "sent"
        saved.external_message_id = external_id
        db.commit()
    stats = _interaction_stats([tour_id], user.key)[tour_id]
    return {
        "id": comment_id, "display_name": user.display_name, "username": user.username,
        "photo_url": user.photo_url, "profile_url": user.profile_url, "text": text_value,
        "delivery_status": "sent",
        "created_at": item.created_at.isoformat(), "stats": stats,
    }

@app.get("/api/tours/{tour_id}/feedback")
def list_feedback(tour_id: int, request: Request) -> dict:
    current_user(request)
    with SessionLocal() as db:
        _require_tour(db, tour_id)
        items = db.scalars(
            select(TourFeedback).where(TourFeedback.tour_id == tour_id)
            .order_by(TourFeedback.created_at.asc()).limit(100)
        ).all()
        item_map = {item.id: item for item in items}
    return {"items": [
        {"id": item.id, "display_name": item.display_name, "username": item.username,
         "photo_url": item.photo_url, "profile_url": item.profile_url, "text": item.text,
         "reply_to_id": item.reply_to_id,
         "reply_to_display_name": item_map[item.reply_to_id].display_name if item.reply_to_id in item_map else None,
         "reply_to_text": item_map[item.reply_to_id].text if item.reply_to_id in item_map else None,
         "created_at": item.created_at.isoformat()}
        for item in items
    ]}


@app.post("/api/tours/{tour_id}/feedback")
def create_feedback(tour_id: int, payload: CommentInput, request: Request) -> dict:
    user = require_telegram_user(request)
    _enforce_rate(user.key, "feedback", 10, 300)
    text_value = payload.text.strip()
    if not text_value:
        raise HTTPException(status_code=422, detail="Feedback bo'sh bo'lmasligi kerak")
    with SessionLocal() as db:
        _require_tour(db, tour_id)
        parent = None
        if payload.reply_to_id is not None:
            parent = db.get(TourFeedback, payload.reply_to_id)
            if not parent or parent.tour_id != tour_id:
                raise HTTPException(status_code=422, detail="Javob berilayotgan feedback topilmadi")
        item = TourFeedback(
            tour_id=tour_id, user_key=user.key, display_name=user.display_name,
            username=user.username, photo_url=user.photo_url, profile_url=user.profile_url,
            text=text_value, reply_to_id=parent.id if parent else None,
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        return {
            "id": item.id, "display_name": item.display_name, "username": item.username,
            "photo_url": item.photo_url, "profile_url": item.profile_url, "text": item.text,
            "reply_to_id": item.reply_to_id,
            "reply_to_display_name": parent.display_name if parent else None,
            "reply_to_text": parent.text if parent else None,
            "created_at": item.created_at.isoformat(),
            "stats": _interaction_stats([tour_id], user.key)[tour_id],
        }

@app.get("/api/filters")
def list_filters() -> dict:
    cached = cache_get("filters:v3:" + cutoff_date())
    if cached is not None:
        return cached
    cutoff = cutoff_date()
    with SessionLocal() as db:
        countries = db.scalars(
            select(Tour.country)
            .where(Tour.departure_date >= cutoff, *strict_tour_conditions())
            .distinct()
        ).all()
        sources = db.scalars(
            select(Tour.source).where(Tour.departure_date >= cutoff, *strict_tour_conditions()).distinct()
        ).all()
        channels = db.scalars(
            select(Tour.channel).where(Tour.departure_date >= cutoff, *strict_tour_conditions()).distinct()
        ).all()
    data = {
        "countries": sorted(countries),
        "sources": sorted(sources),
        "channels": sorted(channels, key=str.lower),
    }
    cache_set("filters:v3:" + cutoff, data, FILTER_CACHE_TTL)
    return data


@app.get("/api/countries")
def list_countries() -> list[str]:
    return list_filters()["countries"]


# Windows registrida .woff2 yo'q, shuning uchun StaticFiles uni text/plain qilib
# uzatadi. Shriftlar to'g'ri MIME bilan ketishi uchun turni qo'lda qo'shamiz.
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("font/woff", ".woff")

if MEDIA_DIR.exists():
    app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")

if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")