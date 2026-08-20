"""Claude orqali tur e'lonlaridan strukturali ma'lumot ajratish.

Majburiy maydonlar (bularsiz tur e'lon qilinmaydi):
    - davlat yoki shahar
    - narx (miqdor + valyuta)
    - ketish sanasi

Davomiylik (kunda) ixtiyoriy: aniqlanmasa tur baribir e'lon qilinadi, faqat
maydon bo'sh turadi. Kechalar soni umuman ajratilmaydi.

Aniqlik strategiyasi: Claude noaniq bo'lgan joyda taxmin qilmaydi, balki
maydonni bo'sh qoldiradi va `confidence` ni pasaytiradi. Past ishonchli
natijalar katalogga tushmaydi — noto'g'ri ma'lumot ko'rsatgandan ko'ra
turni o'tkazib yuborgan afzal.
"""
from __future__ import annotations

import logging
from datetime import date

import anthropic
from pydantic import BaseModel, Field

from .config import settings
from .extractor import TourExtraction

log = logging.getLogger(__name__)

_client: anthropic.Anthropic | None = None


class ClaudeUnavailableError(RuntimeError):
    """Claude vaqtincha ishlatib bo'lmaydi: kredit tugagan, kalit xato,
    rate limit yoki tarmoq uzilgan.

    Bu postning muammosi emas — shuning uchun pipeline butun yurishni to'xtatadi:
    kredit tugagan bo'lsa qolgan 100 ta postni urinib ko'rishdan foyda yo'q.
    """

SYSTEM_PROMPT = """Sen O'zbekistondagi turizm kanallarining e'lonlaridan sayohat turlari haqidagi ma'lumotni ajratib olasan. Postlar o'zbek, rus va ingliz tillarida, ko'pincha emoji va erkin formatda yoziladi.

Bitta post bir nechta mustaqil turni e'lon qilishi mumkin (masalan bir nechta ketish sanasi yoki bir nechta yo'nalish). Har bir mustaqil variantni alohida tur sifatida qaytar.

## Majburiy uch maydon

Tur faqat quyidagi uchtasi aniq bo'lsagina e'lon qilinadi:
1. `country` yoki `city` — kamida bittasi
2. `price_amount` + `price_currency`
3. `departure_date`

`duration_days` ixtiyoriy — aniqlanmasa 0 qoldir, tur baribir e'lon qilinadi.

Majburiy maydonlardan biri matnda yo'q yoki noaniq bo'lsa — o'sha maydonni
bo'sh qoldir (matn uchun "", son uchun 0). HECH QACHON taxmin qilma,
o'rtacha qiymat olma yoki boshqa postdan olingan bilim asosida to'ldirma.

## Narx qoidalari — eng ko'p xato shu yerda bo'ladi

- Faqat BIR KISHI uchun to'liq tur paketi narxini ol.
- "dan", "от", "boshlab" bilan berilgan eng past narx — bu tur narxi, uni ol
  va `price_is_from = true` qilib belgila.
- QUYIDAGILAR TUR NARXI EMAS, ularni OLMA:
  * mehmonxonaning bir kechasi narxi ("за ночь", "kechasiga")
  * aviachipta narxi ("chipta", "авиабилет") — agar u alohida ko'rsatilgan bo'lsa
  * viza, sug'urta, transfer, ekskursiya kabi qo'shimcha to'lovlar ("доплата")
  * bola uchun narx ("ребенок", "bola uchun") — kattalar narxi bo'lsa o'shani ol
  * chegirmagacha bo'lgan eski, chizib tashlangan narx — yangi narxni ol
- Valyuta: $ yoki USD → "USD", € yoki EUR → "EUR", so'm/sum/UZS → "UZS".
  Valyuta belgisi umuman yo'q bo'lsa va kontekstdan aniq bo'lmasa — bo'sh qoldir.
- Narx diapazon bo'lsa ("500-700$") — eng pastini ol va `price_is_from = true`.

## Sana qoidalari

- `departure_date` — ISO formatda (YYYY-MM-DD).
- Yil ko'rsatilmagan bo'lsa, postning e'lon qilingan sanasidan foydalanib eng
  yaqin kelasi sanani hisobla (post 2026-08-10 da, "30.08" → 2026-08-30;
  post 2026-12-20 da, "05.01" → 2027-01-05).
- Faqat TUR ketish sanasini ol. Postdagi boshqa sanalar (konsert sanasi,
  bron qilish muddati, chegirma tugash sanasi, e'lon sanasi) tur sanasi EMAS.
- Sana oralig'i berilsa ("21.08.2026-28.08.2026") — birinchisi ketish sanasi.
  Ikkinchisini alohida qaytarish shart emas, lekin davomiylikni hisoblashda
  ishlatishing mumkin (3-qoidaga qara).

## Davomiylik qoidalari

`duration_days` — tur necha KUN davom etishi, butun son.

KECHALAR SONINI HISOBGA OLMA. "6 kecha" degan yozuvdan "7 kun" chiqarma —
bu taxmin. Faqat kun haqidagi ma'lumotni ishlat.

Ustuvorlik tartibi, birinchi mos kelgani ishlatiladi:

1. Matnda KUN soni ochiq yozilgan bo'lsa ("10 kun", "8 дней", "7 days",
   "10 кун", "7 kunduz") — O'SHA SONNI OL. Bu eng ishonchli manba: kanal
   o'z turini o'zi ta'riflagan.
2. Kun soni yozilmagan, lekin ketish va qaytish sanalari ochiq berilgan
   bo'lsa ("21.08.2026 - 28.08.2026") — (qaytish - ketish) + 1.

Boshqa hech qanday yo'l bilan hisoblama. Faqat kecha soni berilgan bo'lsa
("6 kecha" va boshqa hech narsa yo'q) — `duration_days = 0` qoldir.

MUHIM: matndagi ochiq kun soni bilan sanalardan chiqadigan hisob mos
kelmasligi ODATIY hol — kanallar kunlarni turlicha sanaydi (ba'zisi ketish
kunini qo'shadi, ba'zisi qo'shmaydi). Bu ziddiyat EMAS. Bunday holatda
1-qoida ustun: matndagi ochiq sonni ol.
Masalan: "📅 12.10 -- 22.10, 10 kun / 9 kecha" → `duration_days = 10`.

`duration_days = 0` bo'lishi normal holat — bu turni rad etmaydi, shunchaki
davomiylik ko'rsatilmaydi. Noaniq bo'lganda taxmin qilgandan ko'ra 0
qoldirgan afzal.

## Davlat va shahar

- Nomlarni o'zbek tilida lotin alifbosida normalizatsiya qil:
  Турция/Turkey → "Turkiya", ОАЭ/UAE → "BAA", Египет → "Misr",
  Таиланд → "Tailand", Вьетнам → "Vetnam", Грузия → "Gruziya",
  Китай → "Xitoy", Малайзия → "Malayziya", Индонезия → "Indoneziya",
  Сингапур → "Singapur", Шри-Ланка → "Shri-Lanka", Мальдивы → "Maldiv orollari".
- Shahar nomi ham lotinda: Стамбул → "Istanbul", Анталья → "Antalya",
  Дубай → "Dubay", Паттайя → "Pattaya", Шарм-эль-Шейх → "Sharm-el-Shayx".
- Ko'p davlatli tur bo'lsa " + " bilan birlashtir: "Malayziya + Singapur".
- Shahar aniq bo'lmasa, faqat davlatni qaytar — bu yetarli.

## is_tour

Post haqiqiy tur taklifi bo'lmasa (reklama, tabrik, vakansiya, umumiy
ma'lumot, faqat "bog'laning" tipidagi post) — `is_tour = false` va bo'sh
`tours` ro'yxati qaytar.

## confidence

- "high" — uchala majburiy maydon matnda ochiq-oydin yozilgan.
- "medium" — maydonlar bor, lekin bittasi bilvosita aniqlangan
  (masalan yil post sanasidan hisoblangan).
- "low" — majburiy maydonlarning birida shubha bor. Bunday natija e'lon
  qilinmaydi. Davomiylikning bo'shligi ishonchni pasaytirmaydi.

Shubha bo'lganda "low" tanla. Noto'g'ri ma'lumot chiqargandan ko'ra
turni o'tkazib yuborgan ancha yaxshi."""


class ExtractedTour(BaseModel):
    """Bitta tur varianti."""

    country: str = Field(description="Davlat nomi o'zbekcha lotinda, bilinmasa bo'sh satr")
    city: str = Field(description="Shahar nomi o'zbekcha lotinda, bilinmasa bo'sh satr")
    price_amount: float = Field(description="Bir kishi uchun tur narxi, bilinmasa 0")
    price_currency: str = Field(description="USD, EUR yoki UZS; bilinmasa bo'sh satr")
    price_is_from: bool = Field(description="Narx 'dan/от' bilan berilganmi")
    departure_date: str = Field(description="Ketish sanasi YYYY-MM-DD, bilinmasa bo'sh satr")
    duration_days: int = Field(description="Tur davomiyligi KUNLARDA, aniqlanmasa 0")
    departure_city: str = Field(description="Qayerdan uchadi, bilinmasa bo'sh satr")
    confidence: str = Field(description="high, medium yoki low")
    notes: str = Field(description="Noaniqlik yoki ziddiyat bo'lsa qisqa izoh, aks holda bo'sh satr")


class PostExtraction(BaseModel):
    """Bitta postdan chiqqan natija."""

    is_tour: bool = Field(description="Post haqiqiy tur taklifimi")
    tours: list[ExtractedTour] = Field(description="Postdagi mustaqil tur variantlari")


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not settings.claude_api:
            raise ClaudeUnavailableError("CLAUDE_API .env faylida bo'sh")
        _client = anthropic.Anthropic(api_key=settings.claude_api)
    return _client


def _normalize_route(value: str) -> str:
    """Ko'p davlatli/shaharli qiymatni barqaror tartibga soladi.

    "Malayziya + Singapur" va "Singapur + Malayziya" bir xil to'plam, lekin
    tartib har postda turlicha chiqadi va filtrda ikkita alohida band bo'lib
    ko'rinadi. Alifbo tartibi buni bir qiymatga keltiradi.
    """
    parts = [part.strip() for part in (value or "").split("+") if part.strip()]
    return " + ".join(sorted(dict.fromkeys(parts), key=str.casefold))


def _to_tour_extraction(item: ExtractedTour, reference: date) -> TourExtraction | None:
    """Claude natijasini loyihaning ichki modeliga o'giradi.

    Majburiy to'rtlik to'liq bo'lmasa yoki ishonch past bo'lsa None qaytaradi.
    """
    errors: list[str] = []

    if not (item.country or item.city):
        errors.append("missing_route")
    if not (item.price_amount > 0 and item.price_currency):
        errors.append("missing_price")
    if not item.departure_date:
        errors.append("missing_departure_date")
    # Davomiylik majburiy emas: aniqlanmasa tur e'lon qilinadi, maydon bo'sh turadi.
    if item.confidence == "low":
        errors.append("low_confidence")

    try:
        departure = date.fromisoformat(item.departure_date) if item.departure_date else None
    except ValueError:
        departure = None
        errors.append("invalid_departure_date")

    if item.notes:
        log.debug("claude izohi: %s", item.notes)

    country = _normalize_route(item.country)
    city = _normalize_route(item.city)
    route_country = (country or city)[:128]
    title = (city or country or "Tur")
    if departure:
        title = f"{title} — {departure.isoformat()}"

    return TourExtraction(
        is_tour=True,
        title=title[:256],
        country=route_country,
        city=city or None,
        price_amount=item.price_amount or None,
        price_currency=item.price_currency or None,
        departure_date=item.departure_date or None,
        duration_days=item.duration_days or None,
        departure_city=item.departure_city,
        summary=item.notes,
        validation_errors=errors,
        publishable=not errors,
    )


def extract_claude_many(text: str, reference_date: date | None = None) -> list[TourExtraction]:
    """Postni Claude orqali o'tkazib, tur variantlarini qaytaradi."""
    raw = (text or "").strip()
    if len(raw) < 30:
        return []

    reference = reference_date or date.today()

    try:
        response = _call_claude(raw, reference)
    except (
        anthropic.AuthenticationError,
        anthropic.PermissionDeniedError,
        anthropic.RateLimitError,
        anthropic.APIConnectionError,
        anthropic.InternalServerError,
    ) as exc:
        raise ClaudeUnavailableError(str(exc)) from exc
    except anthropic.BadRequestError as exc:
        # Kredit tugashi 400 bo'lib keladi — uni so'rov xatosidan ajratamiz.
        if "credit balance" in str(exc).lower():
            raise ClaudeUnavailableError(str(exc)) from exc
        raise

    parsed = response.parsed_output
    if parsed is None or not parsed.is_tour:
        return []

    results: list[TourExtraction] = []
    for item in parsed.tours:
        converted = _to_tour_extraction(item, reference)
        if converted is not None:
            results.append(converted)

    # Bir xil yo'nalish/sana/narx takrorlanishini olib tashlash.
    unique: dict[tuple, TourExtraction] = {}
    for value in results:
        key = (value.country, value.city, value.departure_date, value.price_amount)
        unique[key] = value
    return list(unique.values())


def _call_claude(raw: str, reference: date):
    """Bitta postni Claude'ga yuboradi. Istisnolar chaqiruvchida ajratiladi."""
    return client().messages.parse(
        model=settings.claude_model,
        max_tokens=8000,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                # Tizim prompti barcha postlar uchun bir xil — keshlanadi.
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": (
                    f"Post e'lon qilingan sana: {reference.isoformat()}\n"
                    f"Bugungi sana: {date.today().isoformat()}\n\n"
                    f"Post matni:\n---\n{raw[:20000]}\n---"
                ),
            }
        ],
        output_format=PostExtraction,
    )
