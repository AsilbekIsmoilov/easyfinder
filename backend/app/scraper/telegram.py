"""Telegram kanallaridan postlarni yig'ib raw_posts jadvaliga yozadi.

Birinchi ishga tushirish (session string olish uchun):
    python -m app.scraper.telegram --login

Keyin oddiy scrape:
    python -m app.scraper.telegram
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import re
import time
from pathlib import Path
from datetime import date, timedelta

from sqlalchemy import select
from telethon import TelegramClient
from telethon.sessions import StringSession

from ..config import settings
from ..db import RawPost, SessionLocal, Tour, init_db

log = logging.getLogger(__name__)
MEDIA_DIR = Path(__file__).resolve().parents[2] / "media" / "telegram"


def _make_client() -> TelegramClient:
    return TelegramClient(
        StringSession(settings.telegram_session or None),
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )


async def login() -> None:
    """Interaktiv login — natijada .env ga qo'yiladigan session string chiqaradi.

    Mavjud TELEGRAM_SESSION ataylab e'tiborga olinmaydi. Aks holda buyruq eski
    akkauntga ulanib, aynan o'sha sessiyani qaytaradi va boshqa raqam bilan
    kirishning iloji bo'lmaydi.
    """
    client = TelegramClient(
        StringSession(),                 # bo'sh sessiya -> telefon raqami so'raladi
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )
    async with client:
        me = await client.get_me()
        print()
        print(f"Login OK: {me.first_name} (@{me.username})")
        print()
        print("Quyidagini .env ichidagi TELEGRAM_SESSION ga qo'ying:")
        print()
        print(client.session.save())
        print()
        print("Eslatma: bu yangi sessiya. Eskisini Telegram > Settings >")
        print("Devices dan bekor qilishni unutmang.")


async def _last_seen_id(db, channel: str) -> int:
    """Shu kanaldan oxirgi saqlangan post ID si (incremental scrape uchun)."""
    rows = db.scalars(
        select(RawPost.source_id).where(RawPost.source == "telegram", RawPost.channel == channel)
    ).all()
    ids = [int(r.rsplit(":", 1)[1]) for r in rows if ":" in r]
    return max(ids) if ids else 0


# Sana ko'rinishidagi tokenlar: 02.09.2026, 2/9, "15 сентября", "15 sentabr"
DATE_HINT = re.compile(
    r"(?<!\d)\d{1,2}\s*[./-]\s*\d{1,2}"
    r"|(?<!\d)\d{1,2}\s*(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр"
    r"|yanvar|fevral|mart|aprel|may|iyun|iyul|avgust|sentabr|oktabr|noyabr|dekabr)",
    re.I,
)


def content_hash(text: str) -> str:
    """Post matnining barqaror hash'i — tahrirni aniqlash uchun."""
    return hashlib.sha256((text or "").strip().encode("utf-8")).hexdigest()


def _worth_storing(text: str) -> bool:
    """Claude'ga yuborishdan oldingi juda yengil filtr.

    Faqat aniq imkonsiz postlarni tashlaydi: matnda umuman sana ko'rinishidagi
    token bo'lmasa, unda ketish sanasi bo'lgan tur ham bo'lolmaydi. Rule-based
    parserdan farqli o'laroq bu yerda sanani to'liq ajrata olish talab
    qilinmaydi — shubhali post saqlanadi va qarorni Claude qabul qiladi.
    """
    return bool(text) and len(text) >= 30 and bool(DATE_HINT.search(text))


async def scrape(full: bool = False) -> int:
    """Kanallardan postlarni yig'adi.

    full=True bo'lsa incremental min_id chegarasi qo'llanmaydi va butun tarix
    (SCRAPE_HISTORY_DAYS ichida) qayta ko'rib chiqiladi — bir marta to'liq
    backfill qilish uchun. Kundalik ishda full=False yetarli.
    """
    init_db()
    if not settings.channels:
        log.warning("TELEGRAM_CHANNELS bo'sh")
        return 0

    saved = 0
    images = 0
    started = time.perf_counter()
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    async with _make_client() as client:
        with SessionLocal() as db:
            for channel in settings.channels:
                channel_started = time.perf_counter()
                min_id = 0 if full else await _last_seen_id(db, channel)
                # To'liq backfillda eski postlar qayta uchraydi — unique
                # cheklovga urilmaslik uchun saqlanganlarini oldindan bilamiz.
                known_ids: set[str] = set()
                if full:
                    known_ids = set(
                        db.scalars(
                            select(RawPost.source_id).where(
                                RawPost.source == "telegram", RawPost.channel == channel
                            )
                        ).all()
                    )
                try:
                    entity = await client.get_entity(channel)
                except Exception as exc:  # kanal topilmadi / ruxsat yo'q
                    log.warning("kanal ochilmadi %s: %s", channel, exc)
                    continue

                count = 0
                channel_images = 0
                # Avval oynani to'liq o'qib olamiz. Sabab: kanallar albom
                # (media group) tashlaganda matn bitta xabarda, rasmlar esa
                # qo'shni matnsiz xabarlarda bo'ladi. Ularni oldindan bilmasak,
                # matnli post rasmsiz qolib ketadi.
                window = []
                async for msg in client.iter_messages(
                    entity, limit=settings.scrape_limit or None, min_id=min_id
                ):
                    if (
                        msg.date
                        and settings.scrape_history_days > 0
                        and msg.date.date() < date.today() - timedelta(days=settings.scrape_history_days)
                    ):
                        break
                    window.append(msg)

                # albom -> undagi birinchi rasmli xabar
                album_photo = {}
                for msg in window:
                    gid = getattr(msg, "grouped_id", None)
                    if gid and msg.photo and gid not in album_photo:
                        album_photo[gid] = msg

                for msg in window:
                    if not msg.message:
                        continue
                    if f"{channel}:{msg.id}" in known_ids:
                        continue
                    # Yagona prefiltr — yengil sana tekshiruvi. Tur bormi-yo'qmi
                    # degan qarorni Claude qabul qiladi, shuning uchun bu yerda
                    # hech qanday parser ishlatilmaydi.
                    if settings.scrape_prefilter and not _worth_storing(msg.message):
                        continue
                    # Rasm shu xabarda bo'lmasa, o'sha albomdagi rasmni olamiz.
                    source_msg = msg
                    if not msg.photo:
                        gid = getattr(msg, "grouped_id", None)
                        if gid and gid in album_photo:
                            source_msg = album_photo[gid]

                    photo_url = None
                    if source_msg.photo:
                        filename = f"{channel}_{source_msg.id}.jpg"
                        target = MEDIA_DIR / filename
                        try:
                            downloaded = await client.download_media(source_msg.photo, file=str(target))
                            if downloaded:
                                photo_url = f"/media/telegram/{Path(downloaded).name}"
                                channel_images += 1
                        except Exception as exc:
                            log.warning("rasm yuklanmadi %s:%s: %s", channel, source_msg.id, exc)

                    db.add(
                        RawPost(
                            source="telegram",
                            source_id=f"{channel}:{msg.id}",
                            channel=channel,
                            url=f"https://t.me/{channel}/{msg.id}",
                            text=msg.message,
                            photo_url=photo_url,
                            comment_available=bool(msg.replies and msg.replies.comments),
                            posted_at=msg.date.replace(tzinfo=None) if msg.date else None,
                            content_hash=content_hash(msg.message),
                            edit_date=msg.edit_date.replace(tzinfo=None) if msg.edit_date else None,
                        )
                    )
                    count += 1
                db.commit()
                saved += count
                images += channel_images
                log.info(
                    "%s: %d yangi post, %d rasm, %.2f soniya",
                    channel, count, channel_images, time.perf_counter() - channel_started,
                )
    log.info("jami: %d post, %d rasm, %.2f soniya", saved, images, time.perf_counter() - started)
    return saved


def _message_id(source_id: str) -> int | None:
    """'kanal:12345' dan Telegram message ID sini ajratadi."""
    _, _, tail = (source_id or "").rpartition(":")
    return int(tail) if tail.isdigit() else None


async def refresh(limit: int | None = None) -> list[int]:
    """Saqlangan postlarning tahrirlanganini aniqlaydi.

    Kanaldagi post tahrirlansa (masalan narx o'zgartirilsa) uning message ID si
    o'zgarmaydi, shuning uchun incremental scrape uni ko'rmaydi.

    Tekshiriladigan xabarlar ikki manbadan yig'iladi:
      1. katalogda FAOL turi bor hamma postlar — yoshidan qat'i nazar.
         Bot ko'rsatib turgan har bir tur nazoratda bo'lishi shart.
      2. oxirgi N ta xabar — hali turga aylanmagan post tahrirlanib,
         yaroqli holga kelishi mumkin.

    Yangi postlar bu yerda qo'shilmaydi — u `scrape()` ning ishi.

    Qaytaradi: matni o'zgargan va qayta tahlilga qo'yilgan raw_post id lari.
    """
    init_db()
    if not settings.channels:
        return []

    window = limit or settings.scrape_limit or 50
    active_cutoff = (date.today() + timedelta(days=4)).isoformat()
    changed: list[int] = []
    started = time.perf_counter()

    async with _make_client() as client:
        with SessionLocal() as db:
            for channel in settings.channels:
                stored = {
                    row.source_id: row
                    for row in db.scalars(
                        select(RawPost).where(
                            RawPost.source == "telegram", RawPost.channel == channel
                        )
                    ).all()
                }
                try:
                    entity = await client.get_entity(channel)
                except Exception as exc:
                    log.warning("kanal ochilmadi %s: %s", channel, exc)
                    continue

                channel_changed = 0
                seen_ids: set[int] = set()

                def apply(msg) -> None:
                    """Bitta xabarni bazadagisi bilan solishtiradi."""
                    nonlocal channel_changed
                    if not msg or not msg.message:
                        return
                    post = stored.get(f"{channel}:{msg.id}")
                    if post is None:
                        return  # yangi post — create bosqichida qo'shiladi
                    fresh = content_hash(msg.message)
                    edited = msg.edit_date.replace(tzinfo=None) if msg.edit_date else None
                    if post.content_hash is None:
                        # Eski qator: hash hali hisoblanmagan. Uni to'ldiramiz,
                        # lekin "o'zgargan" deb belgilamaymiz — post allaqachon
                        # shu matn bilan tahlil qilingan. Aks holda birinchi
                        # yurishda butun baza bekorga qayta tahlilga ketardi.
                        post.content_hash = fresh
                        post.edit_date = edited
                        return
                    if fresh == post.content_hash:
                        return  # o'zgarmagan — Claude'ga umuman bormaydi

                    post.text = msg.message
                    post.content_hash = fresh
                    post.edit_date = edited
                    post.comment_available = bool(msg.replies and msg.replies.comments)
                    post.processed = False
                    post.processing_status = "pending"
                    post.last_error = None
                    post.next_retry_at = None
                    changed.append(post.id)
                    channel_changed += 1

                # 1) Oxirgi N ta xabar
                async for msg in client.iter_messages(entity, limit=window):
                    seen_ids.add(msg.id)
                    apply(msg)

                # 2) Faol turi bor, lekin oynadan tashqarida qolgan postlar
                active_post_ids = set(db.scalars(
                    select(Tour.raw_post_id).where(
                        Tour.channel == channel,
                        Tour.departure_date >= active_cutoff,
                        Tour.raw_post_id.is_not(None),
                    ).distinct()
                ).all())
                extra: list[int] = []
                for post in stored.values():
                    if post.id not in active_post_ids:
                        continue
                    message_id = _message_id(post.source_id)
                    if message_id is not None and message_id not in seen_ids:
                        extra.append(message_id)

                for start in range(0, len(extra), 100):
                    batch = extra[start:start + 100]
                    try:
                        messages = await client.get_messages(entity, ids=batch)
                    except Exception as exc:
                        log.warning("%s: xabarlarni olishda xato: %s", channel, exc)
                        continue
                    for msg in messages:
                        apply(msg)  # o'chirilgan xabar None bo'lib keladi

                db.commit()
                log.info(
                    "%s: %d tahrirlangan (oyna %d + faol tur %d)",
                    channel, channel_changed, window, len(extra),
                )

    log.info(
        "tahrir tekshiruvi: %d post o'zgargan, %.2f soniya",
        len(changed), time.perf_counter() - started,
    )
    return changed


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--login", action="store_true", help="Session string olish")
    args = parser.parse_args()

    if args.login:
        asyncio.run(login())
    else:
        total = asyncio.run(scrape())
        print(f"Jami {total} ta yangi post saqlandi.")


if __name__ == "__main__":
    main()
