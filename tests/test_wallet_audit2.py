"""Audit 061 finding F2: make_backup removes stale *.part files (killed mid-backup) but nothing else (offline)."""
import os
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

import wallet_admin


class StalePartFilesTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.data = self.directory.name
        self.db = Path(self.data) / "shop.db"
        self.conn = sqlite3.connect(self.db)
        self.conn.execute("CREATE TABLE t (x)")
        self.conn.commit()
        self.addCleanup(self.conn.close)
        self.backups = Path(wallet_admin.backup_dir(self.data))
        self.backups.mkdir()

    def part(self, name, age_seconds):
        path = self.backups / name
        path.write_bytes(b"partial")
        stamp = time.time() - age_seconds
        os.utime(path, (stamp, stamp))
        return path

    def test_only_stale_part_files_are_removed(self):
        old = self.part("shop-20260101-000000.db.part", 2 * 3600)
        fresh = self.part("shop-20260101-000001.db.part", 60)
        kept = self.backups / "shop-20260101-000002.db"
        kept.write_bytes(b"real backup")
        path = wallet_admin.make_backup(self.conn, self.data, now=datetime(2026, 1, 2, 3, 4, 5))
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())                   # may belong to a backup still being written
        self.assertTrue(kept.exists())
        self.assertEqual(kept.read_bytes(), b"real backup")
        self.assertTrue(self.db.exists())                 # the live database is never touched
        self.assertTrue(os.path.exists(path))

    def test_works_when_there_is_no_backup_dir_yet(self):
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        path = wallet_admin.make_backup(self.conn, other.name, now=datetime(2026, 1, 2, 3, 4, 5))
        self.assertTrue(os.path.exists(path))

    def test_unrelated_files_with_other_names_are_left_alone(self):
        stranger = self.part("notes.part", 5 * 3600)
        wallet_admin.make_backup(self.conn, self.data, now=datetime(2026, 1, 2, 3, 4, 5))
        self.assertTrue(stranger.exists())


if __name__ == "__main__":
    unittest.main()
