from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    telegram_api_id: int = 0

    @field_validator("telegram_api_id", mode="before")
    @classmethod
    def _empty_to_zero(cls, v: object) -> object:
        """.env da to'ldirilmagan qiymat ilovani yiqitmasin."""
        return 0 if v in ("", None) else v

    telegram_api_hash: str = ""
    telegram_session: str = ""
    telegram_channels: str = ""
    telegram_bot_token: str = ""
    scrape_limit: int = 200
    scrape_prefilter: bool = True
    scrape_history_days: int = 365

    # --- Claude extraction ---
    # Turlarni ajratishning yagona yo'li. Bepul rule-based parser kodda
    # saqlanadi, lekin ishlatilmaydi — o'chirish/yoqish kaliti ham yo'q.
    claude_api: str = ""
    claude_model: str = "claude-opus-5"


    database_url: str = "sqlite:///./tours.db"
    redis_url: str = "redis://127.0.0.1:6380/0"
    cache_ttl_seconds: int = 60
    run_startup_migrations: bool = True
    media_base_url: str = ""
    admin_job_key: str = ""
    # Yangi postlarni yig'ib tahlil qilish vaqti (vergul bilan bir nechta bo'lishi mumkin)
    create_schedule: str = "20:00"
    # Tahrirlangan postlarni tekshirish vaqti
    update_schedule: str = "21:00"
    pipeline_timezone: str = "Asia/Tashkent"
    pipeline_notifications_enabled: bool = False
    # Operativ xabarlar (limit ogohlantirishi, update hisoboti) shu chat'ga ketadi.
    # Vergul bilan bir nechta chat ID bo'lishi mumkin. Bo'sh bo'lsa yuborilmaydi.
    admin_chat_id: str = ""
    telegram_webapp_url: str = ""
    s3_endpoint_url: str = ""
    s3_bucket: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""

    @property
    def channels(self) -> list[str]:
        return [c.strip().lstrip("@") for c in self.telegram_channels.split(",") if c.strip()]


settings = Settings()
