"""Eski DBga tegmasdan bepul parser bilan yangi MySQL DBni to'ldiradi."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
from sqlalchemy import func, select
from sqlalchemy.engine import make_url

TARGET_DB = "tour_finder_free"
CHANNELS = (
    "freshtouruz,mandarintour,CentralTur_uz,Asialuxe,inapptravelchannel,"
    "ditraveluz,Clubtravel_uz,travelsystemuz,"
    "anortravelchannel,viatravel_uz,yurtur_uz,manoruz,satori_tour_uz,viptraveluz"
)


def configure() -> None:
    load_dotenv(".env")
    source = os.environ.get("DATABASE_URL", "")
    if not source.startswith("mysql"):
        raise RuntimeError("Free parser trial uchun MySQL DATABASE_URL kerak")
    source_url = make_url(source)

    os.environ["DATABASE_URL"] = source_url.set(database=TARGET_DB).render_as_string(
        hide_password=False
    )
    os.environ["SCRAPE_LIMIT"] = "200"
    os.environ["SCRAPE_PREFILTER"] = "true"
    os.environ["TELEGRAM_CHANNELS"] = CHANNELS
    os.environ["RUN_STARTUP_MIGRATIONS"] = "true"


def main() -> None:
    configure()
    from app.db import SessionLocal, Tour, RawPost
    from app.pipeline import process_pending
    from app.scraper.telegram import scrape
    from sqlalchemy import func, select

    scraped = asyncio.run(scrape())
    result = process_pending(limit=None)
    seen, created = result.seen, result.created
    with SessionLocal() as db:
        raw_total = db.scalar(select(func.count()).select_from(RawPost)) or 0
        tour_total = db.scalar(select(func.count()).select_from(Tour)) or 0
    print(
        f"Free parser trial tayyor: scraped={scraped}, processed={seen}, "
        f"created={created}, raw_total={raw_total}, tour_total={tour_total}, db={TARGET_DB}"
    )


if __name__ == "__main__":
    main()
