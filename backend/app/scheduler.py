"""Belgilangan vaqtlarda create va update joblarini Redis navbatiga qo'yadi.

Jadval:
    CREATE_SCHEDULE (odatda 20:00) — yangi postlarni yig'ib tahlil qiladi
    UPDATE_SCHEDULE (odatda 21:00) — tahrirlangan postlarni topib yangilaydi
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import settings
from .worker import enqueue

log = logging.getLogger(__name__)


def _add_jobs(scheduler: BlockingScheduler, schedule: str, job: str) -> None:
    for value in schedule.split(","):
        value = value.strip()
        if not value:
            continue
        hour_text, minute_text = value.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
        scheduler.add_job(
            enqueue,
            CronTrigger(hour=hour, minute=minute, timezone=settings.pipeline_timezone),
            args=[job],
            id=f"{job}-{hour:02d}-{minute:02d}",
            coalesce=True,
            max_instances=1,
            misfire_grace_time=900,
            replace_existing=True,
        )
        log.info("%s jadvali: %02d:%02d %s", job, hour, minute, settings.pipeline_timezone)


def schedule_pipeline() -> None:
    scheduler = BlockingScheduler(timezone=settings.pipeline_timezone)
    _add_jobs(scheduler, settings.create_schedule, "create")
    _add_jobs(scheduler, settings.update_schedule, "update")
    scheduler.start()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    schedule_pipeline()
