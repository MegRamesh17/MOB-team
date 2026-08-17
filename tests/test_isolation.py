"""
Tests for company-level isolation.

Closes the gap described in docs/company-isolation-gap.md: role_scope answers "which
roles inside a company may see this chunk", and nothing answered "which company does
this chunk belong to at all". In a shared index that means every chunk is retrievable
by every company.

The enforcement lives in isolation.py; this exercises the wiring through the model, the
bank, ingestion, and the two search_index entry points. Azure is stubbed — what is under
test is that an untagged chunk cannot reach a shared index, not the search service.

The asymmetry with role_scope is the property being protected, and it is easy to
"tidy away" later by giving company_id a sensible-looking default. These tests exist to
make that break loudly.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quizgen.bank import Bank  # noqa: E402
from quizgen.isolation import (  # noqa: E402
    IsolationError,
    stamp_and_validate_batch,
    stamp_company_id,
    validate_company_id,
)
from quizgen.models import Chunk  # noqa: E402


def _chunk(**overrides) -> Chunk:
    base = dict(
        chunk_id="c1", doc_id="d1", doc_title="Security Policy", topic="Passwords",
        section="Authentication", page_start=1, page_end=1,
        text="Passwords must be at least 14 characters long.",
    )
    base.update(overrides)
    return Chunk(**base)


class TestCompanyIdIsNotOptional(unittest.TestCase):
    """A missing company_id must fail, never fall back to something permissive."""

    def test_chunk_defaults_to_empty_not_to_a_wildcard(self):
        # role_scope defaults to ALL because "no stated audience" means everyone
        # within one company. company_id has no safe equivalent: "visible to every
        # company" is never the right default.
        chunk = _chunk()
        self.assertEqual(chunk.role_scope, "ALL")
        self.assertEqual(chunk.company_id, "")

    def test_validate_rejects_missing_company_id(self):
        with self.assertRaises(IsolationError):
            validate_company_id({"chunk_id": "c1"})

    def test_validate_rejects_blank_and_whitespace(self):
        for value in ("", "   ", None):
            with self.subTest(value=value):
                with self.assertRaises(IsolationError):
                    validate_company_id({"chunk_id": "c1", "company_id": value})

    def test_stamp_refuses_an_empty_company_id(self):
        with self.assertRaises(IsolationError):
            stamp_company_id({"chunk_id": "c1"}, "")

    def test_batch_fails_whole_rather_than_dropping_one(self):
        # A partially-tagged batch is the quiet failure: the untagged chunks would
        # simply not appear, and nobody would know which.
        with self.assertRaises(IsolationError):
            stamp_and_validate_batch([{"chunk_id": "a"}, {"chunk_id": "b"}], "  ")


class TestUploadRefusesUntaggedChunks(unittest.TestCase):
    """search_index.upload is the last gate before a shared index."""

    def test_upload_raises_on_an_untagged_chunk(self):
        from quizgen import search_index

        with self.assertRaises(IsolationError) as caught:
            search_index.upload([_chunk()], with_embeddings=False)
        # The message must name the chunk — "something was untagged" is not actionable
        # when a batch is a few hundred passages.
        self.assertIn("c1", str(caught.exception))

    def test_upload_of_an_empty_batch_is_a_no_op(self):
        from quizgen import search_index

        self.assertEqual(search_index.upload([], with_embeddings=False), 0)


class TestRetrieveCannotBeCalledUnscoped(unittest.TestCase):
    """
    company_id is positional and required on retrieve().

    An optional company filter is one forgotten keyword argument away from querying
    every company at once, and the call site would look entirely normal in review.
    """

    def test_retrieve_requires_company_id_positionally(self):
        from quizgen import search_index

        with self.assertRaises(TypeError):
            search_index.retrieve("password policy")  # no company_id at all

    def test_retrieve_rejects_a_blank_company_id(self):
        from quizgen import search_index

        for value in ("", "   "):
            with self.subTest(value=value):
                with self.assertRaises(IsolationError):
                    search_index.retrieve("password policy", value)


class TestCompanyIdSurvivesStorage(unittest.TestCase):
    """A tag that is lost on the way through SQLite is not a tag."""

    def test_round_trip_through_the_bank(self):
        # The BANK's tenant is authoritative, not the Chunk's. Passing a chunk built for
        # another company must not write into that company — so the bank is opened as
        # company 7 rather than the chunk merely claiming to be.
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "bank.db"
            with Bank(db, company_id="7") as bank:
                bank.save_chunks([_chunk()])
            with Bank(db, company_id="7") as bank:
                restored = bank.all_chunks()
        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0].company_id, "7")

    def test_two_companies_stay_distinct(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "bank.db"
            with Bank(db, company_id="1") as bank:
                bank.save_chunks([_chunk(chunk_id="a")])
            with Bank(db, company_id="2") as bank:
                bank.save_chunks([_chunk(chunk_id="b")])
            with Bank(db, company_id=Bank.ALL_COMPANIES) as bank:
                by_id = {c.chunk_id: c.company_id for c in bank.all_chunks()}
        self.assertEqual(by_id, {"a": "1", "b": "2"})

    def test_a_bank_created_before_the_column_still_opens(self):
        """
        CREATE TABLE IF NOT EXISTS is a no-op on an existing table, so a bank written
        before company_id existed keeps its old shape and every read of the new column
        raises. Anyone holding a quizgen.db from last week would have hit this.
        """
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "old.db"
            legacy = sqlite3.connect(str(db))
            legacy.execute(
                "CREATE TABLE chunks ("
                " chunk_id TEXT PRIMARY KEY, doc_id TEXT NOT NULL, doc_title TEXT NOT NULL,"
                " topic TEXT NOT NULL, section TEXT NOT NULL, page_start INTEGER NOT NULL,"
                " page_end INTEGER NOT NULL, text TEXT NOT NULL,"
                " container TEXT NOT NULL DEFAULT '', role_scope TEXT NOT NULL DEFAULT 'ALL')"
            )
            legacy.execute(
                "INSERT INTO chunks VALUES ('old','d','Doc','T','S',1,1,'text','','ALL')"
            )
            legacy.commit()
            legacy.close()

            with Bank(db) as bank:
                visible = bank.all_chunks()
            with Bank(db, company_id=Bank.ALL_COMPANIES) as bank:
                all_rows = bank.all_chunks()

            # The bank opens rather than raising — that is what this test is for.
            #
            # The legacy row survives the migration but is INVISIBLE to a tenant-scoped
            # read, because it carries no company. That is the safe direction: a row that
            # cannot be attributed to a company must not be served to one.
            #
            # Chunks are deliberately not backfilled, unlike the other tables. They are
            # the one thing that reaches a SHARED search index, where an untagged chunk is
            # retrievable by everybody — so guessing a tenant for them is exactly the
            # permissive default docs/company-isolation-gap.md rejects. They have to be
            # re-ingested.
            self.assertEqual(visible, [], "an untagged row must not be served to a tenant")
            self.assertEqual(len(all_rows), 1, "but it is still there")
            self.assertEqual(all_rows[0].company_id, "")
            with self.assertRaises(IsolationError):
                validate_company_id({"chunk_id": "old", "company_id": all_rows[0].company_id})


class TestIngestionTagsAtCreation(unittest.TestCase):
    """Chunks are tagged where they are made, not stamped somewhere downstream."""

    def test_chunks_from_text_carry_the_configured_company(self):
        from quizgen.config import CONFIG
        from quizgen.ingest import chunks_from_text

        chunks = chunks_from_text(
            "Information Security Policy\n\n"
            "Authentication and Passwords\n"
            "Multi-factor authentication requires a second proof of identity in addition "
            "to a password. Passwords must be at least 14 characters long and must not be "
            "reused across any other service. Shared accounts are prohibited entirely.\n",
            "security.pdf",
        )
        self.assertTrue(chunks, "expected at least one chunk from the sample text")
        for chunk in chunks:
            self.assertEqual(chunk.company_id, CONFIG.company_id)
            self.assertNotEqual(chunk.company_id, "", "ingestion must not emit untagged chunks")


if __name__ == "__main__":
    unittest.main()
