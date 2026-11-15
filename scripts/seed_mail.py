#!/usr/bin/env python3
"""Load the fixture emails into the IMAP mailbox the ingest flows read (spec ING-1).

Uses IMAP APPEND rather than SMTP so messages land directly in the ``IMAP_MAILBOX`` folder
(Gmail label ``n8n`` in production) as UNSEEN, with their original Date as INTERNALDATE.

Configuration comes from the shared env names (see .env.example):
    IMAP_HOST, IMAP_PORT, IMAP_SECURE, IMAP_USER, IMAP_PASSWORD, IMAP_MAILBOX

Usage:
    seed_mail.py [--fixtures DIR] [--purge] [--only GLOB] [--check]
      --purge   delete everything in the mailbox first
      --only    append only fixtures matching GLOB (e.g. '0[12]-*')
      --check   append nothing; print message/unseen counts and exit
"""

from __future__ import annotations

import argparse
import imaplib
import os
import sys
from email.parser import BytesHeaderParser
from email.utils import parsedate_to_datetime
from pathlib import Path


def connect(host: str | None = None, port: int | None = None) -> imaplib.IMAP4:
    """Log in with the IMAP_* settings. ``host``/``port`` override them (the parity suite runs on the host)."""
    host = host or os.environ.get("IMAP_HOST", "localhost")
    port = port or int(os.environ.get("IMAP_PORT", "3143"))
    secure = os.environ.get("IMAP_SECURE", "false").lower() == "true"
    conn: imaplib.IMAP4 = imaplib.IMAP4_SSL(host, port) if secure else imaplib.IMAP4(host, port)
    conn.login(os.environ["IMAP_USER"], os.environ["IMAP_PASSWORD"])
    return conn


def quoted(mailbox: str) -> str:
    return '"' + mailbox.replace("\\", "\\\\").replace('"', '\\"') + '"'


def counts(conn: imaplib.IMAP4, mailbox: str) -> tuple[int, int]:
    status, data = conn.status(quoted(mailbox), "(MESSAGES UNSEEN)")
    if status != "OK":
        raise SystemExit(f"STATUS {mailbox} failed: {data}")
    text = data[0].decode() if isinstance(data[0], bytes) else str(data[0])
    tokens = text[text.index("(") + 1 : text.rindex(")")].split()
    values = dict(zip(tokens[::2], tokens[1::2], strict=True))
    return int(values["MESSAGES"]), int(values["UNSEEN"])


def ensure_mailbox(conn: imaplib.IMAP4, mailbox: str) -> None:
    status, _ = conn.select(quoted(mailbox))
    if status != "OK":
        status, data = conn.create(quoted(mailbox))
        if status != "OK":
            raise SystemExit(f"CREATE {mailbox} failed: {data}")
    if conn.state == "SELECTED":
        conn.close()


def purge(conn: imaplib.IMAP4, mailbox: str) -> None:
    conn.select(quoted(mailbox))
    conn.store("1:*", "+FLAGS.SILENT", r"(\Deleted)")
    conn.expunge()
    conn.close()


def append(conn: imaplib.IMAP4, mailbox: str, path: Path) -> None:
    """APPEND one .eml with no flags (UNSEEN) and its Date header as INTERNALDATE."""
    raw = path.read_bytes()
    date = BytesHeaderParser().parsebytes(raw)["Date"]
    internal = imaplib.Time2Internaldate(parsedate_to_datetime(date)) if date else None
    status, data = conn.append(quoted(mailbox), None, internal, raw)  # no flags -> UNSEEN
    if status != "OK":
        raise SystemExit(f"APPEND {path.name} failed: {data}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fixtures", default=os.environ.get("FIXTURES_DIR", "fixtures/emails"))
    parser.add_argument("--purge", action="store_true")
    parser.add_argument("--only", default="*")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    mailbox = os.environ.get("IMAP_MAILBOX", "n8n")

    conn = connect()
    try:
        ensure_mailbox(conn, mailbox)
        if args.check:
            total, unseen = counts(conn, mailbox)
            print(f"mailbox={mailbox} messages={total} unseen={unseen}")
            return 0
        if args.purge:
            purge(conn, mailbox)
        files = sorted(Path(args.fixtures).glob(f"{args.only}.eml"))
        if not files:
            print(
                f"no fixtures matching {args.only!r} in {args.fixtures}",
                file=sys.stderr,
            )
            return 1
        for path in files:
            append(conn, mailbox, path)
            print(f"appended {path.name}")
        total, unseen = counts(conn, mailbox)
        print(f"mailbox={mailbox} messages={total} unseen={unseen}")
        return 0
    finally:
        conn.logout()


if __name__ == "__main__":
    raise SystemExit(main())
