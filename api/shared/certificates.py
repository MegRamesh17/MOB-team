"""Minimal certificate PDF rendering and private Azure Blob storage."""

from __future__ import annotations

import io
import os
from datetime import datetime
from typing import Optional


CONTAINER = "certificates"


def render_certificate(
    employee_name: str,
    training_title: str,
    score: float,
    issued_at: str,
    expires_at: str,
    certificate_id: str,
) -> bytes:
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.pdfgen import canvas

    output = io.BytesIO()
    width, height = landscape(letter)
    pdf = canvas.Canvas(output, pagesize=(width, height))
    pdf.setTitle("Quizrant certificate - {}".format(training_title))

    ink = HexColor("#1E1B2E")
    accent = HexColor("#6423C9")
    muted = HexColor("#6B6480")
    line = HexColor("#E4DCF5")

    pdf.setStrokeColor(line)
    pdf.setLineWidth(2)
    pdf.rect(28, 28, width - 56, height - 56, stroke=1, fill=0)
    pdf.setStrokeColor(accent)
    pdf.setLineWidth(5)
    pdf.line(62, height - 76, width - 62, height - 76)

    pdf.setFillColor(accent)
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawCentredString(width / 2, height - 125, "QUIZRANT")
    pdf.setFillColor(ink)
    pdf.setFont("Helvetica-Bold", 30)
    pdf.drawCentredString(width / 2, height - 180, "Certificate of Completion")

    pdf.setFillColor(muted)
    pdf.setFont("Helvetica", 13)
    pdf.drawCentredString(width / 2, height - 225, "This certifies that")
    pdf.setFillColor(ink)
    pdf.setFont("Helvetica-Bold", 24)
    pdf.drawCentredString(width / 2, height - 265, employee_name or "Employee")

    pdf.setFillColor(muted)
    pdf.setFont("Helvetica", 13)
    pdf.drawCentredString(width / 2, height - 305, "successfully completed")
    pdf.setFillColor(ink)
    pdf.setFont("Helvetica-Bold", 18)
    title = training_title if len(training_title) <= 72 else training_title[:69] + "..."
    pdf.drawCentredString(width / 2, height - 340, title)

    pdf.setFillColor(muted)
    pdf.setFont("Helvetica", 11)
    pdf.drawCentredString(
        width / 2, height - 385,
        "Final score: {:.1f}%   |   Issued: {}   |   Valid until: {}".format(
            score, _date(issued_at), _date(expires_at)),
    )
    pdf.setFont("Helvetica", 9)
    pdf.drawCentredString(width / 2, 58, "Certificate ID: {}".format(certificate_id))
    pdf.showPage()
    pdf.save()
    return output.getvalue()


def store_certificate(
    content: bytes,
    blob_name: str,
    connection_string: Optional[str] = None,
    service=None,
) -> str:
    """Upload to the private certificate container and return the blob name only."""
    if service is None:
        from azure.storage.blob import BlobServiceClient

        value = connection_string or os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
        if not value:
            raise RuntimeError("AZURE_STORAGE_CONNECTION_STRING is not configured")
        service = BlobServiceClient.from_connection_string(value)

    from azure.storage.blob import ContentSettings

    client = service.get_blob_client(container=CONTAINER, blob=blob_name)
    client.upload_blob(
        content, overwrite=True,
        content_settings=ContentSettings(content_type="application/pdf"),
    )
    return blob_name


def download_certificate(blob_name: str, connection_string: Optional[str] = None) -> bytes:
    from azure.storage.blob import BlobServiceClient

    value = connection_string or os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
    if not value:
        raise RuntimeError("AZURE_STORAGE_CONNECTION_STRING is not configured")
    service = BlobServiceClient.from_connection_string(value)
    return service.get_blob_client(container=CONTAINER, blob=blob_name).download_blob().readall()


def _date(value: str) -> str:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date().isoformat()
    except (TypeError, ValueError):
        return str(value)[:10]
