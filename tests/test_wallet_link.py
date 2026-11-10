"""Task 052: "Ví của tôi" - link Telegram to the web account, view balance (read only).

The web side is a local aiohttp server on 127.0.0.1 that RE-COMPUTES the HMAC signature of every request, so the
signing is really exercised; nothing reaches the real web, Telegram or any other service."""
import hashlib
import hmac
import json
import logging
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiohttp import web

import shop_bot
from utils.wolfmod_api import sign_request

SECRET = "fixture-wallet-secret-at-least-32-characters"
CODE = "ABCD2345"
BAL = 1234567


def chat(kind="private"):
    return SimpleNamespace(type=kind, id=1)


def make_query(data, kind="private"):
    message = SimpleNamespace(chat=chat(kind), chat_id=1, message_id=2, photo=None, reply_photo=AsyncMock(),
                              reply_text=AsyncMock(), delete=AsyncMock())
    return SimpleNamespace(data=data, answer=AsyncMock(), message=message)


def make_update(query=None, text=None, kind="private", user_id=555, username="minh_le", delete_raises=False):
    user = SimpleNamespace(id=user_id, username=username, first_name="Minh")
    send = AsyncMock()
    message = None
    if text is not None:
        message = SimpleNamespace(chat=chat(kind), text=text, reply_text=AsyncMock(),
                                  delete=AsyncMock(side_effect=Exception("cannot delete") if delete_raises else None))
    return SimpleNamespace(callback_query=query, message=message, effective_user=user,
                           effective_chat=SimpleNamespace(id=1, type=kind, send_message=send))


class WalletTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        settings = patch.multiple(shop_bot, DATA_DIR=self.directory.name,
                                  DB_PATH=str(Path(self.directory.name) / "shop_database.db"),
                                  CLAIMED_LINKS_FILE=str(Path(self.directory.name) / "claimed_links.txt"))
        settings.start()
        self.addCleanup(settings.stop)
        with patch.dict(os.environ, {"USDT_WALLET": ""}):
            shop_bot.init_database()
        shop_bot.AWAITING_WALLET_CODE.clear()
        self.seen = []                    # (method, raw_path, body dict, signature_ok)
        self.replies = {}                 # (method, path) -> (status, json)
        self.nonces = set()

        async def handler(request):
            raw = await request.text()
            expected = sign_request(SECRET, request.method, request.raw_path, request.headers.get("X-Bot-Timestamp", ""), raw)
            ok = hmac.compare_digest(request.headers.get("X-Bot-Signature", ""), expected)
            nonce = request.query.get("nonce")
            replay = (not nonce) or nonce in self.nonces
            self.nonces.add(nonce)
            self.seen.append((request.method, request.path, json.loads(raw) if raw else {}, ok and not replay, dict(request.query)))
            if not ok or replay:
                return web.json_response({"error": "unauthorized"}, status=401)
            status, body = self.replies.get((request.method, request.path), (404, {"error": "not_found"}))
            return web.json_response(body, status=status)

        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", handler)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        env = patch.dict(os.environ, {"WALLET_ENABLED": "1", "WOLFMOD_API_SECRET": SECRET,
                                      "WOLFMOD_API_URL": f"http://127.0.0.1:{port}"})
        env.start()
        self.addCleanup(env.stop)
        self.addAsyncCleanup(self.runner.cleanup)
        shop_bot.set_user_lang(555, "vi")

    def reply(self, method, path, status, body):
        self.replies[(method, path)] = (status, body)

    async def click(self, data, kind="private", lang="vi"):
        shop_bot.set_user_lang(555, lang)
        query = make_query(data, kind)
        with patch.object(shop_bot, "safe_edit_or_reply", new_callable=AsyncMock) as render, \
             patch.object(shop_bot, "refresh_catalogue"):
            await shop_bot.callback_handler(make_update(query), SimpleNamespace())
        return query, render

    @staticmethod
    def markup_info(markup):
        return [[(b.text, b.callback_data, b.url) for b in row] for row in markup.inline_keyboard]

    # ---------------- screens ----------------
    async def test_not_linked_shows_three_step_guide_with_profile_url_and_code_button_in_both_languages(self):
        self.reply("GET", "/api/tg/wallet", 200, {"linked": False})
        for lang, step in (("vi", "Liên kết Telegram"), ("en", "Link Telegram")):
            _, render = await self.click("wallet", lang=lang)
            text = render.await_args.args[1]
            self.assertIn("1)", text); self.assertIn("2)", text); self.assertIn("3)", text)
            self.assertIn(step, text)
            rows = self.markup_info(render.await_args.kwargs["reply_markup"])
            urls = [u for row in rows for _, _, u in row if u]
            datas = [d for row in rows for _, d, _ in row if d]
            self.assertEqual(urls, ["https://www.wolfmod.xyz/profile"])
            self.assertEqual(datas, ["wallet_code", "main_menu"])

    async def test_linked_shows_balance_masked_name_topup_unlink_and_no_pay_with_balance(self):
        self.reply("GET", "/api/tg/wallet", 200, {"linked": True, "balance_vnd": BAL, "display_name_masked": "Le Don***"})
        for lang, label in (("vi", "Số dư"), ("en", "Balance")):
            _, render = await self.click("wallet", lang=lang)
            text = render.await_args.args[1]
            self.assertIn("1,234,567 VNĐ", text); self.assertIn("Le Don***", text); self.assertIn(label, text)
            rows = self.markup_info(render.await_args.kwargs["reply_markup"])
            self.assertEqual([u for row in rows for _, _, u in row if u], ["https://www.wolfmod.xyz/profile"])
            datas = [d for row in rows for _, d, _ in row if d]
            self.assertEqual(datas, ["wallet_unlink", "main_menu"])      # nothing that spends the balance
        self.assertEqual([call[0] for call in self.seen], ["GET", "GET"])  # read live every time, never cached

    async def test_balance_is_fetched_again_each_time_not_cached(self):
        self.reply("GET", "/api/tg/wallet", 200, {"linked": True, "balance_vnd": 100, "display_name_masked": "A***"})
        _, first = await self.click("wallet")
        self.reply("GET", "/api/tg/wallet", 200, {"linked": True, "balance_vnd": 999, "display_name_masked": "A***"})
        _, second = await self.click("wallet")
        self.assertIn("100 VNĐ", first.await_args.args[1]); self.assertIn("999 VNĐ", second.await_args.args[1])

    async def test_web_errors_or_garbage_show_friendly_unavailable_in_both_languages(self):
        for status, body in ((503, {"error": "tg_wallet_disabled"}), (500, {}), (401, {"error": "unauthorized"}),
                             (200, {"linked": "yes"}), (200, {"linked": True, "balance_vnd": -5}), (200, {"linked": True, "balance_vnd": "12"})):
            self.reply("GET", "/api/tg/wallet", status, body)
            for lang, phrase in (("vi", "chưa mở"), ("en", "not open yet")):
                _, render = await self.click("wallet", lang=lang)
                self.assertIn(phrase, render.await_args.args[1], (status, body))

    async def test_missing_secret_means_unavailable_and_no_request(self):
        with patch.dict(os.environ, {"WOLFMOD_API_SECRET": ""}):
            _, render = await self.click("wallet", lang="en")
        self.assertIn("not open yet", render.await_args.args[1])
        self.assertEqual(self.seen, [])

    async def test_flag_off_wallet_callbacks_do_nothing_and_call_nothing(self):
        with patch.dict(os.environ, {"WALLET_ENABLED": "0"}):
            for data in ("wallet", "wallet_code", "wallet_unlink", "wallet_unlink_yes"):
                _, render = await self.click(data)
                self.assertIn("chưa mở", render.await_args.args[1])
        self.assertEqual(self.seen, [])
        self.assertEqual(shop_bot.AWAITING_WALLET_CODE, {})

    # ---------------- entering the code ----------------
    async def test_correct_code_links_signs_the_request_deletes_the_message_and_confirms_without_balance(self):
        self.reply("POST", "/api/tg/wallet/link", 200, {"linked": True, "display_name_masked": "Le Don***"})
        query, _ = await self.click("wallet_code")
        query.message.reply_text.assert_awaited_once()
        self.assertIn(555, shop_bot.AWAITING_WALLET_CODE)
        update = make_update(text=" abcd-2345 ")                     # lower case + stray separators are accepted
        with patch.object(shop_bot, "refresh_catalogue"):
            await shop_bot.handle_user_text_message(update, SimpleNamespace())
        method, path, body, signature_ok, qs = self.seen[-1]
        self.assertEqual((method, path), ("POST", "/api/tg/wallet/link"))
        self.assertTrue(signature_ok)                                  # server recomputed the HMAC and it matched
        self.assertEqual(body, {"tg_user_id": 555, "code": CODE, "tg_username": "minh_le"})   # id from the update, code upper-cased
        update.message.delete.assert_awaited_once()
        text = update.effective_chat.send_message.await_args.args[0]
        self.assertIn("Le Don***", text)
        self.assertNotIn("1,234,567", text); self.assertNotIn("VNĐ", text); self.assertNotIn(CODE, text)
        self.assertNotIn(555, shop_bot.AWAITING_WALLET_CODE)

    async def test_signature_covers_query_and_nonce_and_replay_is_rejected(self):
        self.reply("GET", "/api/tg/wallet", 200, {"linked": False})
        await shop_bot.wallet_request("GET", "/api/tg/wallet", params={"tg_user_id": 555})
        await shop_bot.wallet_request("GET", "/api/tg/wallet", params={"tg_user_id": 555})
        self.assertTrue(all(call[3] for call in self.seen))
        self.assertEqual(len({call[4]["nonce"] for call in self.seen}), 2)
        self.assertEqual([call[4]["tg_user_id"] for call in self.seen], ["555", "555"])

    async def test_each_error_code_has_a_friendly_message_in_vi_and_en(self):
        cases = [
            (404, {"error": "invalid_or_expired_code"}, "sai hoặc đã hết hạn", "wrong or has expired", True),
            (400, {"error": "invalid_input"}, "sai hoặc đã hết hạn", "wrong or has expired", True),
            (409, {"error": "telegram_already_linked"}, "Telegram này đã được liên kết", "Telegram is already linked", False),
            (409, {"error": "account_already_linked"}, "Tài khoản web này đã liên kết", "web account is already linked", False),
            (429, {"error": "too_many_attempts", "retry_after": 125}, "khoảng 3 phút", "about 3 minutes", False),
            (503, {"error": "tg_wallet_disabled"}, "chưa mở", "not open yet", True),
            (503, {"error": "tg_wallet_unavailable"}, "chưa mở", "not open yet", True),
            (401, {"error": "unauthorized"}, "chưa mở", "not open yet", True),
        ]
        for status, body, vi, en, keeps_waiting in cases:
            for lang, phrase in (("vi", vi), ("en", en)):
                shop_bot.set_user_lang(555, lang)
                self.reply("POST", "/api/tg/wallet/link", status, body)
                shop_bot.AWAITING_WALLET_CODE[555] = {"until": 1e12, "bad": 0}
                update = make_update(text=CODE)
                await shop_bot.handle_wallet_code_message(update, SimpleNamespace())
                self.assertIn(phrase, update.effective_chat.send_message.await_args.args[0], (status, body, lang))
                self.assertEqual(555 in shop_bot.AWAITING_WALLET_CODE, keeps_waiting, (status, body))
                update.message.delete.assert_awaited_once()               # the code message is removed in every case

    async def test_bad_format_never_calls_the_web_and_stops_after_five(self):
        shop_bot.AWAITING_WALLET_CODE[555] = {"until": 1e12, "bad": 0}
        for text in ("hello", "1234567", "123456789", "ABCD-23!5", "ABCD 23"):
            update = make_update(text=text)
            self.assertTrue(await shop_bot.handle_wallet_code_message(update, SimpleNamespace()))
        self.assertEqual(self.seen, [])
        self.assertNotIn(555, shop_bot.AWAITING_WALLET_CODE)             # 5th bad input ends the wait
        self.assertIn("Đã dừng", update.message.reply_text.await_args.args[0])

    async def test_failed_delete_does_not_break_the_flow(self):
        self.reply("POST", "/api/tg/wallet/link", 200, {"linked": True, "display_name_masked": "X***"})
        shop_bot.AWAITING_WALLET_CODE[555] = {"until": 1e12, "bad": 0}
        update = make_update(text=CODE, delete_raises=True)
        self.assertTrue(await shop_bot.handle_wallet_code_message(update, SimpleNamespace()))
        self.assertIn("X***", update.effective_chat.send_message.await_args.args[0])

    async def test_group_chats_are_ignored(self):
        query, _ = await self.click("wallet", kind="supergroup")
        query.answer.assert_awaited_with(shop_bot.t("vi", "wallet_private_only"), show_alert=True)
        shop_bot.AWAITING_WALLET_CODE[555] = {"until": 1e12, "bad": 0}
        update = make_update(text=CODE, kind="supergroup")
        with patch.object(shop_bot, "refresh_catalogue"):
            await shop_bot.handle_user_text_message(update, SimpleNamespace())
        self.assertEqual(self.seen, [])
        update.message.delete.assert_not_awaited()
        update.effective_chat.send_message.assert_not_awaited()

    async def test_expired_wait_is_ignored(self):
        shop_bot.AWAITING_WALLET_CODE[555] = {"until": 0.0, "bad": 0}
        update = make_update(text=CODE)
        self.assertFalse(await shop_bot.handle_wallet_code_message(update, SimpleNamespace()))
        self.assertEqual(self.seen, [])

    # ---------------- unlink ----------------
    async def test_unlink_needs_confirmation_then_notifies_with_masked_name(self):
        self.reply("GET", "/api/tg/wallet", 200, {"linked": True, "balance_vnd": BAL, "display_name_masked": "Le Don***"})
        _, ask = await self.click("wallet_unlink")
        self.assertIn("Le Don***", ask.await_args.args[1])
        self.assertEqual([d for row in self.markup_info(ask.await_args.kwargs["reply_markup"]) for _, d, _ in row], ["wallet_unlink_yes", "wallet"])
        self.assertEqual([c[0] for c in self.seen if c[0] == "POST"], [])   # nothing unlinked yet
        self.reply("POST", "/api/tg/wallet/unlink", 200, {"ok": True, "display_name_masked": "Le Don***"})
        _, done = await self.click("wallet_unlink_yes", lang="en")
        text = done.await_args.args[1]
        self.assertIn("unlinked", text); self.assertIn("Le Don***", text); self.assertNotIn("1,234,567", text)
        method, path, body, ok, _ = self.seen[-1]
        self.assertEqual((method, path, body, ok), ("POST", "/api/tg/wallet/unlink", {"tg_user_id": 555}, True))

    async def test_unlink_when_not_linked_and_on_web_failure(self):
        self.reply("POST", "/api/tg/wallet/unlink", 404, {"error": "not_linked"})
        _, render = await self.click("wallet_unlink_yes")
        self.assertIn("chưa liên kết", render.await_args.args[1])
        self.reply("POST", "/api/tg/wallet/unlink", 503, {"error": "tg_wallet_disabled"})
        _, render = await self.click("wallet_unlink_yes")
        self.assertIn("chưa mở", render.await_args.args[1])

    # ---------------- privacy ----------------
    async def test_nothing_sensitive_is_logged(self):
        self.reply("POST", "/api/tg/wallet/link", 200, {"linked": True, "display_name_masked": "Le Don***"})
        self.reply("GET", "/api/tg/wallet", 200, {"linked": True, "balance_vnd": BAL, "display_name_masked": "Le Don***"})
        shop_bot.AWAITING_WALLET_CODE[555] = {"until": 1e12, "bad": 0}
        with self.assertLogs(level=logging.DEBUG) as captured:
            logging.getLogger("probe").debug("start")                     # assertLogs needs at least one record
            await shop_bot.handle_wallet_code_message(make_update(text=CODE), SimpleNamespace())
            await self.click("wallet")
            await shop_bot.wallet_request("GET", "/api/tg/wallet", params={"tg_user_id": 555})
            with patch.dict(os.environ, {"WOLFMOD_API_URL": "http://127.0.0.1:1"}):       # network failure path logs too
                await shop_bot.wallet_request("POST", "/api/tg/wallet/link", body={"tg_user_id": 555, "code": CODE})
        output = "\n".join(captured.output)
        for secret in (CODE, str(BAL), "1,234,567", SECRET, "sha256="):
            self.assertNotIn(secret, output)

    async def test_wallet_routes_only_are_allowed_for_signed_client(self):
        from utils.wolfmod_api import WolfmodAPI
        async with WolfmodAPI("http://127.0.0.1:1", SECRET) as api:
            for bad in ("/api/tg/walletx", "/api/tg/orders/bep20", "/api/admin/x", "/api/tg/wallet?tg_user_id=1"):
                with self.assertRaises(ValueError):
                    await api.request("GET", bad)


if __name__ == "__main__":
    unittest.main()
