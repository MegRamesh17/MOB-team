from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))

from shared.certificates import render_certificate, store_certificate  # noqa: E402


class FakeBlob:
    def __init__(self):
        self.upload = None

    def upload_blob(self, content, **kwargs):
        self.upload = (content, kwargs)


class FakeService:
    def __init__(self):
        self.container = None
        self.name = None
        self.blob = FakeBlob()

    def get_blob_client(self, container, blob):
        self.container = container
        self.name = blob
        return self.blob


class TestCertificatePdf(unittest.TestCase):
    def test_minimal_certificate_is_a_real_single_page_pdf(self):
        content = render_certificate(
            "Avery Employee", "Secure Python Practices", 92.5,
            "2026-08-18T12:00:00+00:00", "2027-08-18T12:00:00+00:00", "cert_abc123",
        )
        self.assertTrue(content.startswith(b"%PDF-"))
        self.assertGreater(len(content), 1000)
        self.assertEqual(content.count(b"/Type /Page"), 2)  # page plus Pages catalog

    def test_upload_uses_private_certificate_container_and_returns_only_blob_name(self):
        service = FakeService()
        name = store_certificate(b"%PDF-test", "1/3/cert_test.pdf", service=service)
        self.assertEqual(name, "1/3/cert_test.pdf")
        self.assertEqual(service.container, "certificates")
        self.assertEqual(service.name, name)
        content, kwargs = service.blob.upload
        self.assertEqual(content, b"%PDF-test")
        self.assertTrue(kwargs["overwrite"])
        self.assertEqual(kwargs["content_settings"].content_type, "application/pdf")


if __name__ == "__main__":
    unittest.main()
