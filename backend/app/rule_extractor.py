"""API talab qilmaydigan Telegram tur postlari parseri.

ISHLATILMAYDI. Turlar faqat Claude orqali ajratiladi (`claude_extractor.py`).
Bu modul saqlanadi, chunki `strict_extractor.py` undan yordamchi jadvallarni
(CITY_ALIASES, COUNTRY_ALIASES, extract_dates) import qiladi.
Batafsil: `strict_extractor.py` boshidagi izoh.
"""
from __future__ import annotations

import re
from datetime import date

MONTHS = {
    "yanvar": 1, "январ": 1, "fevral": 2, "феврал": 2, "mart": 3, "март": 3,
    "aprel": 4, "апрел": 4, "may": 5, "май": 5, "iyun": 6, "июн": 6,
    "iyul": 7, "июл": 7, "avgust": 8, "август": 8, "sentabr": 9, "сентябр": 9,
    "oktabr": 10, "октябр": 10, "noyabr": 11, "ноябр": 11,
    "dekabr": 12, "декабр": 12,
}

DESTINATIONS = [
    ("BAA", "Dubay", ("dubai", "dubay", "дубай", "uae", "оаэ", "sharja", "шардж")),
    ("Misr", "Sharm el-Sheyx", ("sharm", "шарм", "hurghada", "хургада", "misr", "египет")),
    ("Turkiya", "Antalya", ("antalya", "антал", "kemer", "belek", "bodrum", "turkiya", "турц")),
    ("Tailand", "Pxuket", ("phuket", "pxuket", "пхукет", "tailand", "таиланд", "pattaya")),
    ("Vetnam", "Fukuok", ("phu quoc", "fukuok", "фукуок", "vietnam", "vetnam", "вьетнам")),
    ("Gruziya", "Batumi", ("batumi", "батуми", "tbilisi", "тбилиси", "gruziya", "грузи")),
    ("Ozarbayjon", "Boku", ("baku", "boku", "баку", "azerba", "озарбай")),
    ("Xitoy", "Xaynan", ("hainan", "xaynan", "хайнан", "sanya", "санья", "china", "xitoy", "китай")),
    ("Maldiv orollari", "Maldiv", ("maldiv", "мальдив")),
    ("Shri-Lanka", "Kolombo", ("sri lanka", "shri-lanka", "шри-ланк")),
    ("Saudiya Arabistoni", "Makka", ("umra", "умра", "makk", "макк", "madina", "медин")),
    ("Indoneziya", "Bali", ("bali", "бали", "indonez", "индонез")),
    ("Qatar", "Doha", ("qatar", "катар", "doha", "доха")),
    ("Gretsiya", None, ("greece", "gretsiya", "грец")),
    ("Kipr", None, ("cyprus", "kipr", "кипр")),
]


def _yearless(day: int, month: int, today: date) -> date | None:
    try:
        value = date(today.year, month, day)
        # Faqat haqiqiy yil almashinuvi (masalan dekabr -> yanvar) keyingi yilga o'tadi.
        return date(today.year + 1, month, day) if value < today and (today - value).days > 180 else value
    except ValueError:
        return None


def extract_dates(text: str, today: date | None = None) -> list[date]:
    today = today or date.today()
    found: list[tuple[int, date]] = []
    full_spans: list[tuple[int, int]] = []
    for match in re.finditer(r"(?<!\d)(20\d{2})[./-](\d{1,2})[./-](\d{1,2})(?!\d)", text):
        try:
            found.append((match.start(), date(*map(int, match.groups()))))
            full_spans.append(match.span())
        except ValueError:
            pass
    for match in re.finditer(r"(?<!\d)(\d{1,2})[./-](\d{1,2})(?:[./-](20\d{2}))?(?!\d)", text):
        if any(start <= match.start() < end for start, end in full_spans):
            continue
        day, month = int(match.group(1)), int(match.group(2))
        try:
            value = date(int(match.group(3)), month, day) if match.group(3) else _yearless(day, month, today)
            if value:
                found.append((match.start(), value))
        except ValueError:
            pass
    month_names = "|".join(sorted(map(re.escape, MONTHS), key=len, reverse=True))
    pattern = re.compile(
        rf"(?:(20\d{{2}})\s*(?:yil|г(?:од)?\.?)?\s*)?(\d{{1,2}})\s*[- ]?\s*({month_names})\b",
        re.IGNORECASE,
    )
    for match in pattern.finditer(text):
        day, month = int(match.group(2)), MONTHS[match.group(3).lower()]
        try:
            value = date(int(match.group(1)), month, day) if match.group(1) else _yearless(day, month, today)
            if value:
                found.append((match.start(), value))
        except ValueError:
            pass
    result: list[date] = []
    for _, value in sorted(found):
        if value not in result:
            result.append(value)
    return result


def _price(text: str) -> tuple[float | None, str | None]:
    # Telegram postlarida narxlar ko'pincha 8️⃣9️⃣9️⃣💲 kabi emoji bilan yoziladi.
    # Keycap raqamlarini oddiy raqamga, dollar emojisini esa `$` belgisiga aylantiramiz.
    text = re.sub(r"([0-9])\ufe0f?\u20e3", r"\1", text)
    text = text.replace("💲", "$").replace("💵", "$")
    variants = [
        (r"(?<!\d)(\d[\d\s.,]{1,12})\s*(?:\$|usd|у\.?\s*е\.?)", "USD"),
        (r"(?:\$|usd)\s*(\d[\d\s.,]{1,12})", "USD"),
        (r"(?<!\d)(\d[\d\s.,]{1,12})\s*(?:mln|млн)\s*(?:so['‘’`]?m|сум)", "UZS_MLN"),
        (r"(?<!\d)(\d[\d\s]{3,14})\s*(?:so['‘’`]?m|uzs|сум)", "UZS"),
        (r"(?<!\d)(\d[\d\s.,]{1,12})\s*(?:€|eur)", "EUR"),
    ]
    for pattern, currency in variants:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        raw = match.group(1).strip().replace(" ", "")
        raw = raw.replace(",", ".") if "," in raw and "." not in raw else raw.replace(",", "")
        try:
            amount = float(raw)
            return (amount * 1_000_000, "UZS") if currency == "UZS_MLN" else (amount, currency)
        except ValueError:
            pass
    return None, None


def extract_rules(text: str, reference_date: date | None = None):
    from .extractor import TourExtraction

    text = (text or "").strip()
    if len(text) < 30:
        return None
    lowered = text.lower()
    dates = extract_dates(text, reference_date)
    offer_words = ("tur", "тур", "paket", "пакет", "uchish", "вылет", "jo'nash", "sayohat", "tour", "travel", "путешеств")
    if not dates or not any(word in lowered for word in offer_words):
        return TourExtraction(is_tour=False)

    country = city = None
    for standard_country, standard_city, aliases in DESTINATIONS:
        if any(alias in lowered for alias in aliases):
            country, city = standard_country, standard_city
            break
    amount, currency = _price(text)
    days = re.search(r"(\d{1,2})\s*(?:kun|дн(?:ей|я)?|days?)", lowered)
    nights_match = re.search(r"(\d{1,2})\s*(?:kecha|ноч(?:ей|и)?|nights?)", lowered)
    duration = int(days.group(1)) if days else None
    nights = int(nights_match.group(1)) if nights_match else 0
    if not duration and nights:
        duration = nights + 1
    hotel = re.search(r"(?:🏨|🏚|hotel[:\s]*)\s*([^\n]{3,80}?)(?:\s+([1-5])\s*\*|$)", text, re.I | re.M)
    meal = re.search(r"(?:ovqatlanish|питание|meal)[\s:–-]*([^\n]{2,50})", text, re.I)
    contact = re.search(r"(?:\+998[\d\s()-]{7,}|@[A-Za-z][A-Za-z0-9_]{4,})", text)
    include_map = [
        ("aviachipta", ("avia", "авиа", "перелет", "перелёт")),
        ("mehmonxona", ("mehmonxona", "hotel", "отел", "проживан")),
        ("transfer", ("transfer", "трансфер")),
        ("sug'urta", ("sug'urta", "страхов", "insurance")),
        ("ovqatlanish", ("ovqat", "питание", "meal", "nonushta")),
        ("ekskursiya", ("ekskurs", "экскурс")),
    ]
    includes = [label for label, aliases in include_map if any(alias in lowered for alias in aliases)]
    departure = dates[0]
    return_date = dates[1].isoformat() if len(dates) > 1 and dates[1] >= departure else ""
    route = city or country or "Tur"
    return TourExtraction(
        is_tour=True, title=f"{route} — {departure:%d.%m.%Y}", country=country, city=city,
        price_amount=amount, price_currency=currency, departure_date=departure.isoformat(),
        return_date=return_date, duration_days=duration, nights=nights, includes=includes,
        contact=contact.group(0) if contact else None,
        summary=text[:6000],
        hotel_name=hotel.group(1).strip(" —-*") if hotel else "",
        hotel_stars=int(hotel.group(2)) if hotel and hotel.group(2) else 0,
        meal_plan=meal.group(1).strip() if meal else "",
    )


CITY_ALIASES = {
    "Chengdu": ("чэнду", "chengdu"),
    "Furong": ("фуронг", "furong"),
    "Zhangjiajie": ("чжанцзяцзе", "zhangjiajie"),
    "Leshan": ("лэшань", "leshan"),
    "Guangzhou": ("гуанчжоу", "guangzhou"),
    "Fenghuang": ("фэнхуан", "fenghuang"),
    "Chongqing": ("чунцин", "chongqing"),
    "Guilin": ("гуйлинь", "guilin"),
    "Yangshuo": ("яншо", "yangshuo"),
    "Shenzhen": ("шэньчжэнь", "shenzhen"),
    "Hong Kong": ("гонконг", "hong kong"),
    "Macau": ("макао", "macau"),
    "Sanya": ("санья", "sanya"),
    "Hainan": ("хайнан", "hainan"),
    "Dubai": ("дубай", "dubai", "dubay"),
    "Sharjah": ("шардж", "sharjah", "sharja"),
    "Antalya": ("анталья", "antalya"),
    "Kemer": ("кемер", "kemer"),
    "Belek": ("белек", "belek"),
    "Bodrum": ("бодрум", "bodrum"),
    "Trabzon": ("трабзон", "trabzon"),
    "Uzungol": ("узунгёль", "узунгёл", "узунгель", "uzungol", "uzungöl"),
    "Sharm el-Sheikh": ("шарм", "sharm"),
    "Hurghada": ("хургада", "hurghada"),
    "Phuket": ("пхукет", "phuket", "pxuket"),
    "Pattaya": ("паттайя", "pattaya"),
    "Batumi": ("батуми", "batumi"),
    "Tbilisi": ("тбилиси", "tbilisi"),
    "Baku": ("баку", "baku", "boku"),
    "Kappadokiya": ("каппадокия", "kappadokiya", "cappadocia"),
    "Istanbul": ("стамбул", "istanbul"),
    "Fukuok": ("фукуок", "fukuok", "phu quoc"),
    "Bali": ("бали", "bali"),
    "Praga": ("прага", "praga", "prague"),
    "Abu-Dabi": ("абу-даби", "абу даби", "abu dhabi"),
    "Almati": ("алматы", "almati", "almaty"),
    "Ganja": ("гянджа", "ganja"),
    "Naftalan": ("нафталан", "naftalan"),
    "Kushadasi": ("кушадасы", "kusadasi", "kuşadası"),
    "Nha Trang": ("нячанг", "nha trang"),
    "Da Nang": ("дананг", "da nang"),
    "Kolombo": ("коломбо", "colombo", "kolombo"),
    "Makka": ("мекка", "макка", "makkah", "makka"),
    "Madina": ("медина", "madina", "medina"),
}


CITY_COUNTRY = {
    "Chengdu": "Xitoy", "Furong": "Xitoy", "Zhangjiajie": "Xitoy", "Leshan": "Xitoy",
    "Guangzhou": "Xitoy", "Fenghuang": "Xitoy", "Chongqing": "Xitoy", "Guilin": "Xitoy",
    "Yangshuo": "Xitoy", "Shenzhen": "Xitoy", "Hong Kong": "Xitoy", "Macau": "Xitoy",
    "Sanya": "Xitoy", "Hainan": "Xitoy", "Dubai": "BAA", "Sharjah": "BAA",
    "Antalya": "Turkiya", "Kemer": "Turkiya", "Belek": "Turkiya", "Bodrum": "Turkiya",
    "Trabzon": "Turkiya", "Uzungol": "Turkiya", "Kappadokiya": "Turkiya", "Istanbul": "Turkiya",
    "Fukuok": "Vetnam", "Bali": "Indoneziya", "Praga": "Chexiya", "Abu-Dabi": "BAA",
    "Almati": "Qozog‘iston", "Ganja": "Ozarbayjon", "Naftalan": "Ozarbayjon",
    "Kushadasi": "Turkiya", "Nha Trang": "Vetnam", "Da Nang": "Vetnam",
    "Kolombo": "Shri-Lanka", "Makka": "Saudiya Arabistoni", "Madina": "Saudiya Arabistoni",
    "Sharm el-Sheikh": "Misr", "Hurghada": "Misr", "Phuket": "Tailand", "Pattaya": "Tailand",
    "Batumi": "Gruziya", "Tbilisi": "Gruziya", "Baku": "Ozarbayjon",
}

COUNTRY_ALIASES = {
    "Turkiya": ("turkiya", "турция", "turkey", "🇹🇷"),
    "BAA": ("baa", "оаэ", "uae", "emirates", "эмираты", "🇦🇪"),
    "Misr": ("misr", "египет", "egypt", "🇪🇬"),
    "Xitoy": ("xitoy", "китай", "china", "🇨🇳"),
    "Tailand": ("tailand", "таиланд", "thailand", "🇹🇭"),
    "Vetnam": ("vetnam", "вьетнам", "vietnam", "🇻🇳"),
    "Gruziya": ("gruziya", "грузия", "georgia", "🇬🇪"),
    "Ozarbayjon": ("ozarbayjon", "азербайджан", "azerbaijan", "🇦🇿"),
    "Maldiv orollari": ("maldiv", "мальдив", "maldives", "🇲🇻"),
    "Shri-Lanka": ("shri-lanka", "sri lanka", "шри-ланка", "🇱🇰"),
    "Saudiya Arabistoni": ("saudiya", "саудовская аравия", "saudi arabia", "🇸🇦"),
    "Indoneziya": ("indoneziya", "индонезия", "indonesia", "🇮🇩"),
    "Qatar": ("qatar", "катар", "🇶🇦"),
    "Gretsiya": ("gretsiya", "греция", "greece", "🇬🇷"),
    "Kipr": ("kipr", "кипр", "cyprus", "🇨🇾"),
    "Fransiya": ("fransiya", "франция", "france", "🇫🇷"),
    "Italiya": ("italiya", "италия", "italy", "🇮🇹"),
    "Ispaniya": ("ispaniya", "испания", "spain", "🇪🇸"),
    "Germaniya": ("germaniya", "германия", "germany", "🇩🇪"),
    "Chexiya": ("chexiya", "чехия", "czech", "🇨🇿"),
    "Vengriya": ("vengriya", "венгрия", "hungary", "🇭🇺"),
    "Qozog‘iston": ("qozog", "казахстан", "kazakhstan", "🇰🇿"),
    "Qirg‘iziston": ("qirg", "киргиз", "kyrgyz", "🇰🇬"),
    "Rossiya": ("rossiya", "россия", "russia", "🇷🇺"),
}


def _clean_heading(value: str) -> str:
    value = re.sub(r"^[^\wА-Яа-яЁё]+", "", value.strip(), flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip(" :-–—")


def _detected_countries(text: str) -> list[str]:
    lowered = text.lower()
    return list(dict.fromkeys(
        country for country, aliases in COUNTRY_ALIASES.items()
        if any(alias in lowered for alias in aliases)
    ))


def _generic_heading_cities(text: str, countries: list[str]) -> list[str]:
    """Lug‘atda bo‘lmagan shaharlarni sarlavha strukturasi orqali ajratadi."""
    cleaned = _clean_heading(text)
    parenthesized = re.findall(r"\(([^()]{2,160})\)", cleaned)
    route_text = parenthesized[-1] if parenthesized else cleaned
    parts = re.split(r"\s*(?:\+|→|➡️?|—|–|\||/|\s-\s)\s*", route_text)
    if len(parts) == 1:
        match = re.search(r"(?:^|\s)(?:в|to|ga)\s+([A-ZА-ЯЁ][A-Za-zА-Яа-яЁё'’ -]{1,45})", cleaned)
        if match:
            parts = [match.group(1)]
        elif re.search(r"\b(?:тур|tour|sayohat|travel)\b", cleaned, re.IGNORECASE):
            parts = [cleaned]
        else:
            parts = []

    country_aliases = {alias.lower() for aliases in COUNTRY_ALIASES.values() for alias in aliases if not alias.startswith("🇦")}
    generic_words = re.compile(
        r"\b(?:тур|туры|tour|путешествие|sayohat|аватар|сокровища|мегаполисы|будущего|"
        r"летний|зимний|яркий|история|культура|эмоции)\b",
        re.IGNORECASE,
    )
    cities: list[str] = []
    for part in parts:
        candidate = re.sub(r"[^A-Za-zА-Яа-яЁё'’ -]", " ", part)
        candidate = generic_words.sub(" ", candidate)
        for alias in country_aliases:
            candidate = re.sub(rf"\b{re.escape(alias)}\b", " ", candidate, flags=re.IGNORECASE)
        candidate = re.sub(r"\s+", " ", candidate).strip(" :-")
        if not candidate or candidate.lower() in country_aliases or len(candidate) > 50:
            continue
        if len(candidate.split()) > 4 or not re.search(r"[A-Za-zА-Яа-яЁё]{3}", candidate):
            continue
        cities.append(candidate.title())
    return list(dict.fromkeys(cities))


def _detected_locations(text: str, fallback_country: str | None, fallback_city: str | None) -> list[tuple[str | None, str | None]]:
    """Matndagi har bir davlat/shaharni alohida karta lokatsiyasiga aylantiradi."""
    lowered = text.lower()
    countries = _detected_countries(text)
    if not countries and fallback_country:
        countries = [fallback_country]

    cities = [name for name, aliases in CITY_ALIASES.items() if any(alias in lowered for alias in aliases)]
    if not cities:
        cities = _generic_heading_cities(text, countries)
    locations: list[tuple[str | None, str | None]] = []
    represented_countries: set[str] = set()

    for index, city in enumerate(cities):
        city_country = CITY_COUNTRY.get(city)
        if city_country:
            country = city_country
        elif len(countries) == 1:
            country = countries[0]
        elif len(countries) == len(cities):
            country = countries[index]
        else:
            country = fallback_country or (countries[0] if countries else None)
        locations.append((country, city))
        if country:
            represented_countries.add(country)
    for country in countries:
        if country not in represented_countries:
            locations.append((country, fallback_city if country == fallback_country and not cities else None))
    if not locations:
        locations.append((fallback_country, fallback_city))
    return list(dict.fromkeys(locations))

def _compact_summary(title: str, departure: date, return_date: date | None, amount: float | None,
                     currency: str | None, hotel: str, meal: str, includes: list[str], contact: str | None) -> str:
    lines = [title, f"Sana: {departure:%d.%m.%Y}" + (f" — {return_date:%d.%m.%Y}" if return_date else "")]
    if amount is not None:
        shown = str(int(amount)) if amount.is_integer() else str(amount)
        lines.append(f"Narx: {shown} {currency or ''}".rstrip())
    if hotel:
        lines.append(f"Mehmonxona: {hotel}")
    if meal:
        lines.append(f"Ovqatlanish: {meal}")
    if includes:
        lines.append("Narxga kiradi: " + ", ".join(includes))
    if contact:
        lines.append(f"Bog‘lanish: {contact}")
    return "\n".join(lines)


def extract_many_rules(text: str, reference_date: date | None = None):
    """Har bir sana/narx va har bir aniqlangan shahar uchun alohida tur yaratadi."""
    from .extractor import TourExtraction

    base = extract_rules(text, reference_date)
    if not base or not base.is_tour:
        return []

    lines = [line.strip() for line in (text or "").splitlines()]
    heading = base.title or "Tur"
    offers: list[tuple[str, date, date | None, float | None, str | None]] = []
    pending_amount, pending_currency = base.price_amount, base.price_currency
    ignored_heading_words = ("стоимость тура включено", "для подробной", "обращаться", "narxga kiradi", "bog'lanish")

    for line in lines:
        if not line:
            continue
        dates = extract_dates(line, reference_date)
        amount, currency = _price(line)
        if dates:
            offer_amount = amount if amount is not None else pending_amount
            offer_currency = currency or pending_currency
            if offer_amount is not None or len(dates) >= 2:
                offers.append((heading, dates[0], dates[1] if len(dates) > 1 else None, offer_amount, offer_currency))
                continue
        if amount is not None:
            pending_amount, pending_currency = amount, currency
            continue
        cleaned = _clean_heading(line)
        lowered = cleaned.lower()
        has_location = any(alias in lowered for aliases in CITY_ALIASES.values() for alias in aliases)
        has_tour_word = any(word in lowered for word in ("тур", "tour", "sayohat", "путешествие"))
        has_route_structure = bool(re.search(r"(?:\+|→|➡️?|—|–|\||/|\s-\s)", cleaned))
        if (3 <= len(cleaned) <= 180 and (has_location or has_tour_word or has_route_structure)
                and not any(word in lowered for word in ignored_heading_words)
                and not re.match(r"^(?:[-•]|🛫|⛩|🍱|🚄|🏮|✉️|📞)", line)):
            heading = cleaned

    if not offers:
        return [base]

    route_cities = list(dict.fromkeys(
        city for package_title, *_ in offers
        for _, city in _detected_locations(package_title, base.country, base.city) if city
    ))
    city_nights: dict[str, int] = {}
    for line in lines:
        lowered = line.lower()
        nights_match = re.search(r"(\d{1,2})\s*(?:ноч(?:ей|и|ь)?|kecha|nights?)", lowered)
        if not nights_match:
            continue
        for city_name in route_cities:
            aliases = CITY_ALIASES.get(city_name, (city_name.lower(),))
            if any(alias in lowered for alias in aliases):
                city_nights[city_name] = int(nights_match.group(1))

    results: list[TourExtraction] = []
    for package_title, departure, returned, amount, currency in offers:
        locations = _detected_locations(package_title, base.country, base.city)
        for country, city in locations:
            route = " · ".join(value for value in (country, city) if value) or package_title
            title = f"{package_title} — {route}"
            duration = (returned - departure).days + 1 if returned and returned >= departure else base.duration_days
            summary = _compact_summary(
                package_title, departure, returned, amount, currency,
                base.hotel_name, base.meal_plan, base.includes, base.contact,
            )
            results.append(TourExtraction(
                is_tour=True, title=title[:256], country=country, city=city,
                price_amount=amount, price_currency=currency,
                departure_date=departure.isoformat(), return_date=returned.isoformat() if returned else "",
                duration_days=duration, nights=max(duration - 1, 0) if duration else base.nights,
                city_nights=city_nights.get(city or "", 0),
                includes=base.includes, contact=base.contact, summary=summary,
                hotel_name=base.hotel_name, hotel_stars=base.hotel_stars,
                meal_plan=base.meal_plan, departure_city=base.departure_city,
                booking_note=base.booking_note,
            ))
    return results


def extract_single_rules(text: str, reference_date: date | None = None):
    """Bitta source message uchun bitta tur; city faqat ishonchli aliasdan olinadi."""
    result = extract_rules(text, reference_date)
    if not result or not result.is_tour:
        return result

    countries = _detected_countries(text)
    if countries:
        result.country = " + ".join(countries[:4])[:128]

    # Default yoki sarlavhadan taxmin qilingan city ishlatilmaydi.
    result.city = None
    lowered = text.lower()
    cities = [name for name, aliases in CITY_ALIASES.items() if any(alias in lowered for alias in aliases)]
    cities = list(dict.fromkeys(cities))
    if cities:
        result.city = " + ".join(cities)[:128]
        result.title = f"{result.city} — {result.departure_date or ''}".strip(" —")
    result.summary = (text or "").strip()[:6000]
    return result
