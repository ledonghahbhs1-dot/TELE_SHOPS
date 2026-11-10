"""Task 056 PR0 + PR1: the bot's own wallet (store, schema, storage guard, screen) and the retired web-link flow."""
import asyncio
import os
import re
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import i18n
import shop_bot
import wallet_store as ws


class StoreBase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = str(Path(self.directory.name) / "wallet.db")
        self.conns = []
        self.addCleanup(self.close_all)
        self.conn = self.connect()

    def connect(self):
        conn = sqlite3.connect(self.db, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        self.conns.append(conn)
        return conn

    def close_all(self):
        for conn in self.conns:
            try:
                conn.close()
            except Exception:
                pass


class StoreTests(StoreBase):
    def test_credit_is_idempotent_for_the_same_reference(self):
        first = ws.credit_topup(self.conn, 1, 50_000, "TGSTOP1")
        second = ws.credit_topup(self.conn, 1, 50_000, "TGSTOP1")
        self.assertTrue(first.applied)
        self.assertFalse(second.applied)
        self.assertEqual(ws.get_balance(self.conn, 1), 50_000)
        self.assertEqual(len(ws.get_history(self.conn, 1)), 1)
        self.assertEqual(second.ledger_id, first.ledger_id)

    def test_twenty_parallel_credits_of_one_reference_count_once(self):
        ws.ensure_schema(self.conn)
        results, errors = [], []

        def worker():
            conn = self.connect()
            try:
                results.append(ws.credit_topup(conn, 7, 100_000, "TGSPARALLEL"))
            except Exception as error:      # noqa: BLE001 - reported below
                errors.append(repr(error))
            finally:
                conn.close()

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(sum(1 for r in results if r.applied), 1)
        self.assertEqual(len(results), 20)
        self.assertEqual(ws.get_balance(self.conn, 7), 100_000)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM wallet_ledger WHERE ref='TGSPARALLEL'").fetchone()[0], 1)
        self.assertEqual(ws.audit(self.conn), [])

    def test_parallel_purchases_cannot_overspend(self):
        ws.credit_topup(self.conn, 8, 100_000, "TGSFUND")
        outcome = []

        def worker(n):
            conn = self.connect()
            try:
                ws.debit_purchase(conn, 8, 60_000, f"ORDER{n}")
                outcome.append("ok")
            except ws.InsufficientFunds:
                outcome.append("no")
            finally:
                conn.close()

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(outcome.count("ok"), 1)
        self.assertEqual(ws.get_balance(self.conn, 8), 40_000)
        self.assertEqual(ws.audit(self.conn), [])

    def test_invariant_balance_equals_sum_of_ledger_after_mixed_operations(self):
        ws.credit_topup(self.conn, 1, 200_000, "T1")
        ws.debit_purchase(self.conn, 1, 70_000, "O1")
        ws.refund(self.conn, 1, 70_000, "O1")
        ws.debit_purchase(self.conn, 1, 20_000, "O2")
        ws.admin_adjust(self.conn, 1, 5_000, "goodwill", 6571486853)
        ws.admin_adjust(self.conn, 1, -3_000, "fix", "admin")
        ws.credit_topup(self.conn, 2, 20_000, "T2")
        self.assertEqual(ws.get_balance(self.conn, 1), 200_000 - 70_000 + 70_000 - 20_000 + 5_000 - 3_000)
        total = self.conn.execute("SELECT SUM(amount_vnd) FROM wallet_ledger WHERE user_id=1").fetchone()[0]
        self.assertEqual(ws.get_balance(self.conn, 1), total)
        self.assertEqual(ws.audit(self.conn), [])
        last = ws.get_history(self.conn, 1)[0]
        self.assertEqual(last["balance_after"], ws.get_balance(self.conn, 1))

    def test_balance_never_goes_negative(self):
        ws.credit_topup(self.conn, 1, 30_000, "T1")
        with self.assertRaises(ws.InsufficientFunds):
            ws.debit_purchase(self.conn, 1, 30_001, "O1")
        with self.assertRaises(ws.InsufficientFunds):
            ws.admin_adjust(self.conn, 1, -30_001, "too much", "admin")
        self.assertEqual(ws.get_balance(self.conn, 1), 30_000)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM wallet_ledger").fetchone()[0], 1)
        # the database itself refuses a negative balance even if code were wrong
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE wallet_accounts SET balance_vnd = -1 WHERE user_id = 1")
        self.conn.rollback()
        with self.assertRaises(ws.InsufficientFunds):
            ws.debit_purchase(self.conn, 99, 20_000, "NOACCOUNT")     # no wallet at all
        self.assertEqual(ws.get_balance(self.conn, 99), 0)

    def test_ledger_is_append_only(self):
        ws.credit_topup(self.conn, 1, 50_000, "T1")
        with self.assertRaises(sqlite3.DatabaseError):
            self.conn.execute("UPDATE wallet_ledger SET amount_vnd = 999999")
        self.conn.rollback()
        with self.assertRaises(sqlite3.DatabaseError):
            self.conn.execute("DELETE FROM wallet_ledger")
        self.conn.rollback()
        self.assertEqual(ws.get_balance(self.conn, 1), 50_000)
        self.assertEqual(len(ws.get_history(self.conn, 1)), 1)

    def test_data_survives_close_and_reopen(self):
        ws.credit_topup(self.conn, 5, 123_000, "T5")
        ws.debit_purchase(self.conn, 5, 23_000, "O5")
        self.conn.close()
        reopened = self.connect()
        self.assertEqual(ws.get_balance(reopened, 5), 100_000)
        self.assertEqual([h["kind"] for h in ws.get_history(reopened, 5)], ["purchase", "topup"])
        self.assertEqual(ws.audit(reopened), [])

    def test_bad_amounts_are_refused_and_change_nothing(self):
        ws.ensure_schema(self.conn)
        for bad in (1.0, 50_000.5, True, False, 0, -5, "50000", None, float("nan")):
            for fn in (lambda a: ws.credit_topup(self.conn, 1, a, "R"), lambda a: ws.debit_purchase(self.conn, 1, a, "R"),
                       lambda a: ws.refund(self.conn, 1, a, "R")):
                with self.assertRaises(ws.InvalidAmount, msg=repr(bad)):
                    fn(bad)
        for bad in (0, True, 1.5, "7", None, 2_000_001, -2_000_001):
            with self.assertRaises(ws.InvalidAmount, msg=repr(bad)):
                ws.admin_adjust(self.conn, 1, bad, "why", "admin")
        self.assertEqual(ws.get_balance(self.conn, 1), 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM wallet_ledger").fetchone()[0], 0)

    def test_bad_user_and_reference_are_refused(self):
        for bad_user in (0, -1, True, "1", None, 1.5):
            with self.assertRaises(ws.WalletError):
                ws.credit_topup(self.conn, bad_user, 20_000, "R")
        for bad_ref in ("", " ", " pad", None, 5, "x" * 65):
            with self.assertRaises(ws.InvalidReference):
                ws.credit_topup(self.conn, 1, 20_000, bad_ref)
        ws.credit_topup(self.conn, 1, 20_000, "SAME")
        with self.assertRaises(ws.InvalidReference):        # same reference, different amount/user: never silently accepted
            ws.credit_topup(self.conn, 1, 30_000, "SAME")
        with self.assertRaises(ws.InvalidReference):
            ws.credit_topup(self.conn, 2, 20_000, "SAME")
        self.assertEqual(ws.get_balance(self.conn, 1), 20_000)

    def test_refund_only_once_and_only_for_a_matching_purchase(self):
        ws.credit_topup(self.conn, 1, 100_000, "T1")
        with self.assertRaises(ws.InvalidReference):
            ws.refund(self.conn, 1, 40_000, "NOPURCHASE")
        ws.debit_purchase(self.conn, 1, 40_000, "O1")
        with self.assertRaises(ws.InvalidReference):
            ws.refund(self.conn, 1, 39_999, "O1")           # partial
        with self.assertRaises(ws.InvalidReference):
            ws.refund(self.conn, 2, 40_000, "O1")           # someone else's order
        self.assertTrue(ws.refund(self.conn, 1, 40_000, "O1").applied)
        self.assertFalse(ws.refund(self.conn, 1, 40_000, "O1").applied)
        self.assertEqual(ws.get_balance(self.conn, 1), 100_000)

    def test_admin_adjust_requires_reason_and_actor(self):
        for reason in ("", "   ", None, "x" * 201):
            with self.assertRaises(ws.WalletError):
                ws.admin_adjust(self.conn, 1, 5_000, reason, "admin")
        for actor in ("", "  ", None):
            with self.assertRaises(ws.WalletError):
                ws.admin_adjust(self.conn, 1, 5_000, "why", actor)
        result = ws.admin_adjust(self.conn, 1, 5_000, "why", 42)
        self.assertTrue(result.applied)
        row = self.conn.execute("SELECT note, actor, ref FROM wallet_ledger").fetchone()
        self.assertEqual((row["note"], row["actor"]), ("why", "42"))
        self.assertTrue(row["ref"].startswith("ADJ"))

    def test_after_hook_runs_in_the_same_transaction(self):
        self.conn.execute("CREATE TABLE marks (v TEXT)")
        self.conn.commit()

        def boom(conn):
            conn.execute("INSERT INTO marks VALUES ('x')")
            raise RuntimeError("fail after credit")

        with self.assertRaises(RuntimeError):
            ws.credit_topup(self.conn, 1, 20_000, "T1", after=boom)
        self.assertEqual(ws.get_balance(self.conn, 1), 0)         # credit rolled back with the hook
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM marks").fetchone()[0], 0)
        ws.credit_topup(self.conn, 1, 20_000, "T1", after=lambda c: c.execute("INSERT INTO marks VALUES ('ok')"))
        ws.credit_topup(self.conn, 1, 20_000, "T1", after=lambda c: c.execute("INSERT INTO marks VALUES ('twice')"))
        self.assertEqual([r[0] for r in self.conn.execute("SELECT v FROM marks")], ["ok"])   # hook not re-run on replay

    def test_topup_limits_and_cap(self):
        self.assertTrue(ws.can_topup(0, 20_000))
        self.assertTrue(ws.can_topup(0, 2_000_000))
        self.assertFalse(ws.can_topup(0, 19_999))
        self.assertFalse(ws.can_topup(0, 2_000_001))
        self.assertTrue(ws.can_topup(3_000_000, 2_000_000))
        self.assertFalse(ws.can_topup(3_000_001, 2_000_000))
        self.assertFalse(ws.can_topup(0, 50_000.0))
        self.assertFalse(ws.can_topup(0, True))

    def test_reading_never_creates_tables_or_accounts(self):
        fresh = sqlite3.connect(str(Path(self.directory.name) / "fresh.db"))
        self.addCleanup(fresh.close)
        self.assertEqual(ws.get_balance(fresh, 1), 0)
        self.assertEqual(ws.get_history(fresh, 1), [])
        self.assertEqual(fresh.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name LIKE 'wallet%'").fetchone()[0], 0)


class StorageGuardTests(unittest.TestCase):
    RAILWAY = {"RAILWAY_ENVIRONMENT": "production"}

    def check(self, env, data_dir):
        keys = ("RAILWAY_ENVIRONMENT", "RAILWAY_ENVIRONMENT_NAME", "RAILWAY_PROJECT_ID", "RAILWAY_SERVICE_ID",
                "RAILWAY_VOLUME_MOUNT_PATH")
        with patch.dict(os.environ, {k: "" for k in keys}):
            with patch.dict(os.environ, env):
                return ws.storage_is_persistent(data_dir)

    def test_off_railway_is_always_fine(self):
        self.assertEqual(self.check({}, "./data"), (True, "not_on_railway"))

    def test_railway_without_a_volume_is_refused(self):
        self.assertEqual(self.check(self.RAILWAY, "/data"), (False, "no_volume_mounted"))

    def test_railway_data_dir_outside_the_volume_is_refused(self):
        env = dict(self.RAILWAY, RAILWAY_VOLUME_MOUNT_PATH="/data")
        self.assertEqual(self.check(env, "/app/data"), (False, "data_dir_outside_volume"))
        self.assertEqual(self.check(env, "/data-other"), (False, "data_dir_outside_volume"))

    def test_railway_data_dir_inside_the_volume_is_accepted(self):
        with tempfile.TemporaryDirectory() as volume:
            inside = os.path.join(volume, "bot")
            os.makedirs(inside)
            env = dict(self.RAILWAY, RAILWAY_VOLUME_MOUNT_PATH=volume)
            self.assertEqual(self.check(env, volume), (True, "inside_volume"))
            self.assertEqual(self.check(env, inside), (True, "inside_volume"))

    def test_wallet_enabled_needs_the_flag_and_a_persistent_volume(self):
        with tempfile.TemporaryDirectory() as data:
            with patch.object(shop_bot, "DATA_DIR", data):
                for flag in ("", "0", "true", "yes", " "):
                    with patch.dict(os.environ, {"WALLET_ENABLED": flag}):
                        self.assertFalse(shop_bot.wallet_enabled(), flag)
                with patch.dict(os.environ, {"WALLET_ENABLED": "1"}):
                    self.assertTrue(shop_bot.wallet_enabled())
                with patch.dict(os.environ, {"WALLET_ENABLED": "1", "RAILWAY_ENVIRONMENT": "production",
                                             "RAILWAY_VOLUME_MOUNT_PATH": ""}):
                    self.assertFalse(shop_bot.wallet_enabled())


class BotIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        settings = patch.multiple(shop_bot, DATA_DIR=self.directory.name,
                                  DB_PATH=str(Path(self.directory.name) / "shop.db"),
                                  CLAIMED_LINKS_FILE=str(Path(self.directory.name) / "c.txt"), BOT_TOKEN="test-token")
        settings.start()
        self.addCleanup(settings.stop)
        env = patch.dict(os.environ, {"WALLET_ENABLED": "1", "USDT_WALLET": ""})
        env.start()
        self.addCleanup(env.stop)
        shop_bot.init_database()
        shop_bot.set_setting("default_lang", "vi")

    def tables(self):
        conn = shop_bot.get_db_connection()
        try:
            return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'wallet%'")}
        finally:
            conn.close()

    def test_connection_pragmas(self):
        conn = shop_bot.get_db_connection()
        try:
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
            self.assertEqual(conn.execute("PRAGMA synchronous").fetchone()[0], 2)        # FULL
            self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], 5000)
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        finally:
            conn.close()

    def test_tables_only_exist_when_the_wallet_is_on(self):
        self.assertEqual(self.tables(), {"wallet_accounts", "wallet_ledger"})
        with tempfile.TemporaryDirectory() as other:
            with patch.object(shop_bot, "DATA_DIR", other), patch.object(shop_bot, "DB_PATH", str(Path(other) / "x.db")), \
                    patch.dict(os.environ, {"WALLET_ENABLED": "0"}):
                shop_bot.init_database()
                conn = shop_bot.get_db_connection()
                try:
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name LIKE 'wallet%'").fetchone()[0], 0)
                finally:
                    conn.close()

    def credit(self, user_id, amount, ref):
        conn = shop_bot.get_db_connection()
        try:
            return ws.credit_topup(conn, user_id, amount, ref)
        finally:
            conn.close()

    def test_screen_shows_balance_and_history_vi_and_en(self):
        self.credit(5, 150_000, "T1")
        conn = shop_bot.get_db_connection()
        try:
            ws.debit_purchase(conn, 5, 50_000, "O1")
        finally:
            conn.close()
        vi, markup = shop_bot.build_wallet_screen(5, "vi")
        en, _ = shop_bot.build_wallet_screen(5, "en")
        self.assertIn("100,000 VNĐ", vi)
        self.assertIn("4.00 USD", vi)
        self.assertIn("Giao dịch gần đây", vi)
        self.assertIn("+150,000 · Nạp tiền", vi)
        self.assertIn("-50,000 · Mua hàng", vi)
        self.assertIn("Recent transactions", en)
        self.assertIn("+150,000 · Top-up", en)
        self.assertIn("-50,000 · Purchase", en)
        self.assertEqual([[b.callback_data for b in row] for row in markup.inline_keyboard], [["main_menu"]])
        empty, _ = shop_bot.build_wallet_screen(6, "en")
        self.assertIn("0 VNĐ", empty)
        self.assertIn("No transactions yet.", empty)
        self.assertEqual(self.tables(), {"wallet_accounts", "wallet_ledger"})       # reading created no account rows
        conn = shop_bot.get_db_connection()
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wallet_accounts WHERE user_id = 6").fetchone()[0], 0)
        finally:
            conn.close()

    def run_view(self, enabled, chat_type="private", lang="vi", user_id=5):
        query = MagicMock()
        query.message = SimpleNamespace(chat=SimpleNamespace(type=chat_type))
        query.answer = AsyncMock()
        edit = AsyncMock()
        with patch.dict(os.environ, {"WALLET_ENABLED": "1" if enabled else "0"}), patch.object(shop_bot, "safe_edit_or_reply", edit):
            asyncio.run(shop_bot.handle_wallet_view(query, SimpleNamespace(id=user_id), lang))
        return query, edit

    def test_callback_private_chat_only_and_flag_off_shows_unavailable(self):
        self.credit(5, 20_000, "T1")
        query, edit = self.run_view(True)
        self.assertIn("20,000 VNĐ", edit.await_args.args[1])
        query, edit = self.run_view(True, chat_type="group")
        edit.assert_not_awaited()
        self.assertTrue(query.answer.await_args.kwargs["show_alert"])
        query, edit = self.run_view(False)
        self.assertNotIn("20,000", edit.await_args.args[1])
        self.assertIn(i18n.t("vi", "wallet_err_unavailable"), edit.await_args.args[1])


class RetiredWebLinkTests(unittest.TestCase):
    def test_old_link_flow_is_gone(self):
        for name in ("wallet_request", "render_wallet_screen", "handle_wallet_callback", "handle_wallet_code_message",
                     "AWAITING_WALLET_CODE", "WALLET_PROFILE_URL", "_WALLET_CODE_RE"):
            self.assertFalse(hasattr(shop_bot, name), name)
        source = Path(shop_bot.__file__).read_text(encoding="utf-8")
        self.assertNotIn("/api/tg/wallet", source)

    def test_old_wallet_i18n_keys_are_gone_and_new_ones_have_vi_and_en(self):
        for key in ("btn_wallet_open_web", "btn_wallet_enter_code", "btn_wallet_balance", "btn_wallet_topup",
                    "btn_wallet_unlink", "btn_wallet_unlink_yes", "btn_wallet_cancel", "wallet_guide", "wallet_balance",
                    "wallet_prompt_code", "wallet_code_bad_format", "wallet_code_stopped", "wallet_code_invalid",
                    "wallet_err_telegram_linked", "wallet_err_account_linked", "wallet_err_too_many", "wallet_linked_ok",
                    "wallet_unlink_ask", "wallet_unlinked_ok", "wallet_not_linked"):
            self.assertNotIn(key, i18n.STRINGS, key)
        for key in ("btn_wallet", "wallet_screen", "wallet_history_empty", "wallet_kind_topup", "wallet_kind_purchase",
                    "wallet_kind_refund", "wallet_kind_admin_adjust", "wallet_err_unavailable", "wallet_private_only"):
            self.assertTrue(i18n.STRINGS[key]["vi"] and i18n.STRINGS[key]["en"], key)

    def test_wolfmod_api_refuses_the_web_wallet_routes_but_keeps_bot_routes(self):
        from utils import wolfmod_api
        self.assertEqual(wolfmod_api.SIGNED_PREFIXES, ("/api/bot/",))
        self.assertEqual(wolfmod_api.SIGNED_EXACT, ())

        async def call(path):
            api = wolfmod_api.WolfmodAPI("https://example.invalid", "s" * 40)
            return await api.request("GET", path)

        for path in ("/api/tg/wallet", "/api/tg/wallet/link", "/api/tg/wallet/unlink"):
            with self.assertRaises(ValueError):
                asyncio.run(call(path))

    def test_secret_name_is_kept_for_the_other_signed_routes(self):
        source = Path(shop_bot.__file__).read_text(encoding="utf-8")
        self.assertNotIn("TG_WALLET", source)


if __name__ == "__main__":
    unittest.main()
