# Tour Finder — Telegram Mini App

Telegram kanallari (keyinchalik Instagram va web saytlar) dagi tur e'lonlarini yig'ib,
Claude orqali strukturali ma'lumotga aylantirib, bitta mini app'da ko'rsatadi.

## Arxitektura

```
Telegram kanallar ──(Telethon)──> raw_posts ──(Claude Opus 5)──> tours ──(FastAPI)──> Mini App
```

| Qism | Fayl | Vazifasi |
|---|---|---|
| Scraper | `backend/app/scraper/telegram.py` | Kanallardan postlarni oladi, `raw_posts` ga yozadi |
| Extractor | `backend/app/extractor.py` | Qaysi parser ishlashini tanlaydi (Claude yoki bepul) |
| Claude parser | `backend/app/claude_extractor.py` | Erkin matndan narx/sana/yo'nalish ajratadi (structured outputs) |
| Bepul parser | `backend/app/strict_extractor.py` | Ishlatilmaydi — tarix uchun saqlangan rule-based kod |
| Pipeline | `backend/app/pipeline.py` | `raw_posts` → `tours` |
| API | `backend/app/main.py` | `/api/tours`, `/api/countries` + frontend'ni serve qiladi |
| Mini App | `frontend/index.html` | Telegram WebApp SDK, qidiruv + filtrlar |

## Ishga tushirish

> Windows eslatmasi: venv yaratishda `py -3` ishlating (`python` Microsoft Store
> stub'iga tegishi mumkin). Lekin venv aktivlashtirilgandan **keyin** `python`
> ishlating — `py -3` venv'ni chetlab o'tib global Python'ni chaqiradi va
> o'rnatilgan paketlarni topa olmaydi.

```bash
cd backend
py -3 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env                               # va to'ldiring
```

**1. Telegram session olish** (bir marta) — `my.telegram.org` dan `api_id`/`api_hash` oling,
`.env` ga qo'ying, keyin **oddiy terminalda** (telefon raqami va SMS kodi so'raladi,
shuning uchun interaktiv bo'lishi shart):

```bash
python -m app.scraper.telegram --login
```

Chiqqan session string'ni `.env` dagi `TELEGRAM_SESSION` ga qo'ying.

**2. Postlarni yig'ish va tahlil qilish:**

```bash
python -m app.scraper.telegram    # kanallardan postlarni oladi
python -m app.pipeline            # Claude orqali turlarni ajratadi
```

**3. Serverni ishga tushirish:**

```bash
python -m uvicorn app.main:app --reload
```

`http://localhost:8000` — mini app, `http://localhost:8000/docs` — API.

## Mini App'ni Telegram'ga ulash

1. Serverni HTTPS domenga chiqaring (dev uchun `ngrok http 8000` yetarli).
2. [@BotFather](https://t.me/BotFather) → `/newapp` → botni tanlang → URL sifatida shu domenni bering.
3. Bot menyusiga tugma: `/mybots` → Bot Settings → Menu Button → URL.

## Keyingi qadamlar

- [ ] Cron: scraper + pipeline'ni har soatda avtomatik ishga tushirish
- [ ] Dublikat turlarni birlashtirish (bir xil tur bir necha kanalda chiqadi)
- [ ] Instagram scraper (`source="instagram"` — DB va API allaqachon tayyor)
- [ ] Web sayt parserlari (`source="web"`)
- [ ] Rasmlarni yuklab olish va ko'rsatish (`photo_url` maydoni bo'sh turibdi)
- [ ] Foydalanuvchi sevimlilari (`initData` orqali autentifikatsiya)

## Extraction va aniqlik

Turlar Claude Opus 5 orqali ajratiladi (`CLAUDE_MODEL`). Tur katalogga tushishi
uchun **to'rtta maydon** aniq bo'lishi shart:

1. davlat yoki shahar
2. narx (miqdor + valyuta)
3. ketish sanasi
4. davomiylik (kunda)

Bulardan biri postda yo'q yoki noaniq bo'lsa, Claude taxmin qilmaydi —
`confidence: low` qaytaradi va tur e'lon qilinmaydi. Noto'g'ri ma'lumot
ko'rsatgandan ko'ra turni o'tkazib yuborish afzal deb qabul qilingan.

Claude — turlarni ajratishning **yagona** yo'li. Bepul rule-based parser
(`strict_extractor.py`, `rule_extractor.py`) kodda saqlanadi, lekin hech qachon
ishlatilmaydi va uni yoqadigan kalit ham yo'q.

Claude xato bersa (kredit tugashi, tarmoq uzilishi) avtomatik fallback
**qilinmaydi**: post `retry` deb belgilanadi va muammo bartaraf etilgach qayta
ishlanadi. Sababi — fallback jimgina past sifatli ma'lumotni katalogga
yozadi va buni sezish qiyin.

## Xarajat

Claude Opus 5 — $5 / 1M input, $25 / 1M output. Tizim prompti keshlanadi
(`cache_control: ephemeral`), shuning uchun faqat post matni to'liq narxda
hisoblanadi. O'rtacha post ~400 token, javob ~300 token →
**1 post ≈ $0.01**, ya'ni 1000 post ≈ $10.

Har bir post faqat bir marta qayta ishlanadi (`raw_posts.processed`), shuning
uchun xarajat postlar oqimiga bog'liq, katalog hajmiga emas.
