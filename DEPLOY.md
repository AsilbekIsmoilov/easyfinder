# TourFinder(Beta) deploy

Loyiha uchta alohida jarayon bilan ishlaydi:

- `api` — FastAPI va Telegram Mini App backend;
- `worker` — Telegram scrape va bepul rule-based parser fon vazifalari;
- `migrate` — MySQL jadvallarini yaratish va eskirgan turlarni tozalash.

Production scheduler avtomatik ishlaydi: har kuni 08:00, 13:00 va 21:00 da scrape va bepul parser pipeline bajariladi.

## 1. Serverni tayyorlash

Ubuntu serverda Docker Engine va Docker Compose plugin o‘rnatilgan bo‘lishi kerak. Loyiha fayllarini serverga ko‘chiring va loyiha papkasiga kiring.

```bash
cp backend/.env.prod.example backend/.env.production
cp .env.mysql.example .env.mysql
```

Quyidagi fayllardagi `CHANGE_ME` qiymatlarini almashtiring:

- `backend/.env.production`: Telegram API, bot tokeni, Telegram session va boshqa ilova sozlamalari;
- `.env.mysql`: MySQL root/user parollari.

`DATABASE_URL` ichidagi MySQL paroli `.env.mysql` dagi `MYSQL_PASSWORD` bilan bir xil bo‘lishi shart. Parolda `@`, `:`, `/`, `#` kabi belgilar bo‘lsa, URL-encoded qiymatdan foydalaning.


Production rejimida faqat bepul rule-based parser ishlaydi; tashqi AI API kaliti va parsing xarajati yo'q.`r`n
## 2. Ishga tushirish

Linux serverda:

```bash
chmod +x deploy.sh
./deploy.sh
```

Windows PowerShell’da:

```powershell
.\deploy.ps1
```

Skript image’ni build qiladi, MySQL va Redis’ni ishga tushiradi, migratsiyani bajaradi, so‘ng API va worker’ni ko‘taradi.

Holat va loglarni tekshirish:

```bash
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs -f api worker scheduler
curl http://localhost:8000/api/health
```

Qayta deploy:

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

To‘xtatish:

```bash
docker compose -f docker-compose.prod.yml down
```

`down -v` ishlatmang: `-v` MySQL va Redis volume’larini ham o‘chiradi.

## 3. HTTPS va Telegram Mini App URL

Telegram Mini App uchun ochiq HTTPS manzil kerak. Reverse proxy orqali `https://sizning-domeningiz.uz` ni serverdagi `http://127.0.0.1:8000` ga yo‘naltiring.

Cloudflare Tunnel ishlatilsa, `backend/.env.production` ichiga `TUNNEL_TOKEN` yozing va:

```bash
docker compose -f docker-compose.prod.yml --profile tunnel up -d --build
```

Keyin BotFather’dagi Main App URL va Web App URL’ni shu HTTPS manzilga o‘zgartiring.

## 4. Hozirgi MySQL ma’lumotlarini serverga ko‘chirish

Eski kompyuterda dump yarating:

```powershell
docker exec tour-finder-mysql mysqldump -uroot -pYOUR_ROOT_PASSWORD --single-transaction --routines --triggers tour_finder_free > tour_finder_free.sql
```

`tour_finder_free.sql` faylini serverga ko‘chiring. Serverdagi yangi stack ishga tushgach, tiklang:

```bash
docker compose -f docker-compose.prod.yml exec -T mysql mysql -uroot -pYOUR_ROOT_PASSWORD tour_finder_free < tour_finder_free.sql
docker compose -f docker-compose.prod.yml restart api worker
```

Parolni shell history’da qoldirmaslik uchun production’da interaktiv MySQL login yoki server secret manager’dan foydalanish tavsiya etiladi.

## 5. Qo‘lda scrape

Pipeline scheduler orqali avtomatik ishlaydi. Zarur holatda worker navbatiga manual yuborish mumkin:

```bash
docker compose -f docker-compose.prod.yml exec api python scripts/enqueue_job.py scrape_and_pipeline
```

Faqat postlarni yig‘ish:

```bash
docker compose -f docker-compose.prod.yml exec api python scripts/enqueue_job.py scrape
```

Faqat oldin yig‘ilgan postlarni bepul parser pipeline’dan o‘tkazish:

```bash
docker compose -f docker-compose.prod.yml exec api python scripts/enqueue_job.py pipeline
```

Production serverda `TELEGRAM_SESSION_STRING` oldindan sozlangan bo‘lishi kerak. Shunda qayta interaktiv login talab qilinmaydi.

## 6. Notification va analytics

Mini Appga kirgan Telegram userlar `notification_subscribers` va `app_users` jadvallarida saqlanadi. Har pipeline tugaganda bot yangi turlar soni bilan notification yuboradi.

Jadval sozlamalari:

```env
PIPELINE_SCHEDULE=08:00,13:00,21:00
PIPELINE_TIMEZONE=Asia/Tashkent
TELEGRAM_WEBAPP_URL=https://sizning-domeningiz.uz
ADMIN_JOB_KEY=uzun-maxfiy-key
```

Admin analytics:

```bash
curl -H "X-Admin-Key: YOUR_ADMIN_KEY" https://sizning-domeningiz.uz/api/admin/analytics
```

Endpoint jami userlar, DAU/WAU/MAU, top qidirilgan davlatlar, o‘rtacha budjet, top turlar, manba bosishlari va kanal view/click reytingini qaytaradi.