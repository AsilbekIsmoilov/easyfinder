import unittest
from datetime import date

# Bu fayl bepul rule-based parserni sinaydi va uni to'g'ridan-to'g'ri chaqiradi.
# app.extractor.extract_many orqali chaqirilsa Claude'ga so'rov ketadi — testlar
# sekinlashadi va har yurishda pul sarflanadi.
from app.strict_extractor import extract_strict_many as extract_many
from app.strict_extractor import select_tour_price


class StrictExtractorTests(unittest.TestCase):
    def setUp(self):
        self.reference = date(2026, 7, 24)

    def test_hotel_extra_is_not_tour_price(self):
        text = """Antalya tour
Дата вылета: 05.08.2026-12.08.2026
Mehmonxona uchun qo‘shimcha 6$
Tur narxi: 1235$
Питание: HB"""
        values = extract_many(text, self.reference)
        self.assertEqual(len(values), 1)
        self.assertEqual(values[0].price_amount, 1235)
        self.assertEqual(values[0].meal_plan, "HB")
        self.assertEqual(values[0].duration_days, 8)

    def test_only_extra_price_leaves_tour_price_empty(self):
        text = """Dubai tour
Вылет: 10.08.2026-17.08.2026
Экскурсия 30$
Hotel city tax 6$"""
        values = extract_many(text, self.reference)
        self.assertEqual(len(values), 1)
        self.assertIsNone(values[0].price_amount)
        self.assertEqual(values[0].meal_plan, "")

    def test_independent_destinations_become_separate_tours(self):
        text = """Antalya tour
05.08.2026-12.08.2026 / 900$
Dubai tour
10.08.2026-17.08.2026 / 700$"""
        values = extract_many(text, self.reference)
        self.assertEqual(len(values), 2)
        self.assertEqual({item.country for item in values}, {"Turkiya", "BAA"})

    def test_price_on_separate_line_stays_with_its_offer(self):
        text = """Antalya tour
Вылет: 05.08.2026-12.08.2026
Цена тура: 900$
Dubai tour
Вылет: 10.08.2026-17.08.2026
Цена тура: 700$"""
        values = extract_many(text, self.reference)
        by_country = {item.country: item for item in values}
        self.assertEqual(by_country["Turkiya"].price_amount, 900)
        self.assertEqual(by_country["BAA"].price_amount, 700)
    def test_multi_country_route_stays_one_tour(self):
        text = """Istanbul + Tbilisi + Baku tour
Вылет: 10.08.2026-18.08.2026
Цена тура: 1200$
Питание: BB"""
        values = extract_many(text, self.reference)
        self.assertEqual(len(values), 1)
        self.assertIn("Turkiya", values[0].country)
        self.assertIn("Gruziya", values[0].country)
        self.assertIn("Ozarbayjon", values[0].country)

    def test_duration_conflict_blocks_publish(self):
        text = """Antalya tour
Вылет: 05.08.2026-12.08.2026
10 дней
Цена тура: 900$"""
        value = extract_many(text, self.reference)[0]
        self.assertFalse(value.publishable)
        self.assertIn("duration_conflict", value.validation_errors)


    def test_named_month_day_list_does_not_turn_into_date_range(self):
        text = (
            "\u0410\u043d\u0442\u0430\u043b\u0438\u044f 11-12-13 \u0438\u044e\u043b\u044c \u0443\u0447\u0438\u0448\u0433\u0430 \u043a\u0430\u0442\u0442\u0430 \u0447\u0435\u0433\u0438\u0440\u043c\u0430\u043b\u0430\u0440\n"
            "\u0411\u0438\u043b\u0435\u0442 \u0443\u0437\u0438\u043d\u0438 199\u20ac \u043e\u043b\u0438\u0448\u0438\u043d\u0433\u0438\u0437 \u043c\u0443\u043c\u043a\u0443\u043d. \u0422\u0443\u0440 \u043f\u0430\u043a\u0435\u0442\u043b\u0430\u0440 \u0445\u0430\u043c \u043d\u0430\u0440\u0445\u0438 \u0430\u043d\u0447\u0430 \u0442\u0443\u0448\u0433\u0430\u043d!\n"
            "11-12-13 \u0438\u044e\u043b\u044c"
        )
        values = extract_many(text, date(2026, 6, 1))
        self.assertEqual(
            [value.departure_date for value in values],
            ["2026-07-11", "2026-07-12", "2026-07-13"],
        )
        self.assertTrue(all(value.return_date == "" for value in values))
        self.assertTrue(all(value.duration_days is None for value in values))
        self.assertTrue(all(value.price_amount is None for value in values))


if __name__ == "__main__":
    unittest.main()