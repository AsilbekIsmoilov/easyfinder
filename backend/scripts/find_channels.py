"""Telegram'dan tur e'lon qiladigan kanallarni qidiradi va reytinglaydi.

Ishlatish (serverda):
    docker compose -f docker-compose.prod.yml exec api python scripts/find_channels.py

MUHIM: bu skript scraper bilan bir xil Telegram sessiyasidan foydalanadi.
`create` (:10) va `update` (:40) joblari ishlayotgan paytda ishga tushirmang —
bitta sessiyaga ikki ulanish sessiyani buzishi mumkin. Eng xavfsiz vaqt: :15–:35.

Nima qiladi:
  1. Bir nechta kalit so'z bo'yicha global qidiruv
  2. Har kanalning obunachi soni va oxirgi postlarini oladi
  3. Postlarda tur belgilari (sana + narx) bor-yo'qligini tekshiradi
  4. Obunachi soni bo'yicha tartiblab chiqaradi
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from telethon import functions  # noqa: E402
from telethon.tl.types import Channel  # noqa: E402

from app.config import settings  # noqa: E402
from app.scraper.telegram import _make_client  # noqa: E402

KEYWORDS = ["tur", "tour", "sayohat", "turizm", "travel", "putevka", "goryashiy tur"]
SAMPLE_POSTS = 25

PRICE = re.compile(r"\d{2,5}\s*(?:\$|USD|у\.е|уе|000\s*so'm|сум)", re.I)
DATE = re.compile(r"(?<!\d)\d{1,2}\s*[./-]\s*\d{1,2}", re.I)
TOUR_WORD = re.compile(r"\btur\b|тур|sayohat|путешеств|отдых|dam olish", re.I)


async def main() -> None:
    known = {c.lower() for c in settings.channels}
    found: dict[str, dict] = {}

    async with _make_client() as client:
        for word in KEYWORDS:
            try:
                res = await client(functions.contacts.SearchRequest(q=word, limit=40))
            except Exception as exc:
                print(f"  qidiruv xatosi ({word}): {exc}")
                continue
            for chat in res.chats:
                # faqat kanallar; guruh va superguruhlar kerak emas
                if not isinstance(chat, Channel) or chat.megagroup or not chat.username:
                    continue
                found.setdefault(chat.username, {"title": chat.title, "id": chat.id})

        print(f"topilgan nomzod kanallar: {len(found)}\n")
        print("tekshirilmoqda (obunachi soni va postlar) ...\n")

        rows = []
        for username, info in found.items():
            try:
                full = await client(functions.channels.GetFullChannelRequest(username))
                subs = full.full_chat.participants_count or 0
                if subs < 500:
                    continue

                tour_posts = 0
                total = 0
                async for msg in client.iter_messages(username, limit=SAMPLE_POSTS):
                    if not msg.message:
                        continue
                    total += 1
                    text = msg.message
                    if PRICE.search(text) and DATE.search(text) and TOUR_WORD.search(text):
                        tour_posts += 1
                if total == 0:
                    continue

                share = tour_posts / total
                rows.append({
                    "username": username,
                    "title": info["title"][:38],
                    "subs": subs,
                    "tour_posts": tour_posts,
                    "sample": total,
                    "share": share,
                })
            except Exception:
                continue

        # Tur e'lonlari ulushi sezilarli bo'lganlarni obunachi bo'yicha tartiblaymiz
        rows = [r for r in rows if r["share"] >= 0.25]
        rows.sort(key=lambda r: (-r["subs"]))

        print(f"{'#':<3}{'kanal':<26}{'obunachi':>10}{'tur/namuna':>12}{'ulush':>8}  nomi")
        print("-" * 96)
        for i, r in enumerate(rows[:25], 1):
            mark = " *" if r["username"].lower() in known else ""
            print(f"{i:<3}@{r['username']:<25}{r['subs']:>10}"
                  f"{str(r['tour_posts']) + '/' + str(r['sample']):>12}"
                  f"{r['share'] * 100:>7.0f}%  {r['title']}{mark}")

        print("\n* — allaqachon kuzatilayotgan kanal")
        print("\nQo'shish uchun .env.production dagi TELEGRAM_CHANNELS ga vergul bilan yozing.")


if __name__ == "__main__":
    asyncio.run(main())
