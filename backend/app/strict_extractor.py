"""Strict, bepul va kontekstga asoslangan Telegram tur parseri.

ISHLATILMAYDI. Turlar faqat Claude orqali ajratiladi (`claude_extractor.py`).
Bu modul tarix va zaxira sifatida saqlanadi: uni ishlatish uchun
`extractor.extract_many` ni qo'lda o'zgartirish kerak. Testlari
`tests/test_strict_extractor.py` da saqlanib turibdi.

Sabab: real kanallarda bu parser 40 ta postdan 0 ta tur chiqargan, Claude esa
o'sha postlardan to'g'ri natija bergan. Avtomatik fallback ataylab olib
tashlangan — Claude ishlamay qolganda past sifatli ma'lumot yozilishidan
ko'ra postni qayta ishlashga qoldirish afzal.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from .extractor import TourExtraction
from .rule_extractor import CITY_ALIASES, CITY_COUNTRY, COUNTRY_ALIASES, extract_dates

TOUR_WORDS = ("tur", "tour", "sayohat", "путешеств", "пакет", "вылет", "uchish", "\u0443\u0447\u0438\u0448", "jo'nash")
DEPARTURE_WORDS = ("вылет", "дата вылета", "uchish", "\u0443\u0447\u0438\u0448", "jo'nash", "ketish", "departure", "отправление", "🛫", "✈")
PRICE_POSITIVE = ("цена тура", "стоимость тура", "цены от", "tur narxi", "тур нархи", "paket narxi", "от ", "dan", "за человека")
PRICE_NEGATIVE = ("отель", "hotel", "mehmonxona", "доплата", "экскурс", "visa", "виза", "страхов", "transfer", "трансфер", "city tax", "за ночь", "ребен", "ребён", "bola", "\u0431\u0438\u043b\u0435\u0442", "\u0430\u0432\u0438\u0430\u0431\u0438\u043b\u0435\u0442", "chipta", "aviachipta")
HEADING_BLOCKERS = ("narxga kiradi", "стоимость включ", "обращайтесь", "murojaat", "питание", "ovqatlanish", "адрес", "телефон")

MEAL_CODES = {
    "uai": "UAI", "ultra all inclusive": "UAI", "ультра всё включено": "UAI",
    "ai": "AI", "all inclusive": "AI", "всё включено": "AI", "все включено": "AI",
    "fb": "FB", "full board": "FB", "полный пансион": "FB",
    "hb": "HB", "half board": "HB", "полупансион": "HB",
    "bb": "BB", "bed and breakfast": "BB", "завтрак": "BB", "nonushta": "BB",
    "ro": "RO", "room only": "RO", "без питания": "RO", "ovqatsiz": "RO",
}


NAMED_MONTHS = {
    "yanvar": 1, "\u044f\u043d\u0432\u0430\u0440\u044c": 1, "\u044f\u043d\u0432\u0430\u0440\u044f": 1,
    "fevral": 2, "\u0444\u0435\u0432\u0440\u0430\u043b\u044c": 2, "\u0444\u0435\u0432\u0440\u0430\u043b\u044f": 2,
    "mart": 3, "\u043c\u0430\u0440\u0442": 3, "\u043c\u0430\u0440\u0442\u0430": 3,
    "aprel": 4, "\u0430\u043f\u0440\u0435\u043b\u044c": 4, "\u0430\u043f\u0440\u0435\u043b\u044f": 4,
    "may": 5, "\u043c\u0430\u0439": 5, "\u043c\u0430\u044f": 5,
    "iyun": 6, "\u0438\u044e\u043d\u044c": 6, "\u0438\u044e\u043d\u044f": 6,
    "iyul": 7, "\u0438\u044e\u043b\u044c": 7, "\u0438\u044e\u043b\u044f": 7,
    "avgust": 8, "\u0430\u0432\u0433\u0443\u0441\u0442": 8, "\u0430\u0432\u0433\u0443\u0441\u0442\u0430": 8,
    "sentabr": 9, "\u0441\u0435\u043d\u0442\u044f\u0431\u0440\u044c": 9, "\u0441\u0435\u043d\u0442\u044f\u0431\u0440\u044f": 9,
    "oktabr": 10, "\u043e\u043a\u0442\u044f\u0431\u0440\u044c": 10, "\u043e\u043a\u0442\u044f\u0431\u0440\u044f": 10,
    "noyabr": 11, "\u043d\u043e\u044f\u0431\u0440\u044c": 11, "\u043d\u043e\u044f\u0431\u0440\u044f": 11,
    "dekabr": 12, "\u0434\u0435\u043a\u0430\u0431\u0440\u044c": 12, "\u0434\u0435\u043a\u0430\u0431\u0440\u044f": 12,
}

@dataclass
class PriceCandidate:
    amount: float
    currency: str
    start: int
    end: int
    context: str
    score: int


def _normalize(text: str) -> str:
    value = re.sub(r"([0-9])\ufe0f?\u20e3", r"\1", text or "")
    return value.replace("💲", "$").replace("💵", "$").replace("＄", "$")


def _number(raw: str) -> float | None:
    value = raw.strip().replace(" ", "")
    if "," in value and "." not in value:
        tail = value.rsplit(",", 1)[-1]
        value = value.replace(",", ".") if len(tail) <= 2 else value.replace(",", "")
    else:
        value = value.replace(",", "")
    try:
        return float(value)
    except ValueError:
        return None


def price_candidates(text: str) -> list[PriceCandidate]:
    value = _normalize(text)
    patterns = (
        (r"(?<!\d)(\d[\d\s.,]{0,12})\s*(?:\$|usd|у\.?\s*е\.?)", "USD"),
        (r"(?:\$|usd)\s*(\d[\d\s.,]{0,12})", "USD"),
        (r"(?<!\d)(\d[\d\s.,]{0,12})\s*(?:€|eur)", "EUR"),
        (r"(?<!\d)(\d[\d\s.,]{0,12})\s*(?:mln|млн)\s*(?:so['‘’`]?m|сум)", "UZS_MLN"),
        (r"(?<!\d)(\d[\d\s]{3,14})\s*(?:so['‘’`]?m|uzs|сум)", "UZS"),
    )
    found: list[PriceCandidate] = []
    for pattern, currency in patterns:
        for match in re.finditer(pattern, value, re.I):
            amount = _number(match.group(1))
            if amount is None:
                continue
            if currency == "UZS_MLN":
                amount, currency_name = amount * 1_000_000, "UZS"
            else:
                currency_name = currency
            line_start = value.rfind("\n", 0, match.start()) + 1
            line_end = value.find("\n", match.end())
            line_end = len(value) if line_end < 0 else line_end
            context = value[line_start:line_end].strip()
            lowered = context.casefold()
            score = 0
            score += 8 if any(word in lowered for word in PRICE_POSITIVE) else 0
            score -= 12 if any(word in lowered for word in PRICE_NEGATIVE) else 0
            score += 4 if len(extract_dates(context)) >= 1 else 0
            score += 2 if currency_name in {"USD", "EUR"} and amount >= 100 else 0
            score -= 8 if currency_name in {"USD", "EUR"} and amount < 100 else 0
            found.append(PriceCandidate(amount, currency_name, match.start(), match.end(), context, score))
    unique: dict[tuple[float, str, int], PriceCandidate] = {}
    for item in found:
        unique[(item.amount, item.currency, item.start)] = item
    return list(unique.values())


def select_tour_price(text: str, local_line: str = "") -> tuple[float | None, str | None]:
    candidates = price_candidates(text)
    if local_line:
        local = price_candidates(local_line)
        for item in local:
            item.score += 5
        candidates.extend(local)
    valid = [item for item in candidates if item.score >= 2]
    if not valid:
        return None, None
    best = max(valid, key=lambda item: (item.score, item.amount))
    return best.amount, best.currency


def _locations(text: str) -> tuple[list[str], list[str]]:
    lowered = text.casefold()
    countries = [country for country, aliases in COUNTRY_ALIASES.items() if any(alias.casefold() in lowered for alias in aliases)]
    cities = [city for city, aliases in CITY_ALIASES.items() if any(alias.casefold() in lowered for alias in aliases)]
    # Eski alias faylidagi Cyrillic encodingdan mustaqil, eng ko'p uchraydigan nomlar.
    if any(alias in lowered for alias in ("\u0430\u043d\u0442\u0430\u043b\u0438\u044f", "antalya")) and "Antalya" not in cities:
        cities.append("Antalya")
    if any(alias in lowered for alias in ("\u0442\u0443\u0440\u0446\u0438\u044f", "\u0442\u0443\u0440\u043a\u0438\u044f", "turkiya", "turkey")) and "Turkiya" not in countries:
        countries.append("Turkiya")
    for city in cities:
        country = CITY_COUNTRY.get(city)
        if country and country not in countries:
            countries.append(country)
    return list(dict.fromkeys(countries)), list(dict.fromkeys(cities))


def _meal(text: str) -> str:
    for line in text.splitlines():
        lowered = line.casefold()
        if not re.search(r"(?:питание|ovqatlanish|meal|🍽)", lowered):
            continue
        payload = re.split(r"[:：–—-]", lowered, maxsplit=1)[-1].strip()
        for alias, code in sorted(MEAL_CODES.items(), key=lambda item: len(item[0]), reverse=True):
            if re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", payload):
                return code
    return ""


def _hotel(text: str) -> tuple[str, int]:
    """Faqat aniq hotel/mehmonxona labeli yonidagi nomni oladi."""
    patterns = (
        r"(?:hotel|mehmonxona|отель|гостиница)\s*[:\-–—]\s*([^\n]{2,100})",
        r"(?:🏨)\s*([^\n]{2,100})",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if not match:
            continue
        value = match.group(1).strip().strip("-–—.,")
        if not value or re.fullmatch(r"(?:проживание|joylashuv|accommodation|[1-5]\s*\*)", value, re.I):
            continue
        stars_match = re.search(r"(?<!\d)([1-5])\s*\*", value)
        stars = int(stars_match.group(1)) if stars_match else 0
        name = re.sub(r"\s*(?:[1-5]\s*\*|[⭐★]{1,5})\s*$", "", value).strip()
        if len(name) >= 2:
            return name[:128], stars
    return "", 0

NIGHTS_PATTERN = r"(?<!\d)(\d{1,2})\s*(?:kecha|ноч(?:ей|и|ь)?|nights?)(?!\w)"


def _explicit_duration(text: str) -> tuple[int | None, int | None]:
    days = re.search(r"(?<!\d)(\d{1,2})\s*(?:kun|дн(?:ей|я|ь)?|days?)(?!\w)", text, re.I)
    nights = re.search(NIGHTS_PATTERN, text, re.I)
    return (int(days.group(1)) if days else None, int(nights.group(1)) if nights else None)


def _night_mentions(text: str) -> list[int]:
    """Matndagi barcha "N kecha" qiymatlari.

    Ko'p shaharli turlarda kechalar shaharlar bo'yicha bo'linadi
    ("Istanbul 2 kecha", "Trabzon 5 kecha") — bunda tur davomiyligi ularning
    yig'indisi bo'ladi, birinchi uchragan son emas.
    """
    return [int(value) for value in re.findall(NIGHTS_PATTERN, text, re.I)]


def _is_heading(line: str) -> bool:
    clean = re.sub(r"^[^\wА-Яа-яЁё]+", "", line).strip()
    lowered = clean.casefold()
    if not 2 < len(clean) <= 180 or any(word in lowered for word in HEADING_BLOCKERS):
        return False
    if extract_dates(clean) or price_candidates(clean):
        return False
    countries, cities = _locations(clean)
    return bool(countries or cities or any(word in lowered for word in ("тур", "tour", "sayohat")))


def _named_month_day_list(text: str, reference: date) -> list[date]:
    month_pattern = "|".join(sorted((re.escape(name) for name in NAMED_MONTHS), key=len, reverse=True))
    match = re.search(
        rf"(?<!\d)(\d{{1,2}}(?:\s*(?:-|\u2013|\u2014|,|/)\s*\d{{1,2}})+)\s*({month_pattern})(?!\w)",
        text,
        re.I,
    )
    if not match:
        return []
    month = NAMED_MONTHS[match.group(2).casefold()]
    values: list[date] = []
    for raw_day in re.findall(r"\d{1,2}", match.group(1)):
        try:
            values.append(date(reference.year, month, int(raw_day)))
        except ValueError:
            continue
    return list(dict.fromkeys(values))


def _strict_dates(text: str, reference: date) -> list[date]:
    named_days = _named_month_day_list(text, reference)
    if named_days:
        return named_days
    range_pattern = re.compile(
        r"(?<!\d)(\d{1,2})[./](\d{1,2})(?:[./](20\d{2}))?\s*(?:—|–|-)\s*"
        r"(\d{1,2})[./](\d{1,2})(?:[./](20\d{2}))?(?!\d)"
    )
    match = range_pattern.search(text)
    if match:
        first_year = int(match.group(3) or reference.year)
        second_year = int(match.group(6) or first_year)
        try:
            first = date(first_year, int(match.group(2)), int(match.group(1)))
            second = date(second_year, int(match.group(5)), int(match.group(4)))
            if second < first and match.group(6) is None:
                second = date(first_year + 1, second.month, second.day)
            return [first, second]
        except ValueError:
            return []
    return extract_dates(text, reference)

def _offer_lines(text: str, reference_date: date) -> list[tuple[str, str, list[date], str]]:
    lines = [part.strip() for part in text.splitlines()]
    heading_indexes = [index for index, line in enumerate(lines) if line and _is_heading(line)]
    offers: list[tuple[str, str, list[date], str]] = []
    seen_named_day_lists: set[tuple[date, ...]] = set()
    for index, line in enumerate(lines):
        if not line:
            continue
        named_days = _named_month_day_list(line, reference_date)
        dates = named_days or _strict_dates(line, reference_date)
        if named_days:
            day_key = tuple(named_days)
            if day_key in seen_named_day_lists:
                continue
            seen_named_day_lists.add(day_key)
        lowered = line.casefold()
        if not dates or not (any(word in lowered for word in DEPARTURE_WORDS) or len(dates) >= 2 or price_candidates(line)):
            continue
        previous = [position for position in heading_indexes if position < index]
        heading_index = previous[-1] if previous else 0
        following = [position for position in heading_indexes if position > index]
        end_index = following[0] if following else len(lines)
        heading = lines[heading_index] if heading_index in heading_indexes else ""
        context = "\n".join(part for part in lines[heading_index:end_index] if part)
        if named_days:
            offers.extend((heading, line, [departure], context) for departure in named_days)
        else:
            offers.append((heading, line, dates, context))
    return offers

def extract_strict_many(text: str, reference_date: date | None = None) -> list[TourExtraction]:
    raw = _normalize((text or "").strip())
    reference = reference_date or date.today()
    lowered = raw.casefold()
    if len(raw) < 30 or not any(word in lowered for word in TOUR_WORDS):
        return []

    offers = _offer_lines(raw, reference)
    if not offers:
        return []
    global_countries, global_cities = _locations(raw)
    explicit_days, explicit_nights = _explicit_duration(raw)
    results: list[TourExtraction] = []

    for heading, offer_line, dates, offer_context in offers:
        departure = dates[0]
        returned = dates[1] if len(dates) > 1 and dates[1] >= departure else None
        # Kanallar qaytish sanasini kamdan-kam yozadi, lekin kecha sonini deyarli doim
        # ko'rsatadi ("6kecha") — shundan qaytish sanasini aniq hisoblash mumkin.
        derived_return = False
        if returned is None and explicit_nights:
            returned = departure + timedelta(days=explicit_nights)
            derived_return = True
        countries, cities = _locations(heading or offer_line)
        if not countries:
            countries = global_countries
        if not cities and len(offers) == 1:
            cities = global_cities
        if not countries:
            continue

        calculated_days = (returned - departure).days + 1 if returned else None
        calculated_nights = (returned - departure).days if returned else None
        validation_errors: list[str] = []
        # Qaytish sanasi kecha sonidan olingan bo'lsa, taqqoslash o'z-o'ziga bo'ladi —
        # ziddiyat tekshiruvi faqat mustaqil ikkinchi sana bo'lganda mantiqiy.
        if not derived_return:
            if calculated_days and explicit_days and calculated_days != explicit_days:
                validation_errors.append("duration_conflict")
            if calculated_nights is not None and explicit_nights is not None and calculated_nights != explicit_nights:
                # Shaharlar bo'yicha bo'lingan kechalar yig'indisi sanalar oralig'iga
                # to'g'ri kelsa, ziddiyat yo'q — post shunchaki batafsil yozilgan.
                if calculated_nights != sum(_night_mentions(raw)):
                    validation_errors.append("nights_conflict")
        duration = calculated_days or explicit_days or (explicit_nights + 1 if explicit_nights is not None else None)
        nights = calculated_nights if calculated_nights is not None else (explicit_nights or 0)
        scoped_text = raw if len(offers) == 1 else offer_context
        amount, currency = select_tour_price(scoped_text, offer_line)
        meal = _meal(scoped_text)
        hotel_name, hotel_stars = _hotel(scoped_text)
        route_country = " + ".join(countries[:4])
        route_city = " + ".join(cities)[:128] if cities else None
        required_values = {
            "country": route_country,
            "city": route_city,
            "price": amount if amount is not None and currency else None,
            "departure_date": departure.isoformat() if departure else None,
            "return_date": returned.isoformat() if returned else None,
            "duration_nights": nights if nights > 0 else None,
        }
        validation_errors.extend(
            f"missing_{field}" for field, value in required_values.items() if value in (None, "")
        )
        # Mehmonxona va ovqatlanish kanallarda kamdan-kam ko'rsatiladi. Ular yetishmasa
        # ham tur e'lon qilinadi, lekin last_error'da ko'rinib turadi.
        soft_errors = [
            f"missing_{field}" for field, value in (("meal", meal), ("hotel", hotel_name))
            if value in (None, "")
        ]
        title = (route_city or route_country) + f" — {departure.isoformat()}"
        results.append(TourExtraction(
            is_tour=True, title=title[:256], country=route_country[:128], city=route_city,
            price_amount=amount, price_currency=currency, departure_date=departure.isoformat(),
            return_date=returned.isoformat() if returned else "", duration_days=duration,
            nights=nights, meal_plan=meal, hotel_name=hotel_name, hotel_stars=hotel_stars,
            summary=raw[:6000], validation_errors=validation_errors + soft_errors,
            publishable=not validation_errors,
        ))
    # Bir xil route/sana/narx takrorlarini olib tashlash.
    unique: dict[tuple, TourExtraction] = {}
    for item in results:
        key = (item.country, item.city, item.departure_date, item.return_date, item.price_amount, item.price_currency)
        unique[key] = item
    return list(unique.values())