"""ING-1 against the golden vector (ing1-imap-simple.json), plus unit checks of the decoders.

The integration test reads the seeded GreenMail of whichever stack is running (`make kestra-up`
seeds it), without marking anything as read. It's skipped when GreenMail isn't reachable.
Connection settings default to the host ports in .env.example.
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path

import pytest
from summarizer import imap_simple

VECTORS = Path(__file__).resolve().parents[2] / "tests" / "golden" / "vectors"
IMAP = {
    "host": os.environ.get("WFX_IMAP_HOST", "localhost"),
    "port": int(os.environ.get("WFX_IMAP_PORT", "3143")),
    "secure": False,
    "user": os.environ.get("WFX_IMAP_USER", "newsletters"),
    "password": os.environ.get("WFX_IMAP_PASSWORD", "local-only"),
    "mailbox": os.environ.get("WFX_IMAP_MAILBOX", "n8n"),
}


def _greenmail_up() -> bool:
    try:
        socket.create_connection((IMAP["host"], IMAP["port"]), timeout=1).close()
    except OSError:
        return False
    return True


@pytest.mark.skipif(not _greenmail_up(), reason="GreenMail not reachable; run make kestra-up")
def test_ing1_matches_golden_vector() -> None:
    expected = json.loads((VECTORS / "ing1-imap-simple.json").read_text(encoding="utf-8"))["cases"]
    items = imap_simple.fetch_unseen(**IMAP, mark_seen=False)
    assert len(items) == len(expected), "mailbox must hold exactly the 9 seeded fixtures, all UNSEEN (make kestra-seed)"
    # The seed script appends the fixtures in file-name order, so UID order matches the vector order.
    for item, case in zip(items, expected, strict=True):
        assert item == case["output"], case["id"]


def test_qp_keeps_crlf_and_drops_soft_breaks() -> None:
    assert imap_simple.qp_decode("caf=C3=A9 =\r\nbar \r\nend") == "caf\xc3\xa9 bar\r\nend"


def test_qp_utf8_charset() -> None:
    data = b"caf=C3=A9\r\n"
    assert imap_simple.decode_part(data, "quoted-printable", "utf-8") == "café\r\n"
    assert imap_simple.decode_part(data, "quoted-printable", "iso-8859-1") == "cafÃ©\r\n"


def test_base64_is_read_as_utf8_whatever_the_charset() -> None:
    assert imap_simple.decode_part(b"Y2Fmw6kNCg==", "BASE64", "iso-8859-1") == "café\r\n"


def test_encoded_words() -> None:
    assert imap_simple.decode_words("=?UTF-8?B?8J+noA==?= The Neuron <n@x>") == "🧠 The Neuron <n@x>"
    assert imap_simple.decode_words("=?utf-8?q?a_b?= =?utf-8?q?c?=") == "a bc"
    assert imap_simple.decode_words("=?utf-8?B?8J+n?= =?utf-8?B?oA==?=") == "🧠"  # split character


def test_header_unfolding_keeps_raw_quotes() -> None:
    header = imap_simple.parse_header('From: "Morning Brew"\r\n <crew@x.example>\r\nSubject: Hi\r\n\r\n')
    assert header == {"from": ['"Morning Brew" <crew@x.example>'], "subject": ["Hi"]}
