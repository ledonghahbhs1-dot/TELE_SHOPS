import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import shop_bot

PLUS = "gemini_plus"
PRO = "gemini_pro_account"
NEW = "premium_vpn"  # not in SHOP_SKUS anywhere in the code


def item(sku, name=None, price=50000, in_stock=True, max_qty=1, usdt=2.5, type="item", description="desc"):
    return {"sku": sku, "name": name or sku.upper(), "description": description, "type": type,
            "price_vnd": price, "price_usdt": usdt, "in_stock": in_stock, "max_qty": max_qty}


def payload(*products):
    return {"success": True, "products": list(products)}


def make_query(data):
    message = SimpleNamespace(chat_id=1, message_id=2, photo=None, reply_photo=AsyncMock(),
                              reply_text=AsyncMock(), delete=AsyncMock())
    return SimpleNamespace(data=data, answer=AsyncMock(), message=message)


def make_update(query):
    return SimpleNamespace(callback_query=query,
                           effective_user=SimpleNamespace(id=123, username="fixture", first_name="Fixture"))


def datas(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def texts(markup):
    return [b.text for row in markup.inline_keyboard for b in row]


class AutoDiscoverTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        settings = patch.multiple(shop_bot,
            DATA_DIR=self.directory.name,
            DB_PATH=str(Path(self.directory.name) / "shop_database.db"),
            CLAIMED_LINKS_FILE=str(Path(self.directory.name) / "claimed_links.txt"))
        settings.start()
        self.addCleanup(settings.stop)
        refresh = patch.object(shop_bot, "sync_web_product_price", return_value=True)
        refresh.start()
        self.addCleanup(refresh.stop)
        shop_bot._catalogue.update(items=None, next=0.0)
        shop_bot._web_stock_cache.clear()
        self.addCleanup(lambda: shop_bot._catalogue.update(items=None, next=0.0))
        with patch.dict(os.environ, {"USDT_WALLET": ""}):
            shop_bot.init_database()
        shop_bot.set_setting("default_lang", "vi")  # legacy tests assert the Vietnamese UI

    def web(self, *products):
        return patch.object(shop_bot, "_fetch_catalogue_payload", return_value=payload(*products))

    def stock_everywhere(self, count=5):
        return patch.object(shop_bot, "get_stock_count", return_value=count)

    def orders(self):
        conn = shop_bot.get_db_connection()
        self.addCleanup(conn.close)
        return conn.execute("SELECT * FROM orders").fetchall()

    # ---------- discovery ----------
    def test_new_web_product_appears_in_menu_without_any_code_or_env_change(self):
        self.assertNotIn(NEW, shop_bot.SHOP_SKUS)
        with self.web(item(PLUS), item(NEW, name="Premium VPN 1 Year", price=99000)), self.stock_everywhere():
            self.assertTrue(shop_bot.refresh_catalogue(force=True))
            keyboard = shop_bot.shop_keyboard(1)
        self.assertIn(f"buy_{NEW}", datas(keyboard))
        self.assertTrue(any("Premium VPN 1 Year" in t and "99,000" in t for t in texts(keyboard)))
        self.assertEqual(datas(keyboard)[0], "buy_gemini_plus")

    def test_name_price_description_and_max_qty_come_from_web(self):
        with self.web(item(NEW, name="Premium VPN", price=99000, max_qty=3, usdt=4.5, description="Fast VPN")):
            shop_bot.refresh_catalogue(force=True)
        row = shop_bot.get_product(NEW)
        self.assertEqual((row["name"], row["price"], row["description"], row["max_qty"]), ("Premium VPN", 99000, "Fast VPN", 3))
        self.assertEqual(shop_bot.sku_max_qty(NEW), 3)
        self.assertEqual(shop_bot.get_usdt_price(NEW), 4.5)

    def test_web_changes_update_an_already_stored_product(self):
        # gemini_pro_account is seeded locally (price 125000, max_qty 1); the dashboard is the source of truth.
        with self.web(item(PRO, name="Pro renamed", price=130000, max_qty=4, usdt=6.0, description="new text")):
            shop_bot.refresh_catalogue(force=True)
        row = shop_bot.get_product(PRO)
        self.assertEqual((row["name"], row["price"], row["description"], row["max_qty"]), ("Pro renamed", 130000, "new text", 4))
        self.assertEqual(shop_bot.sku_max_qty(PRO), 4)
        self.assertEqual(shop_bot.get_usdt_price(PRO), 6.0)

    def test_out_of_stock_web_product_is_listed_as_sold_out_and_not_buyable(self):
        with self.web(item(PLUS), item(NEW, in_stock=False)):
            shop_bot.refresh_catalogue(force=True)
            keyboard = shop_bot.shop_keyboard(1)
        self.assertIn(f"soldout_{NEW}", datas(keyboard))
        self.assertNotIn(f"buy_{NEW}", datas(keyboard))
        self.assertTrue(any("HẾT HÀNG" in t for t in texts(keyboard)))

    async def test_discovered_product_detail_screen_checks_real_stock(self):
        with self.web(item(NEW, in_stock=True)):
            shop_bot.refresh_catalogue(force=True)
        query = make_query(f"buy_{NEW}")
        with self.stock_everywhere(0), patch.object(shop_bot, "safe_edit_or_reply", new_callable=AsyncMock) as render:
            await shop_bot.callback_handler(make_update(query), SimpleNamespace())
        self.assertIn("HẾT HÀNG", render.await_args.args[1])
        self.assertEqual(datas(render.await_args.kwargs["reply_markup"]), ["shop"])

    async def test_deactivated_product_disappears_after_next_refresh(self):
        with self.web(item(PLUS), item(NEW)):
            shop_bot.refresh_catalogue(force=True)
        with self.web(item(PLUS)):
            shop_bot.refresh_catalogue(force=True)
        self.assertNotIn(NEW, shop_bot.known_skus())
        with self.stock_everywhere():
            self.assertNotIn(f"buy_{NEW}", datas(shop_bot.shop_keyboard(1)))
        # an old button for it creates no order
        query = make_query(shop_bot.cb_qty("vietqr", NEW, 1))
        with self.stock_everywhere(), patch.object(shop_bot, "safe_edit_or_reply", new_callable=AsyncMock), \
             patch.object(shop_bot, "sync_order_to_web_async", new_callable=AsyncMock) as sync:
            await shop_bot.callback_handler(make_update(query), SimpleNamespace())
        sync.assert_not_awaited()
        self.assertEqual(self.orders(), [])

    # ---------- fail-safe ----------
    def test_api_error_before_any_success_falls_back_to_shop_skus(self):
        with patch.object(shop_bot, "_fetch_catalogue_payload", side_effect=OSError("down")):
            self.assertFalse(shop_bot.refresh_catalogue(force=True))
        self.assertEqual(shop_bot.known_skus(), shop_bot.SHOP_SKUS)
        with self.stock_everywhere():
            self.assertIn(f"buy_{PRO}", datas(shop_bot.shop_keyboard(1)))  # menu is not empty

    def test_api_error_after_success_keeps_the_last_good_catalogue(self):
        with self.web(item(NEW)):
            shop_bot.refresh_catalogue(force=True)
        with patch.object(shop_bot, "_fetch_catalogue_payload", side_effect=OSError("down")):
            self.assertTrue(shop_bot.refresh_catalogue(force=True))
        self.assertIn(NEW, shop_bot.known_skus())

    def test_malformed_payloads_are_treated_as_errors(self):
        for bad in (None, [], {"success": False, "products": []}, {"success": True}, {"success": True, "products": "x"}, "<html>"):
            with patch.object(shop_bot, "_fetch_catalogue_payload", return_value=bad):
                self.assertFalse(shop_bot.refresh_catalogue(force=True), bad)
        self.assertIsNone(shop_bot._catalogue["items"])

    def test_cache_ttl_and_retry_backoff(self):
        with self.web(item(NEW)) as fetch:
            shop_bot.refresh_catalogue(force=True)
            shop_bot.refresh_catalogue()
            shop_bot.refresh_catalogue()
        self.assertEqual(fetch.call_count, 1)
        shop_bot._catalogue["next"] = 0.0
        with patch.object(shop_bot, "_fetch_catalogue_payload", side_effect=OSError("down")) as failing:
            shop_bot.refresh_catalogue()
            shop_bot.refresh_catalogue()  # inside the 10 s backoff: no second request
        self.assertEqual(failing.call_count, 1)

    def test_without_bot_token_no_network_call_is_attempted(self):
        with patch.object(shop_bot, "BOT_TOKEN", ""), patch("urllib.request.urlopen") as urlopen:
            self.assertFalse(shop_bot.refresh_catalogue(force=True))
        urlopen.assert_not_called()

    # ---------- validation ----------
    def test_invalid_skus_and_products_are_dropped_but_valid_ones_survive(self):
        products = [
            item(NEW),
            item("Mixed_Case9"),
            item("bad sku"), item("x"), item("has-hyphen"), item("../etc/passwd"), item("a" * 41), item("semi;colon"),
            item("key_product", type="key"), item("credits_product", type="credits"),
            item("free_item", price=0), item("neg_item", price=-5), item("float_item", price=1.5),
            item("usdt_bad", usdt=0), item("qty_zero", max_qty=0), item("qty_huge", max_qty=51),
            item("stock_str", in_stock="yes"),
            {"sku": "no_name", "type": "item", "price_vnd": 1, "price_usdt": None, "in_stock": True, "max_qty": 1},
            "not a dict", None,
        ]
        with self.web(*products):
            shop_bot.refresh_catalogue(force=True)
        self.assertEqual(sorted(shop_bot._catalogue["items"]), ["Mixed_Case9", NEW])

    def test_catalogue_is_capped_and_callbacks_fit_telegram_limit(self):
        longest = "a" * 40
        with self.web(item(longest), *[item(f"sku_{n:02d}") for n in range(60)]):
            shop_bot.refresh_catalogue(force=True)
        self.assertLessEqual(len(shop_bot._catalogue["items"]), shop_bot.CATALOGUE_MAX_ITEMS)
        for kind in ("select", "vietqr", "usdt"):
            data = shop_bot.cb_qty(kind, longest, 50)
            self.assertLessEqual(len(data.encode()), 64)
            self.assertEqual(shop_bot.parse_sku_qty_callback(data)[1:], (longest, 50))
        self.assertLessEqual(len(f"buy_{longest}".encode()), 64)
        self.assertLessEqual(len(f"soldout_{longest}".encode()), 64)

    # ---------- purchase flow for a discovered product ----------
    async def test_vietqr_order_for_discovered_product_uses_its_sku_and_web_price(self):
        with self.web(item(NEW, price=99000, max_qty=1)):
            shop_bot.refresh_catalogue(force=True)
        query = make_query(shop_bot.cb_qty("vietqr", NEW, 1))
        with self.stock_everywhere(), patch.object(shop_bot, "generate_vietqr_png_bytes", return_value=None), \
             patch.object(shop_bot, "safe_edit_or_reply", new_callable=AsyncMock), \
             patch.object(shop_bot, "sync_order_to_web_async", new_callable=AsyncMock) as sync:
            await shop_bot.callback_handler(make_update(query), SimpleNamespace())
            await asyncio.sleep(0)
        rows = self.orders()
        self.assertEqual([(r["product_id"], r["amount"], r["quantity"]) for r in rows], [(NEW, 99000, 1)])
        self.assertEqual(sync.await_args.kwargs["sku"], NEW)

    async def test_discovered_product_respects_web_max_qty(self):
        with self.web(item(NEW, max_qty=2)):
            shop_bot.refresh_catalogue(force=True)
        query = make_query(shop_bot.cb_qty("vietqr", NEW, 3))
        with self.stock_everywhere(), patch.object(shop_bot, "safe_edit_or_reply", new_callable=AsyncMock), \
             patch.object(shop_bot, "sync_order_to_web_async", new_callable=AsyncMock) as sync:
            await shop_bot.callback_handler(make_update(query), SimpleNamespace())
        sync.assert_not_awaited()
        self.assertEqual(self.orders(), [])

    async def test_plisio_for_discovered_product_sends_its_sku(self):
        with self.web(item(NEW)):
            shop_bot.refresh_catalogue(force=True)
        seen = []

        def invoice(code, _user, qty, sku=PLUS):
            seen.append(sku)
            return {"success": True, "order_code": code, "amount_usdt": 2.5, "qty": qty, "currency": "USDT_BSC",
                    "expires_at": "2099-01-01T00:00:00Z", "invoice_url": "https://plisio.net/invoice/x"}
        user = SimpleNamespace(id=123, username="u", first_name="U")
        query = SimpleNamespace(message=SimpleNamespace(chat_id=1, message_id=2))
        with patch.object(shop_bot, "create_web_plisio_invoice", side_effect=invoice), \
             patch.object(shop_bot, "safe_edit_or_reply", new_callable=AsyncMock):
            await shop_bot.show_plisio_checkout(query, user, 1, sku=NEW)
        self.assertEqual(seen, [NEW])
        self.assertEqual([r["product_id"] for r in self.orders()], [NEW])

    async def test_delivery_of_discovered_product_is_text_only(self):
        with self.web(item(NEW)):
            shop_bot.refresh_catalogue(force=True)
        conn = shop_bot.get_db_connection()
        conn.execute("INSERT INTO orders (order_id,user_id,product_id,amount,status,quantity) VALUES (?,?,?,?,?,?)",
                     ("TGSNEW000001", 123, NEW, 50000, "pending", 1))
        conn.commit()
        conn.close()
        bot = SimpleNamespace(send_message=AsyncMock())
        with patch.object(shop_bot, "claim_stock_from_web", return_value="user@x.com|secret") as claim:
            self.assertTrue(await shop_bot.send_paid_order_delivery(bot, "TGSNEW000001", 123))
        self.assertEqual(claim.call_args.args[0], NEW)
        kwargs = bot.send_message.await_args.kwargs
        self.assertIn("user@x.com|secret", kwargs["text"])
        self.assertTrue(all(getattr(b, "url", None) is None for row in kwargs["reply_markup"].inline_keyboard for b in row))

    # ---------- gemini_plus is untouched ----------
    def test_gemini_plus_menu_button_is_unchanged_even_if_web_changes_its_listing(self):
        with self.web(item(PLUS, name="Renamed on web", price=1), item(NEW)):
            shop_bot.refresh_catalogue(force=True)
        with self.stock_everywhere(7):
            keyboard = shop_bot.shop_keyboard(1)
        first = keyboard.inline_keyboard[0][0]
        self.assertEqual(first.callback_data, "buy_gemini_plus")
        self.assertIn("MUA GOOGLE AI PLUS 12 THÁNG", first.text)
        self.assertEqual(shop_bot.sku_display_name(PLUS), "Google AI Plus (400 GB Cloud)")

    def test_gemini_plus_stays_available_when_web_omits_it(self):
        with self.web(item(NEW)):
            shop_bot.refresh_catalogue(force=True)
        self.assertEqual(shop_bot.known_skus()[0], PLUS)


if __name__ == "__main__":
    unittest.main()
