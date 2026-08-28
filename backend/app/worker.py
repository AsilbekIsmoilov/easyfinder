"""Redis worker: create (yangi postlar) va update (tahrirlangan postlar) joblari."""
from __future__ import annotations

import asyncio
import json
import logging
import time

from redis import Redis

from .notifications import notify_limit_reached, notify_pipeline, notify_updated_tours
from .pipeline import process_pending
from .scraper.telegram import refresh, scrape
from .services import redis_client
from .config import settings

log = logging.getLogger(__name__)
QUEUE = "tour_finder:jobs"

JOBS = {
    "create",              # yangi postlarni yig'ib tahlil qiladi
    "update",              # tahrirlangan postlarni topib qayta tahlil qiladi
    # eski nomlar — qo'lda ishga tushirish uchun saqlanadi
    "scrape",
    "pipeline",
    "scrape_and_pipeline",
}


def enqueue(job: str) -> str:
    client = redis_client()
    if not client:
        raise RuntimeError("Redis mavjud emas")
    if job not in JOBS:
        raise ValueError(f"Noma'lum job: {job}")
    client.rpush(QUEUE, json.dumps({"job": job}))
    return job


def run_create() -> None:
    """Yangi postlarni yig'adi va tahlil qiladi.

    Xabar faqat aytadigan narsa bo'lganda yuboriladi. Soatlik jadvalda
    yurishlarning ko'pchiligida yangi post bo'lmaydi — har safar "0 ta yangi
    tur" deb yozilsa, obunachilar bir kunda botni bloklaydi.
    """
    scraped = asyncio.run(scrape())
    result = process_pending(limit=None)
    log.info(
        "create: %d yangi post, %d ko'rildi, %d tur",
        scraped, result.seen, result.created,
    )
    if result.unavailable:
        notify_limit_reached(result.unavailable, stage="create")
        return
    if result.created and settings.pipeline_notifications_enabled:
        notify_pipeline(scraped=scraped, processed=result.seen, created=result.created)


def run_update() -> None:
    """Tahrirlangan postlarni topib, turlarni yangilaydi.

    Tahrir topilmasa hech qanday xabar yuborilmaydi — bu odatiy holat.
    """
    changed_posts = asyncio.run(refresh())
    if not changed_posts:
        log.info("update: tahrirlangan post yo'q")
        return

    result = process_pending(limit=None)
    log.info(
        "update: %d post o'zgargan, %d tur qiymati o'zgardi",
        len(changed_posts), len(result.changed),
    )
    if result.unavailable:
        notify_limit_reached(result.unavailable, stage="update")
        return
    if result.changed or result.added or result.removed:
        notify_updated_tours(
            len(changed_posts), result.changed, added=result.added, removed=result.removed
        )


def run_worker() -> None:
    client = Redis.from_url(
        settings.redis_url, decode_responses=True, socket_timeout=None,
        socket_connect_timeout=2, health_check_interval=30,
    )
    client.ping()
    log.info("worker tayyor")
    while True:
        try:
            _, payload = client.blpop(QUEUE)
        except Exception:
            # Redis uzilishi worker'ni o'ldirmasin — qayta ulanib davom etadi.
            log.exception("Redis o'qishda xato; 5 soniyadan keyin qayta urinish")
            time.sleep(5)
            continue

        job = json.loads(payload)["job"]
        try:
            if job == "create":
                run_create()
            elif job == "update":
                run_update()
            elif job in {"scrape", "scrape_and_pipeline"}:
                scraped = asyncio.run(scrape())
                if job == "scrape_and_pipeline":
                    result = process_pending(limit=None)
                    if result.unavailable:
                        notify_limit_reached(result.unavailable, stage="create")
                    elif settings.pipeline_notifications_enabled:
                        notify_pipeline(
                            scraped=scraped, processed=result.seen, created=result.created
                        )
            elif job == "pipeline":
                result = process_pending(limit=None)
                if result.unavailable:
                    notify_limit_reached(result.unavailable, stage="create")
            log.info("job tugadi: %s", job)
        except Exception:
            log.exception("job xato: %s", job)
            if settings.pipeline_notifications_enabled:
                try:
                    notify_pipeline(scraped=0, processed=0, created=0, failed=True)
                except Exception:
                    log.exception("failure notification ham yuborilmadi")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_worker()
