"""ING-1: read UNSEEN mail and return what n8n's IMAP trigger hands to ING-2.

n8n 2.40.3's `emailReadImap` v2 node, format "simple", uses `@n8n/imap@0.26.0` on `imap@0.8.19`.
For each UNSEEN message it returns the first `text/html` and `text/plain` parts, decoded by
`PartData`, plus the `cc`, `date`, `from`, `subject` and `to` headers as node-imap's
`parseHeader` gives them, then marks the messages `\\Seen`. The golden vector
`ing1-imap-simple.json` is the referee (D-015). What it pins down (G-1, G-2):

- Bodies keep their CRLF line endings. Header values are RFC 2047-decoded but otherwise raw.
- A part's bytes are read as UTF-8 text first. BASE64 is then decoded and read as UTF-8 whatever
  the charset; QUOTED-PRINTABLE is decoded by the `quoted-printable` package and read as UTF-8 only
  when the charset is exactly `UTF-8`; 7BIT clears the high bit; 8BIT/BINARY use the charset.
  A part that fails to decode gives `''`.

The MIME structure comes from Python's `email` parser on the whole message (one FETCH per message),
not from the server's BODYSTRUCTURE plus a FETCH per part as in n8n. For well-formed mail both
give the same parts; the decoding rules above are ported from the n8n packages.
"""

from __future__ import annotations

import base64
import binascii
import codecs
import email
import imaplib
import re
from dataclasses import dataclass, field
from email.message import Message
from email.policy import compat32
from typing import Final

from email_summarizer import js

TOP_LEVEL_HEADERS: Final = ("cc", "date", "from", "subject", "to")

# node-imap lib/Parser.js
_HEADER_LINE: Final = re.compile(rf"^([^:]+):[ \t]?({js.DOT}+)?{js.END}")  # RE_HDR
_ENCODED_WORD: Final = re.compile(rf"=\?([^?*]*?)(?:\*{js.DOT}*?)?\?([qbQB])\?({js.DOT}*?)\?=")  # RE_ENCWORD
_ENCODED_WORD_AT_END: Final = re.compile(rf"=\?([^?*]*?)(?:\*{js.DOT}*?)?\?([qbQB])\?({js.DOT}*?)\?={js.END}")
_ENCODED_WORD_AFTER_WSP: Final = re.compile(rf"^[ \t]=\?([^?*]*?)(?:\*{js.DOT}*?)?\?([qbQB])\?({js.DOT}*?)\?=")
_LWS_ONLY: Final = re.compile(r"[ \t]*")
_Q_ESCAPE: Final = re.compile(r"=([a-fA-F0-9]{2})|_")
# quoted-printable@1.0.1 decode()
_QP_TRAILING_WSP: Final = re.compile(rf"[\t ]{js.EOL}")
_QP_SOFT_BREAK: Final = re.compile(r"=(?:\r\n?|\n|\Z)")
_QP_ESCAPE: Final = re.compile(r"=([a-fA-F0-9]{2})")
_NOT_BASE64: Final = re.compile(r"[^A-Za-z0-9+/\-_]")
# The WHATWG Encoding Standard (node-imap's TextDecoder) reads these labels as windows-1252.
_WINDOWS_1252_LABELS: Final = frozenset({"ascii", "us-ascii", "iso-8859-1", "iso8859-1", "latin1", "l1", "cp819"})


def fetch_unseen(
    *, host: str, port: int, secure: bool, user: str, password: str, mailbox: str, mark_seen: bool = True
) -> list[dict[str, str]]:
    """Search UNSEEN in `mailbox`, return each message as n8n's "simple" item, then mark them read."""
    client = imaplib.IMAP4_SSL(host, port) if secure else imaplib.IMAP4(host, port)
    with client as imap:
        imap.login(user, password)
        _check(imap.select(_quote(mailbox)), "SELECT")
        uids = [uid.decode() for uid in _check(imap.uid("SEARCH", "UNSEEN"), "SEARCH")[0].split()]
        items = []
        for uid in uids:
            response = _check(imap.uid("FETCH", uid, "(BODY.PEEK[])"), "FETCH")
            raw = next(part[1] for part in response if isinstance(part, tuple))
            items.append(simple_item(raw))
        if mark_seen and uids:
            _check(imap.uid("STORE", ",".join(uids), "+FLAGS", r"(\Seen)"), "STORE")
    return items


def simple_item(raw: bytes) -> dict[str, str]:
    """One message as the n8n node's "simple" format: text bodies plus the top-level headers."""
    message = email.message_from_bytes(raw, policy=compat32)
    header_block = raw.split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n"  # what FETCH BODY[HEADER] returns
    headers = parse_header(header_block.decode("utf-8", "replace"))
    item = {"textHtml": body_text(message, "html"), "textPlain": body_text(message, "plain")}
    for name in TOP_LEVEL_HEADERS:
        if name in headers:
            item[name] = headers[name][0]
    return item


def body_text(message: Message, subtype: str) -> str:
    """`downloadText`: the first text/<subtype> part, decoded; `''` when there is none or it fails."""
    for part in message.walk():
        if part.is_multipart() or part.get_content_maintype() != "text" or part.get_content_subtype() != subtype:
            continue
        payload = part.get_payload()
        if not isinstance(payload, str):
            return ""
        data = payload.encode("ascii", "surrogateescape").decode("utf-8", "replace")  # chunk.toString('utf8')
        encoding = str(part.get("Content-Transfer-Encoding", "7BIT")).strip().upper()
        charset = part.get_param("charset")
        try:
            return decode_part(data, encoding, charset if isinstance(charset, str) else None)
        except (ValueError, LookupError, binascii.Error):
            return ""
    return ""


def decode_part(data: str, encoding: str, charset: str | None) -> str:
    """`PartData.fromData(data, encoding, charset).toString()` from @n8n/imap 0.26.0."""
    if encoding == "BASE64":
        return _node_base64(data).decode("utf-8", "replace")
    if encoding == "QUOTED-PRINTABLE":
        decoded = qp_decode(data)
        if charset is not None and charset.upper() == "UTF-8":
            try:  # asUtf8(): utf8.decode() on the byte string, or the input unchanged if that throws
                return bytes(ord(c) & 0xFF for c in decoded).decode("utf-8")
            except UnicodeDecodeError:
                return decoded
        return decoded
    if encoding == "7BIT":
        return bytes(b & 0x7F for b in data.encode("utf-8")).decode("latin-1")  # toString('ascii')
    if encoding in ("8BIT", "BINARY"):
        return data.encode("utf-8").decode(_codec(charset or "utf-8") or "utf-8", "replace")
    if encoding == "UUENCODE":
        lines = data.split("\n")
        # parts.splice(1, parts.length - 4).join(''): drop the first line and the last three.
        return _uu_decode("".join(lines[1 : len(lines) - 3])).decode("utf-8", "replace")
    raise ValueError(f"Unknown encoding {encoding}")  # n8n's downloadText turns this into ''


def _uu_decode(text: str) -> bytes:
    """@n8n/imap's `uuDecode`, line-length bytes and all, on the joined lines."""
    out = bytearray()
    i = 0
    while i < len(text):
        length = (ord(text[i]) - 0x20) & 0x3F
        i += 1
        if length == 0:
            break
        written = 0
        while i < len(text) and written < length:
            sextets = []
            for _ in range(4):
                sextets.append((ord(text[i]) - 0x20) & 0x3F if i < len(text) else 0)
                i += 1 if i < len(text) else 0
            c0, c1, c2, c3 = sextets
            for byte in (((c0 << 2) | (c1 >> 4)) & 0xFF, ((c1 << 4) | (c2 >> 2)) & 0xFF, ((c2 << 6) | c3) & 0xFF):
                if written < length:
                    out.append(byte)
                    written += 1
    return bytes(out)


def qp_decode(data: str) -> str:
    """quoted-printable@1.0.1 `decode()`: a string in, a "binary" string (one char per byte) out."""
    data = _QP_TRAILING_WSP.sub("", data)
    data = _QP_SOFT_BREAK.sub("", data)
    return _QP_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), data)


def parse_header(text: str) -> dict[str, list[str]]:
    """node-imap `parseHeader`: lowercased names, a list of values each, folded lines joined, words decoded."""
    lines = text.split("\r\n")
    header: dict[str, list[str]] = {}
    name: str | None = None
    for i, line in enumerate(lines):
        if not line:
            break  # the blank line that ends the header
        if line[0] in "\t ":
            if name is None or name not in header:
                continue  # a folded line with nothing to fold into
            value = line
            if _ENCODED_WORD_AT_END.search(lines[i - 1]) and _ENCODED_WORD_AFTER_WSP.match(value):
                value = value[1:]  # whitespace between folded encoded words is ignored (RFC 2047)
            header[name][-1] += value
            continue
        match = _HEADER_LINE.match(line)
        if not match:
            break
        name = js.trim(match.group(1).lower())
        if match.group(2):
            header.setdefault(name, []).append(match.group(2))
        else:
            header[name] = [""]
    return {key: [decode_words(v) for v in values] for key, values in header.items()}


@dataclass
class _Word:
    start: int
    end: int
    charset: str
    encoding: str
    chunk: str
    consecutive: bool  # only spaces or tabs since the previous encoded word
    previous_end: int
    data: bytes = field(default=b"")


def decode_words(value: str) -> str:
    """node-imap `decodeWords`: decode RFC 2047 encoded words and drop the whitespace between them.

    Adjacent Q words in the same charset are joined before decoding. A word whose bytes end in the
    middle of a character is joined with the next adjacent word in the same charset, as node-imap
    does for mailers that split characters across words.
    """
    words: list[_Word] = []
    previous_end = -1
    for match in _ENCODED_WORD.finditer(value):
        consecutive = previous_end > -1 and _LWS_ONLY.fullmatch(value, previous_end, match.start()) is not None
        word = _Word(
            start=match.start(),
            end=match.end(),
            charset=match.group(1).lower(),
            encoding=match.group(2).lower(),
            chunk=match.group(3),
            consecutive=consecutive,
            previous_end=previous_end,
        )
        last = words[-1] if words else None
        if last and consecutive and word.encoding == "q" and (last.charset, last.encoding) == (word.charset, "q"):
            last.end = word.end
            last.chunk += word.chunk
        else:
            words.append(word)
        previous_end = match.end()

    replacements: list[tuple[int, int, str]] = []
    pending: _Word | None = None  # a partial word waiting for the rest of its character

    def drop_pending() -> None:
        # A partial that is never completed becomes one U+FFFD per byte, as in node-imap.
        if pending is not None:
            replacements.append((pending.start, pending.end, chr(0xFFFD) * len(pending.data)))

    for index, word in enumerate(words):
        word.data = _q_bytes(word.chunk) if word.encoding == "q" else _node_base64(word.chunk)
        codec = _codec(word.charset)
        if codec is None:  # unknown charset: the raw bytes, one char each
            drop_pending()
            pending = None
            replacements.append((word.start, word.end, word.data.decode("latin-1")))
            continue
        start = word.previous_end if word.consecutive else word.start
        data = word.data
        if pending is not None and word.consecutive and pending.charset == word.charset:
            start, data = pending.start, pending.data + data
        else:
            drop_pending()
        pending = None
        try:
            text = data.decode(codec)
        except UnicodeDecodeError as error:
            following = words[index + 1] if index + 1 < len(words) else None
            if error.reason == "unexpected end of data" and following and following.consecutive:
                pending = _Word(start, word.end, word.charset, word.encoding, "", True, -1, data)
                continue
            text = data.decode(codec, "replace")
        replacements.append((start, word.end, text))
    drop_pending()

    replacements.sort()
    for start, end, text in reversed(replacements):
        value = value[:start] + text + value[end:]
    return value


def _q_bytes(chunk: str) -> bytes:
    text = _Q_ESCAPE.sub(lambda m: " " if m.group(0) == "_" else chr(int(m.group(1), 16)), chunk)
    return bytes(ord(c) & 0xFF for c in text)  # new Buffer(str, 'binary')


def _node_base64(data: str) -> bytes:
    """Node's `Buffer.from(data, 'base64')`: skips anything outside the alphabet, stops at padding."""
    cleaned = _NOT_BASE64.sub("", data.split("=", 1)[0]).replace("-", "+").replace("_", "/")
    if len(cleaned) % 4 == 1:
        cleaned = cleaned[:-1]  # a lone trailing sextet carries no whole byte
    return base64.b64decode(cleaned + "=" * (-len(cleaned) % 4))


def _codec(label: str) -> str | None:
    label = label.strip().lower()
    if label in _WINDOWS_1252_LABELS:
        return "cp1252"
    try:
        return codecs.lookup(label).name
    except LookupError:
        return None


def _quote(mailbox: str) -> str:
    return '"' + mailbox.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _check(response: tuple[str, list], command: str) -> list:
    status, data = response
    if status != "OK":
        raise imaplib.IMAP4.error(f"IMAP {command} failed: {data!r}")
    return data
