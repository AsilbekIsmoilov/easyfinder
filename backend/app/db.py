import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    func,
    Integer,
    Index,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    delete,
    inspect,
    select,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import settings

log = logging.getLogger(__name__)

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
    pool_pre_ping=True,
    pool_recycle=1800,
    pool_size=10 if not settings.database_url.startswith("sqlite") else 5,
    max_overflow=20 if not settings.database_url.startswith("sqlite") else 10,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RawPost(Base):
    """Manbadan olingan xom post. Extraction shu jadvaldan o'qiydi."""

    __tablename__ = "raw_posts"
    __table_args__ = (UniqueConstraint("source", "source_id", name="uq_source_post"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(32))  # telegram | instagram | web
    source_id: Mapped[str] = mapped_column(String(128))  # masalan "kanal:12345"
    channel: Mapped[str] = mapped_column(String(128))
    url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    comment_available: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    # Tahrirni aniqlash uchun: matn hash'i o'zgarsa post qayta tahlil qilinadi.
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    edit_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    processing_status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)


class Tour(Base):
    """LLM ajratib bergan strukturali tur."""

    __tablename__ = "tours"
    __table_args__ = (
        Index("ix_tours_active_order", "departure_date", "channel", "posted_at"),
        Index("ix_tours_price", "price_amount"),
        Index("ix_tours_return_date", "return_date"),
        Index("ix_tours_source", "source"),
        Index("ix_tours_country", "country"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    raw_post_id: Mapped[int] = mapped_column(Integer, index=True)
    # Kanal bir turni bir necha marta e'lon qiladi. Har post alohida tur
    # yaratadi, shuning uchun eskilari takror deb belgilanadi va API ularni
    # ko'rsatmaydi. O'chirilmaydi: post qaytib kelsa yoki eng yangisi
    # eskirsa, qayta tiklanishi mumkin.
    is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    source: Mapped[str] = mapped_column(String(32))
    channel: Mapped[str] = mapped_column(String(128))
    url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    title: Mapped[str] = mapped_column(String(256))
    country: Mapped[str | None] = mapped_column(String(128), nullable=True)
    city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    price_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    departure_date: Mapped[str | None] = mapped_column(String(32), nullable=True)  # YYYY-MM-DD
    return_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    duration_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    includes: Mapped[list | None] = mapped_column(JSON, nullable=True)
    contact: Mapped[str | None] = mapped_column(String(256), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    posted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


def cutoff_date() -> str:
    """Katalog faqat shu sanadan keyin ketadigan turlarni ko'rsatadi."""
    return (date.today() + timedelta(days=4)).isoformat()


def strict_tour_conditions() -> list:
    """Katalogda ko'rinadigan turlar uchun yagona filtr.

    Uchta majburiy maydon aniq bo'lishi kerak: yo'nalish (davlat yoki
    shahar), narx va ketish sanasi. Davomiylik ixtiyoriy — aniqlanmasa
    kartada bo'sh turadi. Qaytish sanasi, ovqatlanish va mehmonxona umuman
    ajratilmaydi.

    Bu ro'yxat shu yerda turadi, chunki uni API (`main.py`) ham, inline
    qidiruv (`inline.py`) ham ishlatadi. Ilgari ikkalasida alohida nusxa
    bor edi va ular bir-biridan uzoqlashib ketgandi: takror filtri faqat
    API ga qo'shilib, inline natijalarida takrorlar ko'rinib turardi.
    """
    return [
        Tour.country.is_not(None), Tour.country != "",
        Tour.price_amount.is_not(None), Tour.price_amount > 0,
        Tour.price_currency.is_not(None), Tour.price_currency != "",
        Tour.departure_date.is_not(None), Tour.departure_date != "",
        # Kanal bir turni qayta e'lon qilganda faqat eng yangisi ko'rsatiladi.
        Tour.is_duplicate.is_(False),
    ]


class TourView(Base):
    __tablename__ = "tour_views"
    __table_args__ = (UniqueConstraint("tour_id", "viewer_key", name="uq_tour_viewer"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tour_id: Mapped[int] = mapped_column(Integer, index=True)
    viewer_key: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class TourLike(Base):
    __tablename__ = "tour_likes"
    __table_args__ = (UniqueConstraint("tour_id", "user_key", name="uq_tour_like"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tour_id: Mapped[int] = mapped_column(Integer, index=True)
    user_key: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class TourComment(Base):
    __tablename__ = "tour_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tour_id: Mapped[int] = mapped_column(Integer, index=True)
    user_key: Mapped[str] = mapped_column(String(128), index=True)
    display_name: Mapped[str] = mapped_column(String(128))
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    photo_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    profile_url: Mapped[str | None] = mapped_column(String(256), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    delivery_status: Mapped[str] = mapped_column(String(24), default="local_only")
    external_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

class TourFeedback(Base):
    __tablename__ = "tour_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tour_id: Mapped[int] = mapped_column(Integer, index=True)
    user_key: Mapped[str] = mapped_column(String(128), index=True)
    display_name: Mapped[str] = mapped_column(String(128))
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    photo_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    profile_url: Mapped[str | None] = mapped_column(String(256), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    reply_to_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

class AppUserRecord(Base):
    __tablename__ = "app_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    telegram_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128), default="User")
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    photo_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # Birinchi kelgan manbasi (startapp=src_<nom>). Bir marta yoziladi, keyin
    # o'zgarmaydi — reklama samarasini o'lchash uchun "birinchi teginish" kerak.
    acquisition_source: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class UserPreference(Base):
    __tablename__ = "user_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    destination: Mapped[str] = mapped_column(String(128))
    max_budget: Mapped[float] = mapped_column(Float)
    travel_date: Mapped[str] = mapped_column(String(10))
    completed: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

class UserActivity(Base):
    __tablename__ = "user_activities"
    __table_args__ = (
        Index("ix_activity_event_created", "event_type", "created_at"),
        Index("ix_activity_channel_created", "channel", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_key: Mapped[str] = mapped_column(String(128), index=True)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    tour_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    country: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    channel: Mapped[str | None] = mapped_column(String(128), nullable=True)
    min_budget: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_budget: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

class NotificationSubscriber(Base):
    __tablename__ = "notification_subscribers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128), default="Telegram user")
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

class Channel(Base):
    """Manba kanallar. Bot buyruqlari (/add, /block) bilan boshqariladi.

    Ro'yxat ilgari faqat TELEGRAM_CHANNELS env'da turardi. U jadval bo'sh
    bo'lganda bir marta shu yerga ko'chiriladi, shundan keyin baza asosiy
    manba: env'ga qo'shilgan kanal e'tiborga olinmaydi, /add ishlatiladi.
    Bloklangan kanal o'chirilmaydi, "blocked" holatda qoladi — aks holda
    keyingi deploy'da env'dan qayta kirib qolardi.
    """

    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)   # @ siz, kichik harf
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)  # active | blocked
    added_by: Mapped[str | None] = mapped_column(String(32), nullable=True)       # admin chat_id
    # Kartada ko'rsatiladi: kanal nomi va dumaloq avatar. Scraper yig'ish
    # paytida bir marta oladi va haftada bir yangilaydi — har so'rovda
    # Telegram'ga murojaat qilish shart emas.
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)     # /media/channels/<username>.jpg
    avatar_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


def normalize_channel(value: str) -> str:
    """'@Kanal', 'https://t.me/Kanal', 't.me/Kanal' -> 'kanal'."""
    value = (value or "").strip()
    for prefix in ("https://t.me/", "http://t.me/", "t.me/", "@"):
        if value.lower().startswith(prefix):
            value = value[len(prefix):]
    return value.strip("/").lower()


def active_channels() -> list[str]:
    """Scraper o'qiydigan kanallar — faqat 'active' holatdagilar."""
    with SessionLocal() as db:
        return db.scalars(
            select(Channel.username).where(Channel.status == "active").order_by(Channel.id)
        ).all()


def channel_meta() -> dict[str, dict]:
    """username -> {title, avatar_url}. Karta sarlavhasi uchun."""
    with SessionLocal() as db:
        rows = db.execute(select(Channel.username, Channel.title, Channel.avatar_url)).all()
    return {u: {"title": t, "avatar_url": a} for u, t, a in rows}


def save_channel_meta(username: str, title: str | None, avatar_url: str | None) -> None:
    """Scraper kanalni ochganda nom va avatarni yozib qo'yadi."""
    with SessionLocal() as db:
        item = db.scalar(select(Channel).where(Channel.username == username))
        if item is None:
            return
        item.title = (title or "")[:256] or None
        if avatar_url is not None:
            item.avatar_url = avatar_url
        item.avatar_updated_at = utcnow()
        db.commit()


def _seed_channels_from_env() -> None:
    """TELEGRAM_CHANNELS ni jadval bo'sh bo'lganda bir marta ko'chiradi."""
    with SessionLocal() as db:
        if (db.scalar(select(func.count()).select_from(Channel)) or 0) > 0:
            return
        for username in dict.fromkeys(normalize_channel(c) for c in settings.channels):
            if username:
                db.add(Channel(username=username, status="active"))
        db.commit()


def purge_channel(username: str) -> tuple[int, int]:
    """Kanalning barcha postlari va turlarini o'chiradi.

    Qaytaradi: (o'chirilgan turlar, o'chirilgan postlar). Kanal avatari
    faylini o'chirish chaqiruvchining ishi — bu funksiya faqat baza.
    """
    with SessionLocal() as db:
        tour_ids = db.scalars(select(Tour.id).where(Tour.channel == username)).all()
        if tour_ids:
            db.execute(delete(TourView).where(TourView.tour_id.in_(tour_ids)))
            db.execute(delete(TourLike).where(TourLike.tour_id.in_(tour_ids)))
            db.execute(delete(TourComment).where(TourComment.tour_id.in_(tour_ids)))
            db.execute(delete(TourFeedback).where(TourFeedback.tour_id.in_(tour_ids)))
            db.execute(delete(Tour).where(Tour.id.in_(tour_ids)))
        posts = db.execute(delete(RawPost).where(RawPost.channel == username)).rowcount
        db.commit()
    return len(tour_ids), posts


def _backfill_existing_users() -> None:
    """Oldingi interactionlardan user va notification subscriberlarni bir marta tiklaydi."""
    with SessionLocal() as db:
        if (db.scalar(select(func.count()).select_from(AppUserRecord)) or 0) > 0:
            return
        keys = set(db.scalars(select(TourView.viewer_key).distinct()).all())
        keys.update(db.scalars(select(TourLike.user_key).distinct()).all())
        keys.update(db.scalars(select(TourComment.user_key).distinct()).all())
        comment_users = {
            row.user_key: row for row in db.scalars(
                select(TourComment).order_by(TourComment.created_at.desc())
            ).all()
        }
        now = utcnow()
        for key in keys:
            comment = comment_users.get(key)
            display_name = comment.display_name if comment else "Telegram user"
            username = comment.username if comment else None
            photo_url = comment.photo_url if comment else None
            telegram_id = key.removeprefix("tg:") if key.startswith("tg:") else None
            db.add(AppUserRecord(
                user_key=key, telegram_id=telegram_id, display_name=display_name,
                username=username, photo_url=photo_url, first_seen_at=now, last_seen_at=now,
            ))
            if telegram_id:
                db.add(NotificationSubscriber(
                    chat_id=telegram_id, display_name=display_name, username=username, enabled=True,
                ))
        db.commit()

def init_db() -> None:
    Base.metadata.create_all(engine)
    _backfill_existing_users()
    _seed_channels_from_env()
    raw_columns = {column["name"] for column in inspect(engine).get_columns("raw_posts")}
    retry_columns = {
        "processing_status": "VARCHAR(24) NOT NULL DEFAULT 'pending'",
        "attempt_count": "INTEGER NOT NULL DEFAULT 0",
        "last_error": "VARCHAR(1000)",
        "next_retry_at": "DATETIME",
        "content_hash": "VARCHAR(64)",
        "edit_date": "DATETIME",
    }
    with engine.begin() as connection:
        for name, definition in retry_columns.items():
            if name not in raw_columns:
                connection.execute(text(f"ALTER TABLE raw_posts ADD COLUMN {name} {definition}"))
        connection.execute(text("UPDATE raw_posts SET processing_status = CASE WHEN processed = 1 THEN 'completed' ELSE 'pending' END WHERE processing_status IS NULL OR processing_status = 'pending'"))
    for index in RawPost.__table__.indexes:
        index.create(engine, checkfirst=True)
    feedback_columns = {c["name"] for c in inspect(engine).get_columns("tour_feedback")}
    if "reply_to_id" not in feedback_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE tour_feedback ADD COLUMN reply_to_id INTEGER"))
    # Har ikkala bazada ham kerak: quyidagi MySQL bloki `return` bilan
    # tugaydi, shuning uchun bu tekshiruv undan OLDIN turishi shart.
    tour_columns = {c["name"] for c in inspect(engine).get_columns("tours")}
    if "is_duplicate" not in tour_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE tours ADD COLUMN is_duplicate BOOLEAN NOT NULL DEFAULT 0"))
            connection.execute(text("CREATE INDEX ix_tours_is_duplicate ON tours (is_duplicate)"))
    user_columns = {c["name"] for c in inspect(engine).get_columns("app_users")}
    if "acquisition_source" not in user_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE app_users ADD COLUMN acquisition_source VARCHAR(32)"))
            connection.execute(text("CREATE INDEX ix_app_users_acquisition_source ON app_users (acquisition_source)"))
    # Tur rasmlari endi saqlanmaydi — eski ustunlar bo'lsa tashlanadi. SQLite
    # DROP COLUMN ni 3.35+ da qo'llaydi; lokal test bazasi uchun xato yutiladi.
    for table in ("raw_posts", "tours"):
        if "photo_url" in {c["name"] for c in inspect(engine).get_columns(table)}:
            try:
                with engine.begin() as connection:
                    connection.execute(text(f"ALTER TABLE {table} DROP COLUMN photo_url"))
            except Exception as exc:
                log.warning("%s.photo_url tashlanmadi: %s", table, exc)
    channel_columns = {c["name"] for c in inspect(engine).get_columns("channels")}
    channel_new = {
        "title": "VARCHAR(256)",
        "avatar_url": "VARCHAR(512)",
        "avatar_updated_at": "DATETIME",
    }
    with engine.begin() as connection:
        for name, definition in channel_new.items():
            if name not in channel_columns:
                connection.execute(text(f"ALTER TABLE channels ADD COLUMN {name} {definition}"))

    if not settings.database_url.startswith("sqlite"):
        if engine.dialect.name == "mysql":
            index_names = {item["name"] for item in inspect(engine).get_indexes("tours")}
            if "ix_tours_fulltext" not in index_names:
                with engine.begin() as connection:
                    connection.execute(text("ALTER TABLE tours ADD FULLTEXT INDEX ix_tours_fulltext (title, country, city, summary)"))
        return
    # SQLite create_all mavjud jadvalga yangi ustun qo'shmaydi.
    columns = {c["name"] for c in inspect(engine).get_columns("tours")}
    if "return_date" not in columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE tours ADD COLUMN return_date VARCHAR(32)"))
            connection.execute(text("UPDATE tours SET return_date = json_extract(details, '$.return_date') WHERE details IS NOT NULL"))
    if "details" not in columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE tours ADD COLUMN details JSON"))
    for index in Tour.__table__.indexes:
        index.create(engine, checkfirst=True)
    raw_columns = {c["name"] for c in inspect(engine).get_columns("raw_posts")}
    if "comment_available" not in raw_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE raw_posts ADD COLUMN comment_available BOOLEAN"))
    comment_columns = {c["name"] for c in inspect(engine).get_columns("tour_comments")}
    additions = {
        "username": "VARCHAR(64)",
        "photo_url": "VARCHAR(1024)",
        "profile_url": "VARCHAR(256)",
    }
    with engine.begin() as connection:
        for name, column_type in additions.items():
            if name not in comment_columns:
                connection.execute(text(f"ALTER TABLE tour_comments ADD COLUMN {name} {column_type}"))


def cleanup_expired_tours(today: str) -> int:
    """Muddati o'tgan tur variantlarini o'chiradi; kelajak sibling variantlar saqlanadi."""
    init_db()
    with SessionLocal() as db:
        expired = db.execute(
            select(Tour.id, Tour.raw_post_id).where(Tour.departure_date < today)
        ).all()
        if not expired:
            return 0
        tour_ids = [row[0] for row in expired]
        candidate_raw_ids = set(row[1] for row in expired)
        db.execute(delete(TourView).where(TourView.tour_id.in_(tour_ids)))
        db.execute(delete(TourLike).where(TourLike.tour_id.in_(tour_ids)))
        db.execute(delete(TourComment).where(TourComment.tour_id.in_(tour_ids)))
        db.execute(delete(TourFeedback).where(TourFeedback.tour_id.in_(tour_ids)))
        db.execute(delete(Tour).where(Tour.id.in_(tour_ids)))
        db.flush()
        still_used = set(db.scalars(
            select(Tour.raw_post_id).where(Tour.raw_post_id.in_(candidate_raw_ids)).distinct()
        ).all())
        orphan_raw_ids = candidate_raw_ids - still_used
        if orphan_raw_ids:
            db.execute(delete(RawPost).where(RawPost.id.in_(orphan_raw_ids)))
        db.commit()
        return len(tour_ids)
