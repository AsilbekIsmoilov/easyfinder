from __future__ import annotations

from telethon import TelegramClient
from telethon.sessions import StringSession

from .config import settings


async def send_source_comment(channel: str, message_id: int, display_name: str, text: str) -> int | None:
    """Linked discussion mavjud bo'lsa original kanal posti ostiga comment yozadi."""
    if not (settings.telegram_session and settings.telegram_api_id and settings.telegram_api_hash):
        return None
    client = TelegramClient(
        StringSession(settings.telegram_session),
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return None
        entity = await client.get_entity(channel)
        sent = await client.send_message(
            entity,
            text,
            comment_to=message_id,
        )
        return sent.id
    except Exception:
        return None
    finally:
        await client.disconnect()