"""
Extraction: Document Intelligence when configured, pypdf when not.

The fallback is the load-bearing part. `QUIZGEN_PROVIDER=mock` with no Azure account has
to keep working — it is a hard constraint in PROJECT.md, and tests.yml holds no Azure
credentials by design, so a hard dependency on Document Intelligence would leave the test
suite unable to extract a PDF at all.

Azure is stubbed. What is under test is how a layout result is turned into pages: reading
order, page identity, which paragraph roles are kept, and what happens to tables. Not the
service.
"""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quizgen import extract as extract_mod  # noqa: E402
from quizgen.extract import HEADING_PREFIX, Extraction  # noqa: E402
from quizgen.ingest import looks_like_heading  # noqa: E402


class Region:
    def __init__(self, page_number):
        self.page_number = page_number


class Para:
    def __init__(self, content, page=1, role=None):
        self.content = content
        self.role = role
        self.bounding_regions = [Region(page)]


class Cell:
    def __init__(self, row, col, content):
        self.row_index, self.column_index, self.content = row, col, content


class Table:
    def __init__(self, cells, page=1):
        self.cells = cells
        self.bounding_regions = [Region(page)]


class Result:
    def __init__(self, paragraphs=(), tables=(), page_count=1):
        self.paragraphs = list(paragraphs)
        self.tables = list(tables)
        self.pages = [object()] * page_count


def stub_azure(test, result):
    """Point the Document Intelligence client at a canned result."""
    di = types.ModuleType("azure.ai.documentintelligence")
    models = types.ModuleType("azure.ai.documentintelligence.models")
    creds = types.ModuleType("azure.core.credentials")

    class Poller:
        def result(self):
            return result

    class Client:
        def __init__(self, endpoint=None, credential=None):
            pass

        def begin_analyze_document(self, model, request):
            return Poller()

    di.DocumentIntelligenceClient = Client
    models.AnalyzeDocumentRequest = lambda **kw: kw
    creds.AzureKeyCredential = lambda key: key

    # EVERY key touched below is saved, including azure.core. An earlier version
    # setdefault'd azure.core without saving it, so a stub module survived the test and
    # azure.search.documents could no longer import — four unrelated isolation tests
    # failed with import errors pointing nowhere near the cause. Twice now; hence the
    # explicit list.
    touched = ("azure", "azure.ai", "azure.ai.documentintelligence",
               "azure.ai.documentintelligence.models", "azure.core", "azure.core.credentials")
    saved = {key: sys.modules.get(key) for key in touched}

    def restore():
        for key, value in saved.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value
    test.addCleanup(restore)

    sys.modules["azure.ai"] = types.ModuleType("azure.ai")
    sys.modules["azure.ai.documentintelligence"] = di
    sys.modules["azure.ai.documentintelligence.models"] = models
    sys.modules.setdefault("azure.core", types.ModuleType("azure.core"))
    sys.modules["azure.core.credentials"] = creds

    # A path is never opened — read_bytes is stubbed out with the request builder.
    original = Path.read_bytes
    Path.read_bytes = lambda self: b"%PDF-1.4 stub"
    test.addCleanup(lambda: setattr(Path, "read_bytes", original))


class TestTheHeadingSentinel(unittest.TestCase):
    """
    The marker cannot be the empty string.

    It was, briefly. `"anything".startswith("")` is True, so looks_like_heading() returned
    True for every line and every single line would have become its own section — each
    one a separate topic, each chunk one line long, and mastery measured per line. The
    pipeline would have run to completion and produced nonsense.
    """

    def test_it_is_not_empty(self):
        self.assertNotEqual(HEADING_PREFIX, "")

    def test_it_is_a_character_prose_cannot_contain(self):
        self.assertEqual(len(HEADING_PREFIX), 1)
        self.assertFalse(HEADING_PREFIX.isprintable())

    def test_an_ordinary_line_is_not_treated_as_a_heading(self):
        self.assertFalse(looks_like_heading(
            "the security team must be told within one hour of discovery."))

    def test_a_marked_line_is(self):
        self.assertTrue(looks_like_heading(HEADING_PREFIX + "Incident Reporting"))

    def test_marking_beats_the_heuristic(self):
        # A heading the heuristics would reject — punctuated, lowercase — is still a
        # heading when the extractor labelled it one.
        self.assertFalse(looks_like_heading("what to do when a laptop goes missing:"))
        self.assertTrue(looks_like_heading(
            HEADING_PREFIX + "what to do when a laptop goes missing:"))


class TestAzureExtraction(unittest.TestCase):
    def test_paragraphs_land_on_their_own_pages(self):
        # Page identity has to survive: a citation is "document, page 3, quote", and a
        # question with the wrong page looks verifiable and is not.
        stub_azure(self, Result(
            paragraphs=[Para("First page body.", page=1),
                        Para("Second page body.", page=2)],
            page_count=2))
        result = extract_mod.extract(Path("x.pdf"), prefer_azure=True)

        self.assertEqual(result.engine, "document-intelligence")
        self.assertIn("First page body.", result.pages[0])
        self.assertNotIn("Second page body.", result.pages[0])
        self.assertIn("Second page body.", result.pages[1])

    def test_headings_are_marked_and_collected(self):
        stub_azure(self, Result(paragraphs=[
            Para("Information Security Policy", role="title"),
            Para("Incident Reporting", role="sectionHeading"),
            Para("Report suspected incidents within one hour.")]))
        result = extract_mod.extract(Path("x.pdf"), prefer_azure=True)

        self.assertEqual(result.headings,
                         ["Information Security Policy", "Incident Reporting"])
        self.assertIn(HEADING_PREFIX + "Incident Reporting", result.pages[0])
        self.assertNotIn(HEADING_PREFIX + "Report suspected", result.pages[0])

    def test_page_furniture_is_dropped(self):
        # Headers and footers repeat on every page. Left in, they merged unrelated
        # documents into one training when a letterhead became the title.
        stub_azure(self, Result(paragraphs=[
            Para("ACME Corp | Internal Use Only", role="pageHeader"),
            Para("Page 4 of 12", role="pageNumber"),
            Para("Confidential", role="pageFooter"),
            Para("Real teaching content lives here.")]))
        pages = extract_mod.extract(Path("x.pdf"), prefer_azure=True).pages

        self.assertIn("Real teaching content", pages[0])
        for junk in ("Internal Use Only", "Page 4 of 12", "Confidential"):
            self.assertNotIn(junk, pages[0])

    def test_a_table_is_kept_as_rows(self):
        # Retention schedules and severity matrices only ever appear as tables. Dropping
        # them loses rules that exist nowhere else in the document.
        stub_azure(self, Result(tables=[Table([
            Cell(0, 0, "Severity"), Cell(0, 1, "Response time"),
            Cell(1, 0, "Critical"), Cell(1, 1, "1 hour"),
            Cell(2, 0, "Low"), Cell(2, 1, "5 days")])]))
        page = extract_mod.extract(Path("x.pdf"), prefer_azure=True).pages[0]

        self.assertIn("Severity | Response time", page)
        self.assertIn("Critical | 1 hour", page)
        # Plain text, not markdown: the grounding check looks for a quote verbatim in
        # what was extracted, and markdown separator rows would end up inside quotes.
        self.assertNotIn("---", page)

    def test_cells_keep_their_column_order(self):
        stub_azure(self, Result(tables=[Table([
            Cell(0, 2, "third"), Cell(0, 0, "first"), Cell(0, 1, "second")])]))
        self.assertIn("first | second | third",
                      extract_mod.extract(Path("x.pdf"), prefer_azure=True).pages[0])


class TestFallback(unittest.TestCase):
    def test_unconfigured_uses_pypdf(self):
        from quizgen.config import CONFIG
        self.assertFalse(
            CONFIG.doc_intelligence_configured,
            "the test environment should have no Document Intelligence configured")

    def test_an_azure_failure_falls_back_rather_than_raising(self):
        # Loudly, but it falls back. A run that dies because the extraction service had a
        # bad minute is worse than one that produces less.
        def explode(path):
            raise RuntimeError("service unavailable")

        original = extract_mod._extract_azure
        extract_mod._extract_azure = explode
        self.addCleanup(lambda: setattr(extract_mod, "_extract_azure", original))

        calls = []
        original_pypdf = extract_mod._extract_pypdf
        extract_mod._extract_pypdf = lambda path: (calls.append(path)
                                                   or Extraction(pages=["fallback"]))
        self.addCleanup(lambda: setattr(extract_mod, "_extract_pypdf", original_pypdf))

        result = extract_mod.extract(Path("x.pdf"), prefer_azure=True)
        self.assertEqual(result.pages, ["fallback"])
        self.assertEqual(len(calls), 1)

    def test_empty_extraction_is_reported_not_silent(self):
        # A scan under pypdf yields nothing. That has to be distinguishable from a
        # document with no teachable content, or the fix is unguessable.
        self.assertTrue(Extraction(pages=["", "  "]).is_empty)
        self.assertFalse(Extraction(pages=["", "real text"]).is_empty)


if __name__ == "__main__":
    unittest.main()
