"""Tests for stats.py. Run from the repo root: python -m unittest discover ship"""
import gzip
import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock

import stats

# Made-up requests in the nginx "stats" format, for two sites and one comments host.
LINES = [
    'example.org [16/Nov/2026:06:00:00 +0000] "GET /post/ HTTP/1.1" 200 5120 "-" "Chrome/150 d" en document',
    'example.org [16/Nov/2026:07:00:00 +0000] "GET /post/ HTTP/1.1" 200 5120 "-" "Firefox/150 m" fi document',
    'comments.example.org [16/Nov/2026:07:00:00 +0000] "POST /new HTTP/1.1" 201 300 "example.org/post/" "Chrome/150 d" en empty',
    'example.net [16/Nov/2026:08:00:00 +0000] "GET / HTTP/1.1" 200 4096 "-" "Chrome/150 d" en document',
]


def kinds(stats_dir):
    db = sqlite3.connect(os.path.join(stats_dir, "stats.db"))
    try:
        return dict(db.execute("SELECT host, group_concat(kind) FROM hits GROUP BY host"))
    finally:
        db.close()


class ImportTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        raw = os.path.join(self.dir, "raw", "prod")
        os.makedirs(raw)
        with gzip.open(os.path.join(raw, "stats-current.log.gz"), "wt", encoding="utf-8") as f:
            f.write("\n".join(LINES) + "\n")

    def run_import(self, comments_host, domain):
        db, _ = stats.run_import(self.dir, comments_host, domain, quiet=True)
        db.close()

    def test_second_run_adds_no_rows(self):
        self.run_import("comments.example.org", "example.org")
        db = sqlite3.connect(os.path.join(self.dir, "stats.db"))
        before = db.execute("SELECT count(*), sum(n) FROM hits").fetchone()
        db.close()
        self.run_import("comments.example.org", "example.org")
        db = sqlite3.connect(os.path.join(self.dir, "stats.db"))
        after = db.execute("SELECT count(*), sum(n) FROM hits").fetchone()
        db.close()
        self.assertEqual(before, (4, 4))
        self.assertEqual(after, before)

    def test_site_without_comments_does_not_classify_all_rows_again(self):
        self.run_import("comments.example.org", "example.org")
        with mock.patch.object(stats, "reclassify", wraps=stats.reclassify) as reclassify:
            self.run_import("", "example.net")
            self.run_import("comments.example.org", "example.org")
        reclassify.assert_not_called()
        self.assertEqual(kinds(self.dir)["comments.example.org"], "comment")

    def test_new_comments_host_classifies_rows_again(self):
        self.run_import("", "example.net")
        self.assertEqual(kinds(self.dir)["comments.example.org"], "other")
        self.run_import("comments.example.org", "example.org")
        self.assertEqual(kinds(self.dir)["comments.example.org"], "comment")

    def test_classify_again_writes_only_changed_rows(self):
        self.run_import("comments.example.org", "example.org")
        db = sqlite3.connect(os.path.join(self.dir, "stats.db"))
        self.addCleanup(db.close)
        db.execute("UPDATE hits SET kind='bot' WHERE host='example.net'")
        changes = db.total_changes
        stats.reclassify(db, "comments.example.org")
        self.assertEqual(db.total_changes - changes, 1)
        self.assertEqual(db.execute("SELECT kind FROM hits WHERE host='example.net'").fetchone(), ("read",))


    def test_httpd_lines_keep_the_hour_and_merge(self):
        raw = os.path.join(self.dir, "raw", "httpd")
        os.makedirs(raw)
        path = os.path.join(raw, "httpd-2024-03.log.gz")
        with gzip.open(path, "wt", encoding="utf-8") as f:
            f.write('example.org 192.0.2.1 - - [10/Mar/2024:06:01:02 +0000] "GET /post/ HTTP/1.1" 200 5120\n'
                    'example.org 192.0.2.2 - - [10/Mar/2024:06:59:59 +0000] "GET /post/ HTTP/1.1" 200 5120\n'
                    'example.org 192.0.2.1 - - [10/Mar/2024:07:00:00 +0000] "GET /post/ HTTP/1.1" 200 5120\n')
        self.run_import("comments.example.org", "example.org")
        db = sqlite3.connect(os.path.join(self.dir, "stats.db"))
        self.addCleanup(db.close)
        rows = db.execute("SELECT ts, n FROM hits WHERE src='httpd' ORDER BY ts").fetchall()
        hour = 1710050400  # 2024-03-10 06:00 UTC
        self.assertEqual(rows, [(hour, 2), (hour + 3600, 1)])
        with gzip.open(path, "rt", encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertEqual(sorted(lines), [
            'example.org - - - [10/Mar/2024:06:00:00 +0000] "GET /post/ HTTP/1.1" 200 5120',
            'example.org - - - [10/Mar/2024:06:00:00 +0000] "GET /post/ HTTP/1.1" 200 5120',
            'example.org - - - [10/Mar/2024:07:00:00 +0000] "GET /post/ HTTP/1.1" 200 5120',
        ])

    def test_new_reductions_keep_a_backup_of_the_database(self):
        self.run_import("comments.example.org", "example.org")
        db = sqlite3.connect(os.path.join(self.dir, "stats.db"))
        db.execute("UPDATE meta SET v='0' WHERE k='anon'")
        db.commit()
        db.close()
        self.run_import("comments.example.org", "example.org")
        bak = sqlite3.connect(os.path.join(self.dir, "stats.db.bak"))
        self.addCleanup(bak.close)
        self.assertEqual(bak.execute("SELECT count(*), sum(n) FROM hits").fetchone(), (4, 4))

class AnonAccessTest(unittest.TestCase):
    def reduce(self, request):
        line = f'192.0.2.1 - - [16/Nov/2026:06:02:48 +0000] "{request}" 200 5120 "-" "Chrome/150 d"'
        return stats.anon_access(stats.Counter({line: 3}), set())

    def test_reducing_twice_gives_the_same_line(self):
        once = self.reduce("GET /post/?q=1 HTTP/1.1")
        self.assertEqual(list(once), ['- - - [16/Nov/2026:06:00:00 +0000] "GET /post/ HTTP/1.1" 200 5120 "-" "Chrome/150 d"'])
        self.assertEqual(stats.anon_access(once, set()), once)

    def test_malformed_request_is_stable_and_keeps_its_count(self):
        once = self.reduce("GET /a b c")
        self.assertEqual(list(once.values()), [3])
        self.assertEqual(stats.anon_access(once, set()), once)


if __name__ == "__main__":
    unittest.main()
