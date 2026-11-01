"""QR codes for worker ID cards.

The QR code holds only an opaque random token, never personal details. Supervisors scan
it to record attendance; the token is looked up server-side within their own company.
"""

import re

import segno

PAYLOAD_PREFIX = "BUILDLINK-WORKER:"
TOKEN_PATTERN = re.compile(r"[0-9a-f]{32}")


def qr_payload(worker):
    return f"{PAYLOAD_PREFIX}{worker.qr_token}"


def parse_qr_payload(text):
    """Extract the worker token from scanned or typed text, or return None."""
    value = (text or "").strip()
    if value.upper().startswith(PAYLOAD_PREFIX):
        value = value[len(PAYLOAD_PREFIX):]
    value = value.lower()
    return value if TOKEN_PATTERN.fullmatch(value) else None


def qr_svg(worker, scale=6):
    """An inline SVG QR code for the worker's ID card."""
    return segno.make(qr_payload(worker), error="m").svg_inline(scale=scale, dark="#153d32", light="#ffffff")
