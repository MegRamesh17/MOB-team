"""
Tests for send_expiry_reminders — the daily timer function (Track E) that finds
certificates expiring soon and emails the holder via Resend.

Azure SQL and Resend are both stubbed. What is under test: the query's WHERE clause
picks the right rows (via the fake cursor's fragment match plus explicit param
assertions, same technique test_sqlbank.py uses for placeholder counts), a certificate
gets reminder_sent_at stamped only after a successful send, one recipient's send
failure does not stop the rest of the batch, and an unconfigured Resend key stops the
run cleanly instead of raising the same error N times.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

API = Path(__file__).resolve().parents[1] / "api"
TESTS_DIR = Path(__file__).resolve().parent

sys.path.insert(0, str(TESTS_DIR))
# Reuses test_api_quiz_answer's azure.functions stub rather than re-deriving the set of
# app.* decorators function_app.py happens to use (route/queue_output/queue_trigger/
# timer_trigger, and whatever the next one adds) a second time in a second file.
from test_api_quiz_answer import _install_azure_stubs  # noqa: E402

_install_azure_stubs()
sys.path.insert(0, str(API))

import function_app  # noqa: E402


class Row(dict):
    """Attribute access, matching a pyodbc row."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


class FakeCursor:
    def __init__(self, rows):
        self._candidates = [Row(r) for r in rows]
        self.executed = []   # (sql, params)
        self.updated_ids = []
        self._result = []

    def execute(self, sql, *params):
        flat = list(params[0]) if len(params) == 1 and isinstance(params[0], (list, tuple)) else list(params)
        s = " ".join(sql.split())
        self.executed.append((s, flat))
        if "FROM dbo.Certificates cert" in s:
            self._result = self._candidates
        elif "UPDATE dbo.Certificates SET reminder_sent_at" in s:
            self.updated_ids.append(flat[0])
        return self

    def fetchall(self):
        return list(self._result)


class FakeConn:
    def __init__(self, rows):
        self.cursor_obj = FakeCursor(rows)
        self.commits = 0

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.commits += 1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _row(id=1, doc_title="Workplace Safety", expires_at="2026-09-01",
         email="ethan.brooks@demo.com", employee_name="Ethan Brooks",
         company_name="Quizrant", notifications_enabled=True):
    return dict(id=id, doc_title=doc_title, expires_at=expires_at, email=email,
                employee_name=employee_name, company_name=company_name,
                notifications_enabled=notifications_enabled)


class TestSendExpiryReminders(unittest.TestCase):
    def _patch_conn(self, rows):
        conn = FakeConn(rows)
        original = function_app._conn
        function_app._conn = lambda: conn
        self.addCleanup(lambda: setattr(function_app, "_conn", original))
        return conn

    def test_query_filters_active_unreminded_within_window(self):
        # Nothing about the fake filters rows -- it hands back whatever the test gives
        # it. What's actually checked here is that the WHERE clause the function sends
        # names the three conditions the docstring promises, not that SQL Server would
        # evaluate them correctly (test_sql_parses.py covers real T-SQL syntax
        # elsewhere; this only has migrations to check, not inline query strings).
        conn = self._patch_conn([_row()])
        with patch("shared.comms.send_expiry_email") as send:
            function_app.send_expiry_reminders(None)

        sql, params = conn.cursor_obj.executed[0]
        self.assertIn("cert.status = 'Active'", sql)
        self.assertIn("cert.reminder_sent_at IS NULL", sql)
        self.assertIn("BETWEEN SYSUTCDATETIME()", sql)
        self.assertEqual(params, [30], "EXPIRY_WARNING_DAYS should default to 30")
        send.assert_called_once_with(
            "ethan.brooks@demo.com", "Ethan Brooks", "Workplace Safety",
            "2026-09-01", "Quizrant")

    def test_successful_send_stamps_reminder_sent_at(self):
        conn = self._patch_conn([_row(id=42)])
        with patch("shared.comms.send_expiry_email"):
            function_app.send_expiry_reminders(None)
        self.assertEqual(conn.cursor_obj.updated_ids, [42])
        self.assertEqual(conn.commits, 1)

    def test_no_candidates_sends_nothing(self):
        conn = self._patch_conn([])
        with patch("shared.comms.send_expiry_email") as send:
            function_app.send_expiry_reminders(None)
        send.assert_not_called()
        self.assertEqual(conn.cursor_obj.updated_ids, [])

    def test_one_failed_send_does_not_block_the_rest_of_the_batch(self):
        conn = self._patch_conn([_row(id=1), _row(id=2, email="maya.osei@demo.com")])
        with patch("shared.comms.send_expiry_email") as send:
            send.side_effect = [RuntimeError("Resend rejected the address"), None]
            function_app.send_expiry_reminders(None)
        # Only the row whose send succeeded gets stamped -- a failed send must not be
        # marked reminded, or that certificate silently never gets another chance.
        self.assertEqual(conn.cursor_obj.updated_ids, [2])

    def test_opted_out_recipient_is_skipped_and_left_unstamped(self):
        conn = self._patch_conn([
            _row(id=1, notifications_enabled=False),
            _row(id=2, email="maya.osei@demo.com"),
        ])
        with patch("shared.comms.send_expiry_email") as send:
            function_app.send_expiry_reminders(None)
        # The opted-out row is neither emailed nor stamped -- reminder_sent_at staying
        # NULL is what lets them get the reminder later if they turn notifications
        # back on, rather than this certificate being silently skipped forever.
        send.assert_called_once_with(
            "maya.osei@demo.com", "Ethan Brooks", "Workplace Safety",
            "2026-09-01", "Quizrant")
        self.assertEqual(conn.cursor_obj.updated_ids, [2])

    def test_unconfigured_resend_stops_cleanly_without_marking_anything_sent(self):
        from shared.comms import CommsNotConfigured
        conn = self._patch_conn([_row(id=1), _row(id=2)])
        with patch("shared.comms.send_expiry_email") as send:
            send.side_effect = CommsNotConfigured("RESEND_API_KEY is not set")
            function_app.send_expiry_reminders(None)  # must not raise
        self.assertEqual(conn.cursor_obj.updated_ids, [],
                          "an unconfigured run must not mark anything as reminded")
        send.assert_called_once()  # stops after the first, not called again per row


if __name__ == "__main__":
    unittest.main()
