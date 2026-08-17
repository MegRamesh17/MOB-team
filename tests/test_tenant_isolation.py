"""
TENANT-05: two companies, side by side, no cross-read.

Every other test in this suite runs with one company's data, which is exactly the
condition under which a missing tenant filter is invisible. These put two companies in
one database and assert that neither can see the other's chunks, questions, attempts,
responses, certificates or mastery.

The requirement says the test must PROVE isolation rather than assert it, so
`test_removing_the_filter_makes_this_fail` deliberately opens a bank with tenant scoping
turned off and checks that the leak appears. If someone quietly drops the WHERE clause,
the other tests here go red — and that test explains why they would.

Scoping lives in Bank rather than at the call sites on purpose: a WHERE clause repeated
in forty places has forty chances to be forgotten, and the one that is forgotten returns
another company's rows while looking exactly like a query that worked.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quizgen.bank import Bank  # noqa: E402
from quizgen.models import (  # noqa: E402
    Attempt, Chunk, Difficulty, Option, Question, QuestionType, Response, ReviewStatus,
)

ACME, GLOBEX = "1", "2"


def chunk(company, n=1, doc="Security Policy"):
    return Chunk(
        chunk_id="{}-chunk-{}".format(company, n), doc_id="{}-doc".format(company),
        doc_title=doc, topic="Passwords", section="Authentication",
        page_start=1, page_end=1,
        text="Company {} requires 14-character passwords and no reuse.".format(company),
        company_id=company)


def question(company, n=1, doc="Security Policy"):
    return Question(
        question_id="{}-q-{}".format(company, n), topic="Passwords",
        question_type=QuestionType.MULTIPLE_CHOICE, difficulty=Difficulty.MEDIUM,
        prompt="Company {} minimum password length?".format(company),
        explanation="Because company {} says so.".format(company),
        options=[Option(option_id="{}-o1".format(company), text="14", is_correct=True),
                 Option(option_id="{}-o2".format(company), text="6", is_correct=False)],
        source_chunk_id="{}-chunk-{}".format(company, n),
        source_doc_title=doc, review_status=ReviewStatus.APPROVED)


def attempt(company, learner, passed=True):
    response = Response(
        response_id="{}-r".format(company), attempt_id="{}-att".format(company),
        learner_id=learner, question_id="{}-q-1".format(company), topic="Passwords",
        selected_option_ids=["{}-o1".format(company)], text_answer="",
        is_correct=True, points_awarded=1, answered_at="2026-08-17T00:00:00+00:00")
    return Attempt(
        attempt_id="{}-att".format(company), learner_id=learner,
        started_at="2026-08-17T00:00:00+00:00", submitted_at="2026-08-17T00:05:00+00:00",
        score_percent=100.0, points_awarded=1, points_possible=1, passed=passed,
        responses=[response])


class TwoCompanies(unittest.TestCase):
    """One database file, two tenants, identical-shaped data in each."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "shared.db"

        # Deliberately the SAME learner email in both companies. Employees.email is
        # unique per database in the deployed schema, but the local learner key is just a
        # string — so if isolation depended on the learner id being unique rather than on
        # the tenant filter, this is where it would show.
        self.learner = "shared.name@example.com"

        for company in (ACME, GLOBEX):
            with Bank(self.db, company_id=company) as bank:
                bank.save_chunks([chunk(company)])
                bank.save_questions([question(company)])
                bank.save_attempt(attempt(company, self.learner))
                bank.issue_certificate(
                    certificate_id="{}-cert".format(company), learner_id=self.learner,
                    doc_title="Security Policy", attempt_id="{}-att".format(company),
                    attempt_score=90.0 if company == ACME else 10.0,
                    expires_at="2027-01-01T00:00:00+00:00")
                bank.set_role_requirements(
                    "ALL", [{"doc_title": "Security Policy", "category": "technical"}])

    def bank(self, company):
        b = Bank(self.db, company_id=company)
        self.addCleanup(b.close)
        return b


class TestNoCrossRead(TwoCompanies):
    def test_chunks(self):
        for company in (ACME, GLOBEX):
            with self.subTest(company=company):
                chunks = self.bank(company).all_chunks()
                self.assertEqual(len(chunks), 1)
                self.assertEqual(chunks[0].company_id, company)
                self.assertIn("Company {}".format(company), chunks[0].text)

    def test_questions(self):
        for company in (ACME, GLOBEX):
            with self.subTest(company=company):
                questions = self.bank(company).questions()
                self.assertEqual([q.question_id for q in questions],
                                 ["{}-q-1".format(company)])

    def test_a_question_id_from_another_company_is_not_found(self):
        # Guessing an id must not be a way through. The filter has to apply to lookups
        # by primary key too, not only to list queries.
        self.assertIsNone(self.bank(ACME).get_question("{}-q-1".format(GLOBEX)))
        self.assertIsNotNone(self.bank(ACME).get_question("{}-q-1".format(ACME)))

    def test_attempts_and_mastery(self):
        for company in (ACME, GLOBEX):
            with self.subTest(company=company):
                self.assertEqual(self.bank(company).attempt_count(self.learner), 1)
                mastery = self.bank(company).mastery(self.learner)
                # One answer each. Seeing 2 would mean the other company's response was
                # folded into this learner's accuracy.
                self.assertEqual(sum(m.answered for m in mastery.values()), 1)

    def test_certificates(self):
        acme = self.bank(ACME).certificates(self.learner)
        globex = self.bank(GLOBEX).certificates(self.learner)
        self.assertEqual(len(acme), 1)
        self.assertEqual(len(globex), 1)
        # The scores differ, so a mix-up shows up as a wrong number rather than a
        # duplicate row — the kind of leak that survives a count-based test.
        self.assertEqual(acme[0]["attempt_score"], 90.0)
        self.assertEqual(globex[0]["attempt_score"], 10.0)

    def test_stats(self):
        for company in (ACME, GLOBEX):
            with self.subTest(company=company):
                stats = self.bank(company).stats()
                self.assertEqual(stats["chunks"], 1)
                self.assertEqual(stats["questions"], 1)
                self.assertEqual(stats["attempts"], 1)
                self.assertEqual(stats["responses"], 1)

    def test_recently_seen_does_not_cross_over(self):
        seen = self.bank(ACME).recently_seen(self.learner, last_n_attempts=10)
        self.assertNotIn("{}-q-1".format(GLOBEX), seen)


class TestWritesAreStamped(TwoCompanies):
    def test_a_chunk_is_written_under_the_banks_tenant_not_its_own(self):
        # A Chunk object carries a company_id. If a caller passes one built for another
        # tenant, the BANK's value must win — otherwise a mislabelled object is a way to
        # write into someone else's data.
        stray = chunk(GLOBEX, n=99)
        with Bank(self.db, company_id=ACME) as bank:
            bank.save_chunks([stray])
        self.assertEqual(
            [c.company_id for c in self.bank(ACME).all_chunks() if c.chunk_id.endswith("99")],
            [ACME])
        self.assertEqual(
            [c for c in self.bank(GLOBEX).all_chunks() if c.chunk_id.endswith("99")], [])


class TestTheFilterIsWhatDoesIt(TwoCompanies):
    """
    Proof rather than assertion, as TENANT-05 asks for.

    If these two ever disagree — scoped sees one, unscoped sees two — the filter is doing
    the work. If they agree, either the data is wrong or the filter has stopped applying,
    and both are worth failing over.
    """

    def test_removing_the_filter_makes_this_fail(self):
        scoped = self.bank(ACME).all_chunks()
        unscoped = Bank(self.db, company_id=Bank.ALL_COMPANIES)
        self.addCleanup(unscoped.close)

        self.assertEqual(len(scoped), 1, "one company's data")
        self.assertEqual(len(unscoped.all_chunks()), 2, "both companies' data")
        self.assertGreater(
            len(unscoped.all_chunks()), len(scoped),
            "if these are equal the tenant filter is no longer doing anything")

    def test_the_escape_hatch_is_explicit(self):
        # Opting out has to be a deliberate, greppable act — never something a default
        # does for you.
        self.assertNotEqual(Bank.ALL_COMPANIES, "")
        self.assertNotEqual(Bank.ALL_COMPANIES, None)
        with Bank(self.db) as default_bank:
            self.assertNotEqual(default_bank.company_id, Bank.ALL_COMPANIES,
                                "the default must be scoped, not unscoped")


class TestQScoreDoesNotLeak(TwoCompanies):
    def test_one_companys_certificate_does_not_count_towards_anothers_coverage(self):
        from quizgen import qscore

        now = datetime(2026, 8, 17, tzinfo=timezone.utc)
        for company, expected in ((ACME, 90.0), (GLOBEX, 10.0)):
            with self.subTest(company=company):
                bank = self.bank(company)
                standing = qscore.standing(
                    bank.role_requirements("ALL"), bank.certificates(self.learner), now=now)
                # Coverage 1 of 1 either way; the quality is what betrays a mix-up.
                self.assertEqual(standing["overall"].current, 1)
                self.assertAlmostEqual(standing["overall"].quality, expected, places=1)


if __name__ == "__main__":
    unittest.main()


class TestEveryDeployedQueryIsScoped(unittest.TestCase):
    """
    A structural check over api/function_app.py.

    The behavioural tests above run against the local bank, because there is no Azure SQL
    to test with. That leaves the deployed queries — where the leak actually lives —
    covered by nothing.

    So this reads the file: any endpoint that touches dbo.* must mention company_id. It is
    a blunt instrument and cannot tell a correct filter from a wrong one. What it CAN do
    is catch the failure that actually happens — a new endpoint written without a tenant
    filter at all, which is how every one of the seven found here got that way.
    """

    ENDPOINT = __import__("re").compile(r'route="([^"]+)"')
    # UPDATE and DELETE belong here as much as FROM and INSERT. An earlier version of this
    # pattern matched only reads and inserts, and reported "every data query is
    # tenant-scoped" while POST /review/decide was updating GeneratedQuestions with
    # `WHERE question_id = ?` and no company filter at all — a manager in one company
    # could change another company's question by guessing its id. The audit did not miss
    # it by being wrong about that endpoint; it never looked at it.
    TOUCHES_DB = __import__("re").compile(
        r"\bFROM dbo\.|\bINSERT INTO dbo\.|\bUPDATE dbo\.|\bDELETE FROM dbo\.")

    def _endpoints(self):
        src = (Path(__file__).resolve().parents[1] / "api" / "function_app.py").read_text(
            encoding="utf-8")
        lines = src.split("\n")
        starts = [(i, self.ENDPOINT.search(line).group(1))
                  for i, line in enumerate(lines)
                  if "@app.route" in line and 'route="' in line]
        for idx, (line_no, name) in enumerate(starts):
            end = starts[idx + 1][0] if idx + 1 < len(starts) else len(lines)
            yield name, "\n".join(lines[line_no:end])

    def test_no_endpoint_queries_without_a_tenant_filter(self):
        unscoped = [name for name, body in self._endpoints()
                    if self.TOUCHES_DB.search(body) and "company_id" not in body]
        self.assertEqual(
            unscoped, [],
            "these endpoints read or write dbo.* without mentioning company_id, so they "
            "serve every company's rows: {}".format(", ".join(unscoped)))

    def test_the_check_would_actually_notice(self):
        # Guard against the audit silently matching nothing — a regex that stops matching
        # would make this suite pass by testing an empty list forever.
        with_db = [name for name, body in self._endpoints() if self.TOUCHES_DB.search(body)]
        self.assertGreater(len(with_db), 8,
                           "expected most endpoints to touch the database; if this drops, "
                           "the pattern has stopped matching rather than the code improving")
