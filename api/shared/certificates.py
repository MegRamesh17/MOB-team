"""Certificate PDF rendering (Ascend brand theme) and private Azure Blob storage."""

from __future__ import annotations

import io
import os
from datetime import datetime
from typing import Optional


CONTAINER = "certificates"

# Same tokens as web-app/src/App.jsx's `C` (green700/amber/success/ink) -- kept in sync
# by hand since one lives in Python and the other in JS. If the brand palette changes
# there, change it here too.
_DEEP = "#147A4D"
_AMBER = "#FF9E4A"
_TEAL = "#14BBA6"
_INK = "#0F1214"
_MUTED = "#5C6B62"
_RULE = "#9AA0A4"

_FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")
_SCRIPT_FONT = "PinyonScript"


def _register_script_font(pdfmetrics, ttfonts) -> str:
    """
    Registers the bundled Pinyon Script TTF for the name, falling back to a built-in
    italic serif if the font file is ever missing (e.g. stripped from a slim deploy
    package) -- a missing decorative font should never be why certificate rendering
    breaks.
    """
    path = os.path.join(_FONT_DIR, "PinyonScript-Regular.ttf")
    if not os.path.isfile(path):
        return "Times-Italic"
    try:
        pdfmetrics.registerFont(ttfonts.TTFont(_SCRIPT_FONT, path))
        return _SCRIPT_FONT
    except Exception:  # noqa: BLE001 -- decorative font, never worth failing a certificate over
        return "Times-Italic"


def _corner_ornament(pdf, size: float, color, accent):
    """
    Draws one corner ornament in the current (already translated+scaled) coordinate
    space, with local +x/+y both pointing inward from the corner -- callers position and
    mirror it for each of the four corners via translate()/scale() so this only has to
    be written once. Simplified from the source SVG's three nested opacity-layered
    brackets down to two, and one flourish arc instead of the full curl -- reportlab has
    no opacity-per-layer shorthand and hand-tracing every bezier segment wasn't worth it
    for an ornament this small on the page.
    """
    k = size / 66.0  # source SVG's meaningful extent is ~66 of its 120 viewBox units

    pdf.setStrokeColor(color)
    pdf.setLineWidth(1.6)
    pdf.line(4 * k, 44 * k, 4 * k, 4 * k)
    pdf.line(4 * k, 4 * k, 44 * k, 4 * k)

    pdf.setLineWidth(0.8)
    pdf.setStrokeAlpha(0.8)
    pdf.line(14 * k, 54 * k, 14 * k, 14 * k)
    pdf.line(14 * k, 14 * k, 54 * k, 14 * k)
    pdf.setStrokeAlpha(1)

    # Diamond ticks (rotated squares) astride the brackets.
    for cx, cy, s in ((4 * k, 24 * k, 5 * k), (24 * k, 4 * k, 5 * k), (13 * k, 37 * k, 3.5 * k)):
        pdf.saveState()
        pdf.translate(cx, cy)
        pdf.rotate(45)
        pdf.setFillColor(accent)
        pdf.rect(-s / 2, -s / 2, s, s, stroke=0, fill=1)
        pdf.restoreState()

    # One flourish arc echoing the source's nested curl, plus its two end dots.
    pdf.setStrokeColor(color)
    pdf.setStrokeAlpha(0.7)
    pdf.setLineWidth(0.9)
    p = pdf.beginPath()
    p.moveTo(28 * k, 66 * k)
    p.curveTo(46 * k, 66 * k, 66 * k, 46 * k, 66 * k, 28 * k)
    pdf.drawPath(p, stroke=1, fill=0)
    pdf.setStrokeAlpha(1)
    pdf.setFillColor(accent)
    pdf.circle(66 * k, 28 * k, 2.4 * k, stroke=0, fill=1)
    pdf.circle(28 * k, 66 * k, 2.4 * k, stroke=0, fill=1)


def _leaf(pdf, x: float, y: float, h: float, color):
    """The single-blade mark used on the certificate seal -- the sidebar wordmark's
    two-blade gradient version (web-app/src/logo.jsx) doesn't translate to a small flat
    ink stamp, so the certificate uses this simpler glyph instead, same as the source
    design. Path is the source SVG's, scaled to the requested height."""
    w = h * (60.0 / 76.0)
    pdf.saveState()
    pdf.translate(x, y)
    pdf.scale(w / 60.0, h / 76.0)
    pdf.setFillColor(color)
    p = pdf.beginPath()
    p.moveTo(30, 68)
    p.curveTo(2, 40, 8, 14, 30, 2)
    p.curveTo(52, 14, 58, 40, 30, 68)
    p.close()
    pdf.drawPath(p, stroke=0, fill=1)
    pdf.restoreState()


def _letterspaced(pdf, text: str, cx: float, y: float, font: str, size: float, tracking: float):
    """drawCentredString has no letter-spacing option; this measures the tracked width
    itself and draws char-by-char, which is the only way reportlab supports it."""
    pdf.setFont(font, size)
    widths = [pdf.stringWidth(ch, font, size) for ch in text]
    total = sum(widths) + tracking * (len(text) - 1)
    x = cx - total / 2
    for ch, w in zip(text, widths):
        pdf.drawString(x, y, ch)
        x += w + tracking


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
    from reportlab.pdfbase import pdfmetrics, ttfonts
    from reportlab.pdfgen import canvas

    deep, amber, teal = HexColor(_DEEP), HexColor(_AMBER), HexColor(_TEAL)
    ink, muted, rule_color = HexColor(_INK), HexColor(_MUTED), HexColor(_RULE)
    script_font = _register_script_font(pdfmetrics, ttfonts)

    output = io.BytesIO()
    width, height = landscape(letter)
    pdf = canvas.Canvas(output, pagesize=(width, height))
    pdf.setTitle("Ascend certificate - {}".format(training_title))

    # Three nested frames (outer deep-green, middle amber, inner white sheet), same
    # structure as the source .outer/.mid/.sheet stack.
    pdf.setFillColor(deep)
    pdf.rect(0, 0, width, height, stroke=0, fill=1)
    mid_pad = 16
    pdf.setFillColor(amber)
    pdf.rect(mid_pad, mid_pad, width - 2 * mid_pad, height - 2 * mid_pad, stroke=0, fill=1)
    sheet_pad = mid_pad + 11
    sheet_x, sheet_y = sheet_pad, sheet_pad
    sheet_w, sheet_h = width - 2 * sheet_pad, height - 2 * sheet_pad
    pdf.setFillColor(HexColor("#FFFFFF"))
    pdf.setStrokeColor(ink)
    pdf.setLineWidth(1.2)
    pdf.rect(sheet_x, sheet_y, sheet_w, sheet_h, stroke=1, fill=1)

    corner_size = 58
    for (cx, cy, sx, sy) in (
        (sheet_x + 14, sheet_y + sheet_h - 14, 1, -1),   # top-left
        (sheet_x + sheet_w - 14, sheet_y + sheet_h - 14, -1, -1),  # top-right
        (sheet_x + 14, sheet_y + 14, 1, 1),              # bottom-left
        (sheet_x + sheet_w - 14, sheet_y + 14, -1, 1),   # bottom-right
    ):
        pdf.saveState()
        pdf.translate(cx, cy)
        pdf.scale(sx, sy)
        _corner_ornament(pdf, corner_size, deep, amber)
        pdf.restoreState()

    center_x = sheet_x + sheet_w / 2
    # Anchored well below the top edge rather than right under the corner ornaments --
    # the content block (leaf row through "AT ASCEND") is shorter than the sheet is
    # tall, and starting near the top left a large dead gap above the footer. This
    # roughly centers the block in the space between the ornaments and the footer.
    y = sheet_y + sheet_h - 120

    _leaf(pdf, center_x - 62, y - 11, 15, deep)
    pdf.setFillColor(deep)
    _letterspaced(pdf, "ASCEND", center_x + 4, y - 4, "Helvetica-Bold", 13, 3.2)

    y -= 46
    # Decorative swirl divider -- a single wide, shallow wave with a teal dot at center.
    pdf.setStrokeColor(rule_color)
    pdf.setLineWidth(0.9)
    swirl_w = 190
    p = pdf.beginPath()
    p.moveTo(center_x - swirl_w / 2, y)
    p.curveTo(center_x - swirl_w / 4, y - 10, center_x - swirl_w / 8, y + 10, center_x, y)
    p.curveTo(center_x + swirl_w / 8, y - 10, center_x + swirl_w / 4, y + 10, center_x + swirl_w / 2, y)
    pdf.drawPath(p, stroke=1, fill=0)
    pdf.setFillColor(teal)
    pdf.circle(center_x, y, 2.2, stroke=0, fill=1)

    y -= 44
    pdf.setFillColor(deep)
    _letterspaced(pdf, "CERTIFICATE", center_x, y, "Helvetica-Bold", 34, 2.2)

    y -= 24
    pdf.setFillColor(ink)
    _letterspaced(pdf, "OF COMPLETION", center_x, y, "Helvetica-Bold", 13, 4.5)

    y -= 34
    pdf.setFillColor(ink)
    _letterspaced(pdf, "PRESENTED TO :", center_x, y, "Helvetica-Bold", 10, 1.8)

    y -= 58
    pdf.setFillColor(amber)
    name = employee_name or "Employee"
    name_size = 54 if script_font == _SCRIPT_FONT else 40
    # Shrink to fit rather than truncate -- unlike the training title below, cutting
    # someone's actual name short on their own certificate isn't an acceptable tradeoff
    # for a long one.
    max_name_w = sheet_w - 90
    while name_size > 18 and pdf.stringWidth(name, script_font, name_size) > max_name_w:
        name_size -= 1
    pdf.setFont(script_font, name_size)
    pdf.drawCentredString(center_x, y, name)

    y -= 34
    pdf.setStrokeColor(rule_color)
    pdf.setLineWidth(0.7)
    rule_w = 320
    pdf.line(center_x - rule_w / 2, y, center_x + rule_w / 2, y)

    y -= 22
    title = training_title if len(training_title) <= 72 else training_title[:69] + "..."
    pdf.setFillColor(muted)
    pdf.setFont("Helvetica", 9.5)
    pdf.drawCentredString(center_x, y, "FOR COMPLETING A CERTIFICATION PROGRAM FOR")
    y -= 15
    pdf.setFillColor(deep)
    pdf.setFont("Helvetica-Bold", 9.5)
    title_w = pdf.stringWidth(title.upper() + " ", "Helvetica-Bold", 9.5)
    at_w = pdf.stringWidth("AT ", "Helvetica", 9.5)
    ascend_w = pdf.stringWidth("ASCEND", "Helvetica-Bold", 9.5)
    line_w = title_w + at_w + ascend_w
    x = center_x - line_w / 2
    pdf.drawString(x, y, title.upper() + " ")
    x += title_w
    pdf.setFillColor(muted)
    pdf.setFont("Helvetica", 9.5)
    pdf.drawString(x, y, "AT ")
    x += at_w
    pdf.setFillColor(teal)
    pdf.setFont("Helvetica-Bold", 9.5)
    pdf.drawString(x, y, "ASCEND")

    # Verification data the source design doesn't show but the certificate still needs
    # to carry -- kept as small print rather than dropped.
    pdf.setFillColor(muted)
    pdf.setFont("Helvetica", 7.5)
    pdf.drawCentredString(
        center_x, sheet_y + 16,
        "Final score {:.1f}%  |  Issued {}  |  Valid until {}  |  Certificate ID {}".format(
            score, _date(issued_at), _date(expires_at), certificate_id),
    )

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
