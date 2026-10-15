import os
import unittest
from unittest import mock

import requests

from aubot.notify import TelegramNotifier, find_chat_ids


class FakeResponse:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status

    def json(self):
        return self.body


class FakeSession:
    def __init__(self, response=None, error=None):
        self.response, self.error = response, error
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return self.response

    def get(self, url, **kwargs):
        return self.post(url, **kwargs)


class TelegramNotifierTest(unittest.TestCase):
    def test_send_message(self):
        session = FakeSession(FakeResponse({"ok": True, "result": {}}))
        self.assertTrue(TelegramNotifier("123:ABC", "42", session=session).send("hello"))
        url, kwargs = session.calls[0]
        self.assertEqual(url, "https://api.telegram.org/bot123:ABC/sendMessage")
        self.assertEqual(kwargs["json"], {"chat_id": "42", "text": "hello",
                                          "disable_web_page_preview": True})

    def test_rejected_message_returns_false(self):
        session = FakeSession(FakeResponse({"ok": False, "description": "Bad Request: chat not found"}, 400))
        with self.assertLogs("aubot", "WARNING") as logs:
            self.assertFalse(TelegramNotifier("123:ABC", "42", session=session).send("hi"))
        self.assertIn("chat not found", logs.output[0])

    def test_network_error_is_swallowed_and_token_hidden(self):
        error = requests.ConnectionError("Max retries exceeded with url: /bot123:SECRET/sendMessage")
        notifier = TelegramNotifier("123:SECRET", "42", session=FakeSession(error=error))
        with self.assertLogs("aubot", "WARNING") as logs:
            self.assertFalse(notifier.send("hi"))
        self.assertNotIn("SECRET", logs.output[0])
        self.assertIn("/bot***/sendMessage", logs.output[0])

    def test_long_messages_are_truncated(self):
        session = FakeSession(FakeResponse({"ok": True}))
        TelegramNotifier("t", "1", session=session).send("x" * 5000)
        self.assertEqual(len(session.calls[0][1]["json"]["text"]), 4096)

    def test_from_env(self):
        with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1"}):
            self.assertIsInstance(TelegramNotifier.from_env(), TelegramNotifier)
        with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "t"}, clear=True):
            self.assertIsNone(TelegramNotifier.from_env())


class FindChatIdsTest(unittest.TestCase):
    def test_lists_unique_chats(self):
        body = {"ok": True, "result": [
            {"update_id": 1, "message": {"chat": {"id": 42, "first_name": "Aaliyan", "type": "private"}}},
            {"update_id": 2, "message": {"chat": {"id": 42, "first_name": "Aaliyan", "type": "private"}}},
            {"update_id": 3, "channel_post": {"chat": {"id": -100, "title": "Signals", "type": "channel"}}},
            {"update_id": 4, "my_chat_member": {}},
        ]}
        session = FakeSession(FakeResponse(body))
        self.assertEqual(find_chat_ids("t", session=session), [("42", "Aaliyan"), ("-100", "Signals")])
        self.assertEqual(session.calls[0][0], "https://api.telegram.org/bott/getUpdates")

    def test_network_error_hides_token(self):
        error = requests.ConnectionError("Max retries exceeded with url: /bot123:SECRET/getUpdates")
        with self.assertRaises(RuntimeError) as ctx:
            find_chat_ids("123:SECRET", session=FakeSession(error=error))
        self.assertNotIn("SECRET", str(ctx.exception))
        self.assertIn("Could not reach Telegram", str(ctx.exception))

    def test_bad_token(self):
        session = FakeSession(FakeResponse({"ok": False, "description": "Unauthorized"}, 401))
        with self.assertRaisesRegex(RuntimeError, "Unauthorized"):
            find_chat_ids("bad", session=session)


if __name__ == "__main__":
    unittest.main()
