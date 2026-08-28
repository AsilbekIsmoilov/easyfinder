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
    """Jadval satrini CronTrigger'larga aylantiradi.

    Qo'llab-quvvatlanadigan shakllar (vergul bilan aralashtirsa ham bo'ladi):
        20:00        aniq vaqt
        *:10         har soat, 10-daqiqada
        */2:10       har 2 soatda, 10-daqiqada
    """
    for value in schedule.split(","):
        value = value.strip()
        if not value:
            continue
        hour_text, minute_text = value.split(":", 1)
        minute = int(minute_text)

        if hour_text.startswith("*"):
            step = int(hour_text[2:]) if hour_text.startswith("*/") else 1
            hour = f"*/{step}" if step > 1 else "*"
            label = f"har {step} soatda" if step > 1 else "har soat"
            job_id = f"{job}-h{step}-{minute:02d}"
        else:
            hour = int(hour_text)
            label = f"{hour:02d}:{minute:02d}"
            job_id = f"{job}-{hour:02d}-{minute:02d}"

        scheduler.add_job(
            enqueue,
            CronTrigger(hour=hour, minute=minute, timezone=settings.pipeline_timezone),
            args=[job],
            id=job_id,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=900,
            replace_existing=True,
        )
        log.info("%s jadvali: %s (:%02d) %s", job, label, minute, settings.pipeline_timezone)


def schedule_pipeline() -> None:
    scheduler = BlockingScheduler(timezone=settings.pipeline_timezone)
    _add_jobs(scheduler, settings.create_schedule, "create")
    _add_jobs(scheduler, settings.update_schedule, "update")
    scheduler.start()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    schedule_pipeline()
