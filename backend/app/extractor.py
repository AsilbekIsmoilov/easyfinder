"""Bepul deterministik tur parserining public interfeysi."""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class TourExtraction(BaseModel):
    is_tour: bool = Field(description="Post haqiqiy tur taklifimi")
    title: str | None = None
    country: str | None = None
    city: str | None = None
    price_amount: float | None = None
    price_currency: str | None = None
    departure_date: str | None = None
    duration_days: int | None = None
    includes: list[str] = Field(default_factory=list)
    contact: str | None = None
    summary: str | None = None
    hotel_name: str = ""
    hotel_stars: int = Field(default=0, ge=0, le=5)
    meal_plan: str = ""
    departure_city: str = ""
    return_date: str = ""
    nights: int = Field(default=0, ge=0)
    city_nights: int = Field(default=0, ge=0)
    booking_note: str = ""
    publishable: bool = True
    validation_errors: list[str] = Field(default_factory=list)


def extract(text: str, reference_date: date | None = None) -> TourExtraction | None:
    values = extract_many(text, reference_date)
    return values[0] if values else None


def extract_many(text: str, reference_date: date | None = None) -> list[TourExtraction]:
    """Bitta manba postidagi mustaqil tur variantlarini qaytaradi.

    Yagona ishlatiladigan parser — Claude. Bepul rule-based parser
    (`strict_extractor` / `rule_extractor`) kodda saqlanadi, lekin bu yerdan
    hech qachon chaqirilmaydi.

    Claude xato bersa istisno yuqoriga uzatiladi: kredit tugashi, kalit xatosi
    yoki tarmoq uzilishi parser muammosi emas, infratuzilma muammosi. Sifati
    past natijani katalogga yozgandan ko'ra postni "retry" deb qoldirgan
    afzal — muammo bartaraf etilgach pipeline uni qayta ishlaydi.
    """
    from .claude_extractor import extract_claude_many

    return extract_claude_many(text, reference_date)