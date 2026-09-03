"""raw_posts -> extractor -> tours."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import NamedTuple

from sqlalchemy import delete, or_, select

from .claude_extractor import ClaudeUnavailableError
from .db import (
    RawPost, SessionLocal, Tour, TourComment, TourFeedback, TourLike, TourView,
    cleanup_expired_tours, init_db,
)
from .extractor import extract_many
from .services import cache_delete_pattern

log = logging.getLogger(__name__)


# Kuzatiladigan maydonlar: foydalanuvchi ko'radigan va o'zgarishi muhim bo'lganlar.
TRACKED_FIELDS = (
    "country", "city", "price_amount", "price_currency", "departure_date", "duration_days",
)


class TourChange(NamedTuple):
    """Mavjud turdagi aniq o'zgarish: qaysi maydon, nimadan nimaga."""

    tour_id: int
    route: str
    changes: dict[str, tuple]   # maydon -> (eski, yangi)
    url: str | None             # asl Telegram postining havolasi
    channel: str | None


class PipelineResult(NamedTuple):
    """Bitta pipeline yurishining natijasi."""

    seen: int                     # ko'rilgan postlar soni
    created: int                  # yozilgan/yangilangan tur qatorlari soni
    raw_post_ids: list[int]       # muvaffaqiyatli ishlangan post id lari
    unavailable: str | None       # Claude o'chgan bo'lsa sababi, aks holda None
    changed: list[TourChange]     # qiymati o'zgargan mavjud turlar
    added: int                    # yangi qo'shilgan tur qatorlari
    removed: int                  # olib tashlangan eskirgan variantlar


def mark_duplicates() -> int:
    """Bir xil turning takroriy nusxalarini belgilaydi.

    Kanal ko'pincha bitta turni bir necha hafta davomida qayta e'lon qiladi.
    Har post alohida `raw_post_id` bo'lgani uchun alohida tur yaratiladi va
    katalogda o'nlab bir xil karta paydo bo'ladi.

    Guruhlash kaliti: (KANAL, davlat, ketish sanasi, narx, valyuta). Har
    guruhda faqat ENG YANGI post qoldiriladi, qolganlari `is_duplicate=True`
    bo'ladi. O'chirilmaydi — eng yangisi eskirsa yoki post o'chirilsa,
    keyingi yurish yana eng yangisini tanlaydi.

    KANAL kalitda bo'lishi SHART. Takror faqat bitta kanal ichida hisoblanadi.
    Ikki agentlik bir xil turni sotishi odatiy hol — ular bitta ulgurji
    operatordan oladi. Kanal hisobga olinmasa, biror agentlikning taklifi
    raqobatchisi tufayli yashirinib qolardi: foydalanuvchi narxlarni
    taqqoslay olmaydi, kanal egasi esa o'z turini katalogda ko'rmaydi.

    Shahar esa ataylab kalitga KIRMAYDI: u har postda turlicha ajraladi
    ("Bali + Kuala-Lumpur + Singapur", "Bali + Kuala Lumpur", "Bali") va
    kalitga qo'shilsa bir xil tur yana bir necha guruhga bo'linib ketadi.

    Qaytaradi: belgilangan takrorlar soni.
    """
    with SessionLocal() as db:
        tours = db.scalars(select(Tour).order_by(Tour.id)).all()

        groups: dict[tuple, list[Tour]] = {}
        for tour in tours:
            key = (
                (tour.channel or "").casefold(),
                (tour.country or "").casefold(),
                tour.departure_date or "",
                float(tour.price_amount or 0),
                (tour.price_currency or "").casefold(),
            )
            groups.setdefault(key, []).append(tour)

        changed = 0
        for members in groups.values():
            if len(members) == 1:
                keeper, rest = members[0], []
            else:
                # Eng yangi post g'olib: posted_at, keyin id bo'yicha.
                members.sort(
                    key=lambda t: (t.posted_at or datetime.min, t.id), reverse=True
                )
                keeper, rest = members[0], members[1:]

            if keeper.is_duplicate:
                keeper.is_duplicate = False
                changed += 1
            for other in rest:
                if not other.is_duplicate:
                    other.is_duplicate = True
                    changed += 1

        if changed:
            db.commit()
        return changed


def process_pending(limit: int | None = 200) -> PipelineResult:
    """Qayta ishlanmagan postlarni Claude orqali o'tkazadi.

    Bitta raw postdan bir nechta mustaqil tur yaratilishi mumkin.

    Claude ishlamay qolsa (kredit tugashi, kalit xatosi, tarmoq) yurish
    darhol to'xtaydi: qolgan postlarni urinib ko'rishdan foyda yo'q, ular
    "retry" holatida qoladi va muammo hal bo'lgach qayta ishlanadi.
    """
    init_db()
    cleanup_expired_tours(date.today().isoformat())
    seen = 0
    created = 0
    added = 0
    removed = 0
    done_ids: list[int] = []
    changed: list[TourChange] = []
    unavailable: str | None = None

    with SessionLocal() as db:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        query = (
            select(RawPost)
            .where(
                RawPost.processed.is_(False),
                or_(RawPost.next_retry_at.is_(None), RawPost.next_retry_at <= now),
            )
            .order_by(RawPost.posted_at.is_(None), RawPost.posted_at.desc(), RawPost.id.desc())
        )
        if limit is not None:
            query = query.limit(limit)
        posts = db.scalars(query).all()

        for post in posts:
            seen += 1
            post.processing_status = "processing"
            try:
                results = extract_many(post.text, post.posted_at.date() if post.posted_at else None)
            except ClaudeUnavailableError as exc:
                # Infratuzilma muammosi — postni aybdor qilmaymiz va butun
                # yurishni to'xtatamiz, aks holda qolgan postlar ham bekorga
                # xatoga uchraydi.
                seen -= 1
                post.processed = False
                post.processing_status = "pending"
                post.last_error = str(exc)[:1000]
                post.next_retry_at = None
                db.commit()
                unavailable = str(exc)
                log.error("Claude ishlamayapti, yurish to'xtatildi: %s", exc)
                break
            except Exception as exc:
                post.attempt_count += 1
                post.processed = False
                post.processing_status = "retry"
                post.last_error = str(exc)[:1000]
                post.next_retry_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=5)
                db.commit()
                log.exception("post %s parser xatosi; 5 daqiqadan keyin retry", post.id)
                continue

            post.attempt_count += 1
            post.processed = True
            review_errors = sorted({error for result in results for error in result.validation_errors})
            if not results:
                review_errors = ["unparsed_critical_fields"]
            post.processing_status = "needs_review" if review_errors else "completed"
            post.last_error = ", ".join(review_errors)[:1000] if review_errors else None
            post.next_retry_at = None
            cutoff = date.today() + timedelta(days=4)
            valid_results = []
            for result in results:
                try:
                    departure = date.fromisoformat(result.departure_date or "")
                except ValueError:
                    departure = None
                if result.publishable and departure is not None and departure >= cutoff:
                    valid_results.append(result)

            existing = db.scalars(
                select(Tour).where(Tour.raw_post_id == post.id).order_by(Tour.id)
            ).all()
            unused = list(existing)

            def result_key(value) -> tuple:
                return (
                    value.country or "", value.city or "", value.departure_date or "",
                    float(value.price_amount or 0), value.price_currency or "",
                )

            existing_by_key = {result_key(value): value for value in existing}
            for result in valid_results:
                target = existing_by_key.get(result_key(result))
                if target in unused:
                    unused.remove(target)
                elif unused:
                    # Eski yagona kartaning ID/statistikasini birinchi yangi variantga saqlab qoladi.
                    target = unused.pop(0)
                else:
                    target = Tour(raw_post_id=post.id)
                    db.add(target)

                # Mavjud qatorning eski qiymatlari — yangisi bilan solishtirish uchun.
                before = (
                    {field: getattr(target, field) for field in TRACKED_FIELDS}
                    if target.id is not None else None
                )

                target.source = post.source
                target.channel = post.channel
                target.url = post.url
                target.photo_url = post.photo_url
                target.title = result.title or (result.city or result.country or "Tur")
                target.country = result.country
                target.city = result.city
                target.price_amount = result.price_amount
                target.price_currency = result.price_currency
                target.departure_date = result.departure_date
                target.return_date = None
                target.duration_days = result.duration_days
                target.includes = result.includes
                target.contact = result.contact
                target.summary = result.summary
                target.details = {
                    "departure_city": result.departure_city,
                    "booking_note": result.booking_note,
                }
                target.posted_at = post.posted_at
                created += 1

                if before is None:
                    added += 1
                else:
                    diff = {
                        field: (before[field], getattr(target, field))
                        for field in TRACKED_FIELDS
                        if before[field] != getattr(target, field)
                    }
                    if diff:
                        route = " · ".join(filter(None, (target.country, target.city)))
                        changed.append(TourChange(
                            tour_id=target.id,
                            route=route or "Tur",
                            changes=diff,
                            url=target.url,
                            channel=target.channel,
                        ))

            stale_ids = [tour.id for tour in unused]
            removed += len(stale_ids)
            if stale_ids:
                db.execute(delete(TourView).where(TourView.tour_id.in_(stale_ids)))
                db.execute(delete(TourLike).where(TourLike.tour_id.in_(stale_ids)))
                db.execute(delete(TourComment).where(TourComment.tour_id.in_(stale_ids)))
                db.execute(delete(TourFeedback).where(TourFeedback.tour_id.in_(stale_ids)))
                db.execute(delete(Tour).where(Tour.id.in_(stale_ids)))
            db.commit()
            done_ids.append(post.id)

    marked = mark_duplicates()
    if marked:
        log.info("takrorlar qayta hisoblandi: %d o'zgarish", marked)

    cache_delete_pattern("tours:*")
    cache_delete_pattern("filters:*")
    log.info(
        "qayta ishlandi: %d post, %d tur (yangi %d, o'zgargan %d, olib tashlangan %d)",
        seen, created, added, len(changed), removed,
    )
    return PipelineResult(
        seen=seen, created=created, raw_post_ids=done_ids, unavailable=unavailable,
        changed=changed, added=added, removed=removed,
    )

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    result = process_pending()
    print(f"{result.seen} ta post ko'rildi, {result.created} ta tur saqlandi.")
    if result.unavailable:
        print(f"To'xtatildi: {result.unavailable}")
