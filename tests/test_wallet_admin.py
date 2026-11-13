"""Task 058 PR 4: admin wallet commands + database backups (offline)."""
import asyncio
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import shop_bot
import wallet_admin as wa
import wallet_store as ws

ADMIN_ID = 6571486853


class Base(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        settings = patch.multiple(shop_bot, DATA_DIR=self.directory.name, DB_PATH=str(Path(self.directory.name) / "shop.db"),
                                  CLAIMED_LINKS_FILE=str(Path(self.directory.name) / "c.txt"), BOT_TOKEN="test-token",
                                  ADMIN_ID=str(ADMIN_ID))
        settings.start()
        self.addCleanup(settings.stop)
        env = patch.dict(os.environ, {"WALLET_ENABLED": "1", "USDT_WALLET": ""})
        env.start()
        self.addCleanup(env.stop)
        shop_bot.init_database()
        self.conn = shop_bot.get_db_connection()
        self.addCleanup(self.conn.close)

    def balance(self, user=5):
        return ws.get_balance(self.conn, user)

    def ledger_count(self):
        return self.conn.execute("SELECT COUNT(*) FROM wallet_ledger").fetchone()[0]


class AdjustTests(Base):
    def test_add_writes_one_admin_adjust_line_and_returns_it(self):
        line = wa.adjust(self.conn, 5, 1, 50_000, "bù lỗi nạp", ADMIN_ID)
        self.assertEqual((line.kind, line.amount_vnd, line.balance_after, line.note, line.actor),
                         ("admin_adjust", 50_000, 50_000, "bù lỗi nạp", str(ADMIN_ID)))
        self.assertEqual((self.balance(), self.ledger_count(), ws.audit(self.conn)), (50_000, 1, []))

    def test_sub_reduces_and_cannot_go_negative(self):
        wa.adjust(self.conn, 5, 1, 50_000, "x", ADMIN_ID)
        wa.adjust(self.conn, 5, -1, 20_000, "trừ nhầm", ADMIN_ID)
        self.assertEqual(self.balance(), 30_000)
        with self.assertRaises(wa.AdminError) as caught:
            wa.adjust(self.conn, 5, -1, 30_001, "quá tay", ADMIN_ID)
        self.assertEqual(caught.exception.code, "insufficient_funds")
        self.assertEqual((self.balance(), self.ledger_count()), (30_000, 2))
        wa.adjust(self.conn, 5, -1, 30_000, "về 0", ADMIN_ID)
        self.assertEqual(self.balance(), 0)

    def test_reason_is_required(self):
        for reason in ("", "   ", None, "x" * 201):
            with self.assertRaises(wa.AdminError) as caught:
                wa.adjust(self.conn, 5, 1, 10_000, reason, ADMIN_ID)
            self.assertEqual(caught.exception.code, "reason_required")
        self.assertEqual(self.ledger_count(), 0)

    def test_limit_per_operation(self):
        self.assertEqual(wa.parse_amount("2.000.000"), 2_000_000)
        with self.assertRaises(wa.AdminError) as caught:
            wa.parse_amount("2000001")
        self.assertEqual(caught.exception.code, "too_large")
        with self.assertRaises(wa.AdminError):
            wa.adjust(self.conn, 5, 1, 2_000_001, "x", ADMIN_ID)
        self.assertEqual(self.ledger_count(), 0)

    def test_amount_parsing_rejects_garbage(self):
        for text in ("", "0", "-5", "+5", "1.5k", "abc", "10 000", "1e6", None, "١٢٣"):
            with self.assertRaises(wa.AdminError):
                wa.parse_amount(text)

    def test_user_id_parsing(self):
        self.assertEqual(wa.parse_user_id("123456"), 123456)
        for text in ("", "0", "-1", "abc", "12.5", None, "1" * 16):
            with self.assertRaises(wa.AdminError):
                wa.parse_user_id(text)

    def test_ledger_stays_append_only(self):
        line = wa.adjust(self.conn, 5, 1, 10_000, "x", ADMIN_ID)
        with self.assertRaises(sqlite3.DatabaseError):
            self.conn.execute("UPDATE wallet_ledger SET amount_vnd = 1 WHERE id = ?", (line.id,))
        with self.assertRaises(sqlite3.DatabaseError):
            self.conn.execute("DELETE FROM wallet_ledger WHERE id = ?", (line.id,))

    def test_view_shows_balance_and_newest_ten(self):
        for n in range(12):
            wa.adjust(self.conn, 5, 1, 1_000, f"r{n}", ADMIN_ID)
        view = wa.view(self.conn, 5)
        self.assertEqual((view.balance_vnd, len(view.lines)), (12_000, 10))
        self.assertEqual(view.lines[0].note, "r11")
        self.assertEqual(wa.view(self.conn, 99).lines, [])

    def test_view_is_read_only(self):
        wa.view(self.conn, 99)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM wallet_accounts WHERE user_id = 99").fetchone()[0], 0)


class BackupTests(Base):
    def test_backup_reopens_and_balance_matches(self):
        wa.adjust(self.conn, 5, 1, 70_000, "x", ADMIN_ID)
        path = wa.make_backup(self.conn, self.directory.name)
        self.assertTrue(os.path.isfile(path))
        copy = sqlite3.connect(path)
        try:
            self.assertEqual(copy.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(copy.execute("SELECT balance_vnd FROM wallet_accounts WHERE user_id = 5").fetchone()[0], 70_000)
            self.assertEqual(ws.audit(copy), [])
        finally:
            copy.close()

    def test_keeps_only_the_newest_14(self):
        base = datetime(2026, 1, 1, 0, 0, 0)
        for n in range(20):
            wa.make_backup(self.conn, self.directory.name, now=base + timedelta(hours=6 * n))
        names = [os.path.basename(p) for p in wa.list_backups(self.directory.name)]
        self.assertEqual(len(names), 14)
        self.assertEqual(names[-1], "shop-20260105-180000.db")
        self.assertEqual(names[0], "shop-20260102-120000.db")

    def test_two_backups_in_one_second_do_not_overwrite(self):
        now = datetime(2026, 1, 1, 0, 0, 0)
        first = wa.make_backup(self.conn, self.directory.name, now=now)
        second = wa.make_backup(self.conn, self.directory.name, now=now)
        self.assertNotEqual(first, second)
        self.assertEqual(len(wa.list_backups(self.directory.name)), 2)

    def test_no_partial_file_left_on_failure(self):
        class Broken:
            def backup(self, target):
                raise sqlite3.OperationalError("disk full")

        with self.assertRaises(sqlite3.OperationalError):
            wa.make_backup(Broken(), self.directory.name)
        self.assertEqual(os.listdir(wa.backup_dir(self.directory.name)), [])


def message(text, chat="private", user_id=ADMIN_ID, username="someone"):
    sent = SimpleNamespace(replies=[], docs=[])

    async def reply_text(body, **kwargs):
        sent.replies.append(body)

    async def reply_document(document, filename=None, **kwargs):
        sent.docs.append((filename, document.read()))

    sent.message = SimpleNamespace(text=text, chat=SimpleNamespace(type=chat), reply_text=reply_text, reply_document=reply_document)
    sent.update = SimpleNamespace(message=sent.message, effective_user=SimpleNamespace(id=user_id, username=username))
    sent.context = SimpleNamespace(args=text.split()[1:])
    return sent


class CommandTests(Base):
    def run_command(self, handler, text, **kwargs):
        sent = message(text, **kwargs)
        asyncio.run(handler(sent.update, sent.context))
        return sent

    def test_add_then_show(self):
        sent = self.run_command(shop_bot.admin_wallet_add, "/wallet_add 5 50000 bù lỗi nạp")
        self.assertIn("Đã ghi dòng sổ cái", sent.replies[0])
        self.assertIn("bù lỗi nạp", sent.replies[0])
        self.assertEqual(self.balance(), 50_000)
        shown = self.run_command(shop_bot.admin_wallet_command, "/wallet 5")
        self.assertIn("50,000", shown.replies[0])

    def test_sub_beyond_balance_refused(self):
        self.run_command(shop_bot.admin_wallet_add, "/wallet_add 5 10000 a")
        sent = self.run_command(shop_bot.admin_wallet_sub, "/wallet_sub 5 20000 thu hồi")
        self.assertIn("không đủ", sent.replies[0])
        self.assertEqual((self.balance(), self.ledger_count()), (10_000, 1))

    def test_reason_required_and_limit_enforced(self):
        sent = self.run_command(shop_bot.admin_wallet_add, "/wallet_add 5 50000")
        self.assertIn("Lý do là bắt buộc", sent.replies[0])
        sent = self.run_command(shop_bot.admin_wallet_add, "/wallet_add 5 2000001 quá lớn")
        self.assertIn("tối đa", sent.replies[0])
        self.assertEqual((self.balance(), self.ledger_count()), (0, 0))

    def test_non_admin_is_refused_for_every_command(self):
        for handler, text in ((shop_bot.admin_wallet_command, "/wallet 5"), (shop_bot.admin_wallet_add, "/wallet_add 5 1000 x"),
                              (shop_bot.admin_wallet_sub, "/wallet_sub 5 1000 x"), (shop_bot.admin_backup_command, "/backup")):
            sent = self.run_command(handler, text, user_id=111, username="customer")
            self.assertIn("không có quyền", sent.replies[0])
            self.assertEqual(sent.docs, [])
        self.assertEqual(self.ledger_count(), 0)
        self.assertFalse(os.path.exists(wa.backup_dir(self.directory.name)))

    def test_admin_by_username_is_accepted_like_other_admin_commands(self):
        sent = self.run_command(shop_bot.admin_wallet_add, "/wallet_add 5 1000 x", user_id=222, username="wolfmodyt")
        self.assertIn("Đã ghi", sent.replies[0])

    def test_group_chat_is_refused_even_for_admin(self):
        for handler, text in ((shop_bot.admin_wallet_add, "/wallet_add 5 1000 x"), (shop_bot.admin_backup_command, "/backup")):
            sent = self.run_command(handler, text, chat="supergroup")
            self.assertIn("chat riêng", sent.replies[0])
            self.assertEqual(sent.docs, [])
        self.assertEqual(self.ledger_count(), 0)

    def test_wallet_off_refuses_wallet_commands(self):
        with patch.dict(os.environ, {"WALLET_ENABLED": "0"}):
            sent = self.run_command(shop_bot.admin_wallet_add, "/wallet_add 5 1000 x")
        self.assertIn("chưa bật", sent.replies[0])
        self.assertEqual(self.ledger_count(), 0)

    def test_backup_command_sends_a_file_that_reopens(self):
        self.run_command(shop_bot.admin_wallet_add, "/wallet_add 5 70000 x")
        sent = self.run_command(shop_bot.admin_backup_command, "/backup")
        (name, data), = sent.docs
        self.assertRegex(name, r"^shop-\d{8}-\d{6}(-\d+)?\.db$")
        restored = Path(self.directory.name) / "restored.db"
        restored.write_bytes(data)
        copy = sqlite3.connect(str(restored))
        try:
            self.assertEqual(copy.execute("SELECT balance_vnd FROM wallet_accounts WHERE user_id = 5").fetchone()[0], 70_000)
        finally:
            copy.close()

    def test_no_balance_reason_or_amount_in_logs(self):
        with self.assertLogs(shop_bot.logger, level="DEBUG") as logs:
            shop_bot.logger.info("sentinel")
            self.run_command(shop_bot.admin_wallet_add, "/wallet_add 5 123456 lý-do-bí-mật")
            self.run_command(shop_bot.admin_wallet_command, "/wallet 5")
        joined = "\n".join(logs.output)
        for secret in ("123456", "123,456", "lý-do-bí-mật"):
            self.assertNotIn(secret, joined)


class PeriodicBackupTests(Base):
    def test_task_backs_up_when_wallet_on_and_not_when_off(self):
        async def run_once():
            calls = {"n": 0}

            async def fake_sleep(seconds):
                calls["n"] += 1
                if calls["n"] >= 2:
                    raise asyncio.CancelledError

            with patch.object(shop_bot.asyncio, "sleep", fake_sleep):
                with self.assertRaises(asyncio.CancelledError):
                    await shop_bot.wallet_backup_task(SimpleNamespace())

        asyncio.run(run_once())
        self.assertEqual(len(wa.list_backups(self.directory.name)), 1)
        with patch.dict(os.environ, {"WALLET_ENABLED": "0"}):
            asyncio.run(run_once())
        self.assertEqual(len(wa.list_backups(self.directory.name)), 1)

    def test_interval_is_six_hours(self):
        self.assertEqual(wa.BACKUP_INTERVAL_HOURS, 6)
        self.assertEqual(wa.KEEP_BACKUPS, 14)


if __name__ == "__main__":
    unittest.main()
