"""
Tests for the blob ingestion path.

This is now the primary source of documents and cannot be exercised against real Azure
without credentials, so pdf_extractor is stubbed. What is being tested is the wiring and
the per-document guarantee — not Azure itself.
"""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

SECURITY_DOC = """Information Security Policy

Authentication and Passwords
Multi-factor authentication is the security control that requires a second proof of
identity in addition to a password. Passwords must be at least 14 characters long and
must not be reused across any other service.

Incident Reporting
Suspected incidents must be reported within one hour of discovery. The security team
operates a 24-hour response line for escalations that cannot wait.
"""

SAFETY_DOC = """Workplace Safety Handbook

Fire Safety and Evacuation
Lifts must never be used during a fire evacuation under any circumstances. On hearing
the alarm, evacuate by the nearest marked stairwell to the assembly point.

Personal Protective Equipment
PPE stands for personal protective equipment. Damaged PPE must be replaced before use,
never repaired, and reported to a supervisor the same day.
"""

BLOBS = {
    "security-policy.pdf": SECURITY_DOC,
    "safety-handbook.pdf": SAFETY_DOC,
    "scanned-empty.pdf": "",  # a scan: extracts to nothing
}


def install_fake_extractor():
    """Stand in for src/pdf_extractor.py without touching Azure."""
    module = types.ModuleType("pdf_extractor")
    module.list_pdfs_in_container = lambda container: list(BLOBS)
    module.extract_text_from_blob_pdf = lambda container, name: BLOBS[name]
    sys.modules["pdf_extractor"] = module


COMPANY_DOC = """Code of Conduct

Conflicts of Interest
Employees must disclose any outside employment with a competitor. A conflict of interest
is a situation where personal interest could improperly influence a work decision.

Incident Reporting
Suspected incidents must be reported within one hour of discovery. The response team
operates around the clock for anything that cannot wait until morning.
"""

SWE_DOC = """Software Engineering Standards

Secure Coding Practices
Input validation is the practice of checking untrusted data before it is processed.
All code must pass peer review before it is merged to the main branch.

Deployment and Release
Every production deployment must have a documented rollback plan agreed in advance.
"""

TWO_CONTAINERS = {
    "company-docs": {"code-of-conduct.pdf": COMPANY_DOC},
    "software-engineering-docs": {"eng-standards.pdf": SWE_DOC},
}


def install_multi_container_extractor():
    module = types.ModuleType("pdf_extractor")
    module.list_pdfs_in_container = lambda c: list(TWO_CONTAINERS.get(c, {}))
    module.extract_text_from_blob_pdf = lambda c, n: TWO_CONTAINERS[c][n]
    sys.modules["pdf_extractor"] = module


class TestContainerRoleScope(unittest.TestCase):
    """
    The blob layout is the role taxonomy: company-docs applies to everyone,
    software-engineering-docs to that role. That beats inferring roles from prose,
    because filing is a decision someone made rather than a guess.
    """

    def setUp(self):
        install_multi_container_extractor()
        from quizgen import config

        config.CONFIG.storage_connection_string = "fake-connection-string"
        config.CONFIG.document_containers_raw = (
            "company-docs:ALL,software-engineering-docs:SWE"
        )

    def tearDown(self):
        sys.modules.pop("pdf_extractor", None)

    def test_chunks_are_tagged_with_their_container_scope(self):
        from quizgen.sources import chunks_from_blob_container

        chunks = chunks_from_blob_container()
        scopes = {c.container: c.role_scope for c in chunks}
        self.assertEqual(scopes.get("company-docs"), "ALL")
        self.assertEqual(scopes.get("software-engineering-docs"), "SWE")

    def test_role_inherits_company_wide_topics(self):
        """A Software Engineer must know company policy AND engineering standards."""
        from quizgen.roles import derive_role_profiles
        from quizgen.sources import chunks_from_blob_container

        profiles = {p.code: p for p in derive_role_profiles(chunks_from_blob_container())}

        self.assertIn("SWE", profiles)
        self.assertIn("ALL", profiles)

        company_topics = set(profiles["ALL"].documented_topics)
        swe_topics = set(profiles["SWE"].documented_topics)

        self.assertTrue(
            company_topics.issubset(swe_topics),
            "SWE must inherit every company-wide topic",
        )
        self.assertTrue(
            swe_topics - company_topics,
            "SWE must also have role-specific topics of its own",
        )

    def test_company_scope_does_not_absorb_role_specific_topics(self):
        """Engineering standards must not leak into what every employee is tested on."""
        from quizgen.roles import derive_role_profiles
        from quizgen.sources import chunks_from_blob_container

        profiles = {p.code: p for p in derive_role_profiles(chunks_from_blob_container())}
        company_topics = " ".join(profiles["ALL"].documented_topics).lower()
        self.assertNotIn("secure coding", company_topics)
        self.assertNotIn("deployment", company_topics)


class TestBlobSource(unittest.TestCase):
    def setUp(self):
        install_fake_extractor()
        from quizgen import config

        config.CONFIG.storage_connection_string = "fake-connection-string"

    def tearDown(self):
        sys.modules.pop("pdf_extractor", None)

    def test_each_pdf_keeps_its_own_identity(self):
        """
        The point of calling the extractor per-file instead of using
        extract_text_from_container(): a question must be able to say which document
        and page it came from. Concatenating the container destroys that.
        """
        from quizgen.sources import chunks_from_blob_container

        chunks = chunks_from_blob_container("documents")
        titles = {c.doc_title for c in chunks}

        self.assertIn("Information Security Policy", titles)
        self.assertIn("Workplace Safety Handbook", titles)
        self.assertEqual(
            len({c.doc_id for c in chunks}), 2,
            "each PDF must be its own document, not merged into one",
        )

    def test_section_headings_become_topics(self):
        from quizgen.sources import chunks_from_blob_container

        topics = {c.topic for c in chunks_from_blob_container("documents")}
        self.assertIn("Authentication And Passwords", topics)
        self.assertIn("Fire Safety And Evacuation", topics)

    def test_unextractable_pdf_is_skipped_not_fatal(self):
        """A scanned PDF yields no text. One bad file must not kill the whole run."""
        from quizgen.sources import chunks_from_blob_container

        chunks = chunks_from_blob_container("documents")
        self.assertTrue(chunks)
        self.assertNotIn("scanned-empty.pdf", {c.doc_id for c in chunks})

    def test_questions_generated_from_blob_carry_citations(self):
        from quizgen.llm.mock import MockGenerator
        from quizgen.sources import chunks_from_blob_container

        chunks = chunks_from_blob_container("documents")
        generator = MockGenerator(chunks, seed=1)

        produced = []
        for chunk in chunks:
            produced.extend(generator.generate(chunk, count=3))

        self.assertTrue(produced, "no questions generated from blob content")
        for q in produced:
            self.assertTrue(q.source_doc_title, "question has no document citation")
            self.assertTrue(q.source_quote, "question has no source quote")
            self.assertIn(q.source_doc_title,
                          {"Information Security Policy", "Workplace Safety Handbook"})

    def test_missing_connection_string_fails_clearly(self):
        from quizgen import config
        from quizgen.sources import chunks_from_blob_container

        config.CONFIG.storage_connection_string = ""
        with self.assertRaises(RuntimeError) as ctx:
            chunks_from_blob_container("documents")
        self.assertIn("AZURE_STORAGE_CONNECTION_STRING", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
