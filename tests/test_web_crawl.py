"""Boundaries and provenance for manager-submitted website crawls."""

from __future__ import annotations

import gzip
import socket
import unittest
from unittest.mock import patch

import httpx

from quizgen.config import CONFIG
from quizgen.llm.azure_openai import AzureOpenAIGenerator
from quizgen.models import Chunk, ProvenanceClass
from quizgen.web import (
    _validate_public_url,
    _request_url,
    chunks_from_crawl,
    crawl_site,
    html_to_text,
)


class _Response:
    def __init__(self, url, text, content_type="text/html", status=200):
        self.url = url
        self.text = text
        self.status_code = status
        self.headers = {"content-type": content_type}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP {}".format(self.status_code))


def _html(title, body, links=()):
    anchors = "".join('<a href="{}">next</a>'.format(link) for link in links)
    return (
        "<html><head><title>{}</title></head><body><main>"
        "<h1>{}</h1><p>{}</p>{}</main></body></html>"
    ).format(title, title, body, anchors)


class TestHtmlCleaning(unittest.TestCase):
    def test_promotional_banner_is_not_teaching_text(self):
        title, text = html_to_text(
            "<title>Prompt Engineering Guide</title>"
            "<h1>Learn Claude Code - use PROMPTING for 20% off - Enroll now</h1>"
            "<h2>Prompt design</h2><p>Use clear instructions and relevant context.</p>"
        )
        self.assertEqual(title, "Prompt Engineering Guide")
        self.assertNotIn("20% off", text)
        self.assertIn("Prompt design", text)


class TestCrawlBoundaries(unittest.TestCase):
    def _crawl(self, pages, robots="User-agent: *\nAllow: /\n", max_pages=10):
        def request(url, timeout, client=None):
            if url == "https://docs.example.com/robots.txt":
                return _Response(url, robots, "text/plain")
            return _Response(url, pages[url])

        with patch("quizgen.web._validate_public_url"), \
                patch("quizgen.web._request_url", side_effect=request), \
                patch("quizgen.web.time.sleep"):
            return crawl_site("https://docs.example.com/", max_pages=max_pages)

    def test_follows_same_site_links_and_ignores_external_assets(self):
        body = "Clear instructions improve model behavior. " * 12
        pages = {
            "https://docs.example.com/": _html(
                "Prompt Guide", body,
                ("/basics", "/basics#examples", "/about", "https://other.example.net/x", "/logo.png"),
            ),
            "https://docs.example.com/basics": _html("Prompt Basics", body),
        }
        result = self._crawl(pages)

        self.assertEqual([p.url for p in result.pages], [
            "https://docs.example.com/",
            "https://docs.example.com/basics",
        ])
        self.assertEqual(result.title, "Prompt Guide")

    def test_obeys_robots_txt(self):
        body = "Documented guidance should remain traceable to its source. " * 10
        pages = {
            "https://docs.example.com/": _html("Guide", body, ("/allowed", "/private")),
            "https://docs.example.com/allowed": _html("Allowed", body + " allowed"),
        }
        result = self._crawl(
            pages,
            robots="User-agent: *\nDisallow: /private\n",
        )

        self.assertEqual(len(result.pages), 2)
        self.assertTrue(all("private" not in page.url for page in result.pages))
        self.assertGreaterEqual(result.skipped, 1)

    def test_duplicate_page_content_is_stored_once(self):
        body = "One canonical article may be reachable through several site links. " * 9
        pages = {
            "https://docs.example.com/": _html("Guide", body, ("/copy-a", "/copy-b")),
            "https://docs.example.com/copy-a": _html("Same", body),
            "https://docs.example.com/copy-b": _html("Same", body),
        }
        result = self._crawl(pages)

        self.assertEqual(len(result.pages), 2)
        self.assertGreaterEqual(result.skipped, 1)

    def test_chunks_keep_page_url_under_one_course_title(self):
        body = (
            "Prompt structure combines an instruction, relevant context, and an output "
            "format. Clear constraints make the intended response easier to evaluate. "
        ) * 4
        pages = {
            "https://docs.example.com/": _html("Prompt Guide", body, ("/few-shot",)),
            "https://docs.example.com/few-shot": _html("Few-shot Prompting", body + " Examples help."),
        }
        result = self._crawl(pages)
        chunks = chunks_from_crawl(result, "SDE2")

        self.assertTrue(chunks)
        self.assertEqual({chunk.doc_title for chunk in chunks}, {"Prompt Guide"})
        self.assertEqual(len({chunk.doc_id for chunk in chunks}), 1)
        self.assertEqual(
            {chunk.topic for chunk in chunks}, {"Prompt Guide", "Few-shot Prompting"})
        self.assertEqual(
            {chunk.source_url for chunk in chunks},
            {"https://docs.example.com/", "https://docs.example.com/few-shot"},
        )
        self.assertEqual({chunk.role_scope for chunk in chunks}, {"SDE2"})


class TestSsrfGuard(unittest.TestCase):
    def test_private_ip_is_rejected(self):
        private = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
        with patch("quizgen.web.socket.getaddrinfo", return_value=private):
            with self.assertRaisesRegex(ValueError, "Private network"):
                _validate_public_url("https://internal.example.com/")

    def test_nonstandard_port_is_rejected_before_dns(self):
        with self.assertRaisesRegex(ValueError, "ports 80 and 443"):
            _validate_public_url("https://example.com:8080/")

    def test_streamed_compressed_page_is_returned_as_readable_text(self):
        raw = b"<html><body>Readable documentation</body></html>"

        def respond(request):
            return httpx.Response(
                200,
                headers={"content-type": "text/html", "content-encoding": "gzip"},
                content=gzip.compress(raw),
                request=request,
            )

        with httpx.Client(transport=httpx.MockTransport(respond)) as client, \
                patch("quizgen.web._validate_public_url"):
            response = _request_url("https://docs.example.com/", 2, client=client)

        self.assertIn("Readable documentation", response.text)
        self.assertNotIn("content-encoding", response.headers)


class TestQuestionProvenance(unittest.TestCase):
    def test_grounded_question_keeps_its_exact_crawled_page(self):
        source = "Clear instructions and relevant context improve a prompt's reliability."
        chunk = Chunk(
            chunk_id="chunk-1", doc_id="doc-1", doc_title="Prompt Guide",
            topic="Prompt Basics", section="Prompt Basics", page_start=1, page_end=1,
            text=source, role_scope="SDE2", source_type="web",
            source_url="https://docs.example.com/basics",
            fetched_at="2026-08-19T12:00:00+00:00",
        )
        item = {
            "type": "MultipleChoice",
            "prompt": "What improves a prompt's reliability?",
            "difficulty": "Easy",
            "source_quote": source,
            "options": [
                {"text": "Clear instructions and relevant context", "is_correct": True},
                {"text": "Removing all context", "is_correct": False},
            ],
        }

        generator = object.__new__(AzureOpenAIGenerator)
        with patch.object(CONFIG, "generation_mode", "grounded"):
            question = generator._to_question(chunk, item)

        self.assertIsNotNone(question)
        self.assertEqual(question.provenance_class, ProvenanceClass.EXTERNAL)
        self.assertEqual(question.source_url, chunk.source_url)
        self.assertEqual(question.source_fetched_at, chunk.fetched_at)


if __name__ == "__main__":
    unittest.main()
