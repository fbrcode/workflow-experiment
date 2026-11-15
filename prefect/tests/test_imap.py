"""ING-1: the IMAP read against the golden vector, plus decoder cases the fixtures don't reach.

`test_ing1_against_greenmail` reads the seeded mailbox of whichever stack is running, through its
host port, without marking anything as read. It's skipped when GreenMail isn't reachable. The
mailbox must hold the 9 fixtures, all UNSEEN: `make prefect-seed` (or `make seed`) resets it.
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path

import pytest

from email_summarizer import imap

ROOT = Path(__file__).resolve().parents[2]
VECTOR = json.loads((ROOT / "tests/golden/vectors/ing1-imap-simple.json").read_text(encoding="utf-8"))
GREENMAIL = {
    "host": os.environ.get("TEST_IMAP_HOST", "localhost"),
    "port": int(os.environ.get("TEST_IMAP_PORT", "3143")),  # MAIL_IMAP_HOST_PORT in .env.example
    "secure": False,
    "user": os.environ.get("TEST_IMAP_USER", "newsletters"),
    "password": os.environ.get("TEST_IMAP_PASSWORD", "local-only"),
    "mailbox": os.environ.get("TEST_IMAP_MAILBOX", "n8n"),
}


def _greenmail_reachable() -> bool:
    try:
        socket.create_connection((GREENMAIL["host"], GREENMAIL["port"]), timeout=1).close()
    except OSError:
        return False
    return True


@pytest.mark.parametrize("case", VECTOR["cases"], ids=lambda case: case["id"])
def test_ing1_fixture_file(case: dict) -> None:
    """The parser on the fixture file itself, which is what GreenMail stores (D-013)."""
    raw = (ROOT / case["input"]["fixture"]).read_bytes()
    assert imap.simple_item(raw) == case["output"]


@pytest.mark.skipif(not _greenmail_reachable(), reason="GreenMail isn't reachable; run make prefect-up")
def test_ing1_against_greenmail() -> None:
    items = imap.fetch_unseen(**GREENMAIL, mark_seen=False)
    expected = [case["output"] for case in VECTOR["cases"]]
    assert len(items) == len(expected), "expected the 9 fixtures, all UNSEEN: make prefect-seed"
    assert items == expected  # appended in file-name order, so UID order is the vector's order


def test_qp_follows_the_quoted_printable_package() -> None:
    # One trailing space or tab per line is dropped (also before CR), soft breaks join lines, CRLF stays.
    # In "b =" the char before the line break is "=", so the space survives the first step.
    assert imap.qp_decode("a=C3=A9  \r\nb =\r\nc") == "a\xc3\xa9 \r\nb c"


def test_qp_reads_utf8_only_for_a_utf8_charset() -> None:
    assert imap.decode_part("caf=C3=A9\r\n", "QUOTED-PRINTABLE", "utf-8") == "café\r\n"
    assert imap.decode_part("caf=C3=A9\r\n", "QUOTED-PRINTABLE", "iso-8859-1") == "caf\xc3\xa9\r\n"
    assert imap.decode_part("caf=E9", "QUOTED-PRINTABLE", "UTF-8") == "caf\xe9"  # invalid UTF-8: left as is


def test_base64_is_read_as_utf8_whatever_the_charset() -> None:
    assert imap.decode_part("Y2Fmw6kNCg==\r\n", "BASE64", "iso-8859-1") == "café\r\n"


def test_uuencode_like_n8n() -> None:
    # "begin" line, one data line ("Cat" is `#0V%T`), then the three lines n8n drops ("`", "end", "").
    assert imap.decode_part("begin 644 x\n#0V%T\n`\nend\n", "UUENCODE", None) == "Cat"


def test_7bit_clears_the_high_bit() -> None:
    assert imap.decode_part("caf\xe9", "7BIT", None) == "cafC)"


def test_encoded_words() -> None:
    assert imap.decode_words("=?utf-8?b?8J+noCBUaGUgTmV1cm9u?= <hi@x.example>") == "🧠 The Neuron <hi@x.example>"
    assert imap.decode_words("=?UTF-8?Q?a_b?= =?utf-8?q?c?= d") == "a bc d"  # space between words dropped
    assert imap.decode_words("=?utf-8?B?8J+n?= =?utf-8?B?oA==?=") == "🧠"  # a character split across words
    assert imap.decode_words("=?x-unknown?q?caf=E9?=") == "caf\xe9"  # unknown charset: raw bytes


def test_parse_header_unfolds_and_keeps_quotes() -> None:
    header = imap.parse_header('From: "Morning Brew"\r\n <crew@x.example>\r\nSubject:  two  spaces\r\n\r\n')
    assert header == {"from": ['"Morning Brew" <crew@x.example>'], "subject": [" two  spaces"]}
