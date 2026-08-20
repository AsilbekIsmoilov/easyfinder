"""SQLite ma'lumotlarini MySQL'ga ID va timestamp'lari bilan ko'chiradi.

Ishlatish:
  python scripts/migrate_sqlite_to_mysql.py \
    --target-url "mysql+pymysql://user:password@127.0.0.1:3306/tour_finder?charset=utf8mb4"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, delete, func, insert, select
from sqlalchemy.orm import Session

from app.db import Base, RawPost, Tour, TourComment, TourLike, TourView

MODELS = (RawPost, Tour, TourView, TourLike, TourComment)


def row_mapping(item, model) -> dict:
    return {column.name: getattr(item, column.name) for column in model.__table__.columns}


def migrate(source_url: str, target_url: str, truncate: bool = False) -> None:
    if not target_url.startswith("mysql+"):
        raise SystemExit("Target MySQL SQLAlchemy URL bo'lishi kerak (mysql+pymysql://...)")
    source = create_engine(source_url, connect_args={"check_same_thread": False})
    target = create_engine(target_url, pool_pre_ping=True, pool_recycle=1800)
    Base.metadata.create_all(target)

    with Session(source) as source_db, Session(target) as target_db:
        if truncate:
            for model in reversed(MODELS):
                target_db.execute(delete(model))
            target_db.commit()

        for model in MODELS:
            source_rows = source_db.scalars(select(model).order_by(model.id)).all()
            target_count = target_db.scalar(select(func.count()).select_from(model))
            if target_count:
                raise SystemExit(
                    f"{model.__tablename__} target jadvali bo'sh emas ({target_count}). "
                    "Qayta ko'chirish uchun --truncate ishlating."
                )
            mappings = [row_mapping(item, model) for item in source_rows]
            for start in range(0, len(mappings), 500):
                target_db.execute(insert(model), mappings[start:start + 500])
            target_db.commit()
            copied = target_db.scalar(select(func.count()).select_from(model))
            if copied != len(source_rows):
                raise RuntimeError(f"{model.__tablename__}: {len(source_rows)} != {copied}")
            print(f"{model.__tablename__}: {copied} ta ko'chirildi")

    source.dispose()
    target.dispose()
    print("Migration muvaffaqiyatli tugadi.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-url", default="sqlite:///./tours.db")
    parser.add_argument("--target-url", required=True)
    parser.add_argument("--truncate", action="store_true")
    args = parser.parse_args()
    migrate(args.source_url, args.target_url, args.truncate)


if __name__ == "__main__":
    main()