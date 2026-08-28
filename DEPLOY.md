# EasyFinder(Beta) — deploy

Loyiha Docker Compose orqali beshta xizmat bilan ishlaydi:

| Xizmat | Vazifasi |
|---|---|
| `mysql` | Ma'lumotlar bazasi |
| `redis` | Job navbati va kesh |
| `migrate` | Jadvallarni yaratadi, eskirgan turlarni tozalaydi (bir marta ishlaydi) |
| `api` | FastAPI + Telegram Mini App |
| `worker` | `create` va `update` joblarini bajaradi |
| `scheduler` | Joblarni jadval bo'yicha navbatga qo'yadi |

Ikkita job bor:

- **create** — kanallardan yangi postlar olinadi va Claude orqali tahlil qilinadi
- **update** — tahrirlangan postlar topiladi, turlarning ma'lumoti yangilanadi

Ishga tushish vaqti `.env.production` dagi `CREATE_SCHEDULE` va `UPDATE_SCHEDULE`
bilan belgilanadi (`PIPELINE_TIMEZONE` bo'yicha):

| Shakl | Ma'nosi |
|---|---|
| `20:00` | aniq vaqt |
| `*:10` | har soat, 10-daqiqada |
| `*/2:10` | har 2 soatda |
| `08:00,14:00,20:00` | vergul bilan bir nechta vaqt |

Ikkala jobni bir daqiqaga qo'ymang — `create` va `update` navbatni baham
ko'radi. Masalan `CREATE_SCHEDULE=*:10`, `UPDATE_SCHEDULE=*:40`.

Chastotani oshirish Claude xarajatini deyarli o'zgartirmaydi, chunki har post
faqat bir marta tahlil qilinadi — faqat tizim prompti keshi tez-tez qayta
yoziladi.

## 1. Serverni tayyorlash

Ubuntu serverda Docker Engine va Docker Compose plugin bo'lishi kerak.

```bash
git clone <repo> tour_finder && cd tour_finder
```

Maxfiy fayllar repozitoriyda yo'q — ularni qo'lda yaratasiz:

```bash
cp backend/.env.prod.example backend/.env.production
cp .env.mysql.example .env.mysql
```

### Majburiy to'ldiriladigan qiymatlar

`backend/.env.production` ichida:

| Sozlama | Izoh |
|---|---|
| `TELEGRAM_SESSION` | Yig'uvchi akkaunt sessiyasi. **Shaxsiy akkaunt ishlatmang** |
| `TELEGRAM_BOT_TOKEN` | @BotFather dan |
| `CLAUDE_API` | console.anthropic.com dan. Bo'lmasa turlar umuman ajratilmaydi |
| `TELEGRAM_WEBAPP_URL` | Haqiqiy HTTPS domen |
| `ADMIN_CHAT_ID` | Limit ogohlantirishi va update hisoboti shu chatga ketadi |
| `DATABASE_URL` | Paroli `.env.mysql` dagi `MYSQL_PASSWORD` bilan bir xil bo'lishi shart |
| `SCRAPE_LIMIT` | Har kanaldan bir yurishda nechta xabar o'qilsin (50–100 tavsiya etiladi) |

`.env.mysql` ichida `MYSQL_PASSWORD` va `MYSQL_ROOT_PASSWORD` — kuchli parollar.
Parolda `@ : / #` belgilari bo'lmasin, aks holda `DATABASE_URL` buziladi.

### Telegram sessiyasi haqida

Sessiya string akkauntga to'liq kirish huquqini beradi. Ikkita qoida:

1. **Alohida akkaunt** ishlating — shaxsiy emas. Cheklov tushsa, shaxsiy akkaunt zarar ko'rmaydi.
2. **Bitta sessiya — bitta joy.** Ayni sessiyani uy kompyuteri va serverda bir vaqtda ishlatmang, Telegram uzib qo'yadi. Uyda kerak bo'lsa alohida sessiya oling:

```bash
python -m app.scraper.telegram --login
```

## 2. Ishga tushirish

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

Tekshirish:

```bash
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs -f api worker scheduler
curl http://localhost:8000/api/health
```

Qayta deploy:

```bash
git pull && docker compose -f docker-compose.prod.yml up -d --build
```

To'xtatish:

```bash
docker compose -f docker-compose.prod.yml down
```

`down -v` **ishlatmang** — `-v` MySQL va Redis volume'larini o'chiradi.

## 3. HTTPS va Mini App

Mini App uchun ochiq HTTPS manzil shart. Ikki yo'l bor.

**Reverse proxy** (nginx + Let's Encrypt) — `https://sizning-domen.uz` ni `http://127.0.0.1:8000` ga yo'naltiring.

**Cloudflare Tunnel** — `backend/.env.production` ga `TUNNEL_TOKEN` yozing va:

```bash
docker compose -f docker-compose.prod.yml --profile tunnel up -d --build
```

Domen tayyor bo'lgach, webhook va menu tugmasini ro'yxatdan o'tkazing:

```bash
docker compose -f docker-compose.prod.yml exec api python -m app.bot_setup https://sizning-domen.uz
```

## 4. Qo'lda job ishga tushirish

```bash
# yangi postlarni yig'ish va tahlil qilish
docker compose -f docker-compose.prod.yml exec api python scripts/enqueue_job.py create

# tahrirlangan postlarni tekshirish
docker compose -f docker-compose.prod.yml exec api python scripts/enqueue_job.py update
```

## 5. Xarajat va monitoring

Turlar Claude Opus 5 orqali ajratiladi. Har post **bir marta** tahlil qilinadi;
matni o'zgarmagan post qayta yuborilmaydi. Xarajat post oqimiga bog'liq, katalog
hajmiga emas: ikki kanal va kuniga bir necha post uchun taxminan **$2–4/oy**.
Jadval tez-tezlashtirilsa yoki kanal qo'shilsa proporsional o'sadi.

Claude krediti tugasa yoki kalit xato bo'lsa, yurish darhol to'xtaydi va
`ADMIN_CHAT_ID` ga ogohlantirish keladi. Postlar navbatda qoladi va muammo hal
bo'lgach avtomatik qayta ishlanadi — katalogga sifatsiz ma'lumot yozilmaydi.

## 6. Zaxira nusxa

```bash
docker compose -f docker-compose.prod.yml exec mysql \
  mysqldump -u root -p"$MYSQL_ROOT_PASSWORD" --single-transaction tour_finder \
  > backup-$(date +%F).sql
```

Tiklash:

```bash
docker compose -f docker-compose.prod.yml exec -T mysql \
  mysql -u root -p"$MYSQL_ROOT_PASSWORD" tour_finder < backup-2026-08-18.sql
docker compose -f docker-compose.prod.yml restart api worker
```

## 7. Deploydan oldingi ro'yxat

- [ ] Yig'uvchi uchun alohida Telegram akkaunt va yangi sessiya
- [ ] Bot tokeni yangilangan
- [ ] Claude API kaliti yangilangan va hisobda kredit bor
- [ ] `.env.mysql` da kuchli parollar, `DATABASE_URL` bilan mos
- [ ] HTTPS domen tayyor
- [ ] `bot_setup` ishga tushirilgan
- [ ] `curl /api/health` javob beryapti
- [ ] Zaxira nusxa cron'ga qo'yilgan
