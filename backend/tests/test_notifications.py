import unittest
from unittest.mock import patch

from app.notifications import handle_bot_update, statistics_message


class NotificationTests(unittest.TestCase):
    def test_statistics_message_contains_required_sections(self):
        text = statistics_message(reason="pipeline", scraped=12, processed=10, created=8)
        self.assertIn("Faol turlar", text)
        self.assertIn("Davlatlar bo‘yicha", text)
        self.assertIn("Eng arzon taklif", text)
        self.assertLess(len(text), 4096)

    @patch("app.notifications.send_statistics")
    @patch("app.notifications.subscribe")
    def test_start_subscribes_and_sends_statistics(self, subscribe, send):
        handle_bot_update({"message": {"text": "/start", "chat": {"id": 123}, "from": {"first_name": "Ali", "username": "ali"}}})
        subscribe.assert_called_once_with("123", "Ali", "ali")
        send.assert_called_once_with("123", reason="start")


if __name__ == "__main__":
    unittest.main()