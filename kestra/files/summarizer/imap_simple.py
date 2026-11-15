"""ING-1: read UNSEEN mail the way n8n's IMAP trigger (format "simple") does.

Kestra's own mail trigger (io.kestra.plugin.email.MailReceivedTrigger, plugin-email 1.6.8) can't
be used: it returns only the bare sender address, concatenates all text parts into one `body`, and
selects mail by date instead of UNSEEN. See docs/kestra/effort-log.md.

The reference is production n8n 2.40.3, which uses @n8n/imap 0.26.0 on node-imap 0.8.19 (D-017).
What it does, and what this module reproduces:

1. `SEARCH UNSEEN`, then per message `BODYSTRUCTURE` and `BODY.PEEK[HEADER]` (no flag change).
2. Header: split on CRLF, unfold, take `Name: value` lines (node-imap `parseHeader`), and decode
   RFC 2047 encoded words (node-imap `decodeWords`). Everything else in the value stays raw.
3. Body: the first `text/plain` and the first `text/html` part in BODYSTRUCTURE order, each fetched
   with `BODY.PEEK[<part>]`, read as UTF-8, then decoded by transfer encoding the way
   @n8n/imap's `PartData` does. CRLF line endings stay (golden finding G-1). A missing part is ''.
4. After all messages are read, `+FLAGS (\\Seen)` on them (n8n's default post-process action).

The referee is tests/golden/vectors/ing1-imap-simple.json (D-015).
"""

from __future__ import annotations

import base64
import codecs
import contextlib
import imaplib
import re
import ssl
from typing import Any

from .jscompat import js_trim

TOP_LEVEL_HEADERS = ("cc", "date", "from", "subject", "to")

# --- IMAP response parsing (BODYSTRUCTURE) -----------------------------------------------------

Sexp = list[Any] | str | None


def _chunks(data: list[Any]) -> list[tuple[str, bytes]]:
    """imaplib response items as ('raw', bytes) and ('lit', bytes) chunks, in order."""
    out: list[tuple[str, bytes]] = []
    for item in data:
        if isinstance(item, tuple):
            prefix, literal = item
            out.append(("raw", re.sub(rb"\{\d+\}$", b"", prefix)))
            out.append(("lit", literal))
        elif isinstance(item, bytes):
            out.append(("raw", item))
    return out


def _tokens(data: list[Any]) -> list[Any]:
    tokens: list[Any] = []
    for kind, chunk in _chunks(data):
        if kind == "lit":
            tokens.append(("str", chunk.decode("utf-8", "replace")))
            continue
        i = 0
        while i < len(chunk):
            c = chunk[i : i + 1]
            if c in b" \r\n":
                i += 1
            elif c in b"()":
                tokens.append(c.decode())
                i += 1
            elif c == b'"':
                i += 1
                buf = bytearray()
                while i < len(chunk) and chunk[i : i + 1] != b'"':
                    if chunk[i : i + 1] == b"\\":
                        i += 1
                    buf += chunk[i : i + 1]
                    i += 1
                i += 1
                tokens.append(("str", bytes(buf).decode("utf-8", "replace")))
            else:
                m = re.match(rb"[^\s()\"]+", chunk[i:])
                if m is None:
                    raise ValueError(f"cannot tokenize IMAP response at {chunk[i : i + 20]!r}")
                atom = m.group().decode("utf-8", "replace")
                tokens.append(("atom", atom))
                i += len(m.group())
    return tokens


def _parse(tokens: list[Any]) -> list[Any]:
    stack: list[list[Any]] = [[]]
    for item in tokens:
        if item == "(":
            stack.append([])
        elif item == ")":
            done = stack.pop()
            stack[-1].append(done)
        else:
            kind, value = item
            stack[-1].append(None if kind == "atom" and value.upper() == "NIL" else value)
    return stack[0]


def _find_item(parsed: list[Any], name: str) -> Any:
    for element in parsed:
        if isinstance(element, list):
            for i, value in enumerate(element[:-1]):
                if isinstance(value, str) and value.upper() == name:
                    return element[i + 1]
    raise ValueError(f"{name} not in IMAP response")


def flatten_parts(
    struct: list[Any], prefix: str = "", parts: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """node-imap's parseStructure + @n8n/imap's getParts: the leaf parts in order, with partIDs."""
    parts = [] if parts is None else parts
    if struct and isinstance(struct[0], list):  # multipart: leading child structures, then the subtype
        number = 1
        for child in struct:
            if not isinstance(child, list):
                break
            flatten_parts(child, f"{prefix}.{number}" if prefix else str(number), parts)
            number += 1
    elif len(struct) > 5 and isinstance(struct[1], str):
        raw_params = struct[2] if isinstance(struct[2], list) else []
        params = {str(k).lower(): v for k, v in zip(raw_params[::2], raw_params[1::2], strict=False)}
        parts.append(
            {
                "partID": prefix or "1",
                "type": (struct[0] or "").lower(),
                "subtype": struct[1].lower(),
                "charset": params.get("charset"),
                "encoding": struct[5],
            }
        )
    return parts


# --- Header parsing (node-imap 0.8.19 lib/Parser.js) ------------------------------------------

_NOT_LT = r"[^\n\r\u2028\u2029]"
_HDR = re.compile(rf"([^:]+):[ \t]?({_NOT_LT}+)?\Z")
_ENCWORD = re.compile(rf"=\?([^?*]*?)(?:\*{_NOT_LT}*?)?\?([qb])\?({_NOT_LT}*?)\?=", re.I)
_ENCWORD_END = re.compile(rf"=\?([^?*]*?)(?:\*{_NOT_LT}*?)?\?([qb])\?({_NOT_LT}*?)\?=\Z", re.I)
_ENCWORD_BEGIN = re.compile(rf"[ \t]=\?([^?*]*?)(?:\*{_NOT_LT}*?)?\?([qb])\?({_NOT_LT}*?)\?=", re.I)
_QENC = re.compile(r"=([a-fA-F0-9]{2})|_")
_LWS_ONLY = re.compile(r"[ \t]*")

# WHATWG encoding labels that differ from Python's codec names (node-imap uses a WHATWG TextDecoder).
_WHATWG_ALIASES = {
    **dict.fromkeys(
        ("ascii", "us-ascii", "iso-8859-1", "iso8859-1", "iso88591", "iso_8859-1", "latin1", "l1", "cp1252"),
        "windows-1252",
    ),
    "gb2312": "gbk",
}


def _whatwg_decode(data: bytes, label: str) -> str | None:
    """TextDecoder(label).decode(data), non-fatal; None for an unknown label."""
    name = _WHATWG_ALIASES.get(label.strip().lower(), label.strip().lower())
    if name == "windows-1252":  # WHATWG maps the 5 undefined cp1252 bytes to C1 controls
        return "".join(bytes([b]).decode("cp1252", "ignore") or chr(b) for b in data)
    try:
        codecs.lookup(name)
    except LookupError:
        return None
    return data.decode(name, "replace")


def _node_base64(text: str) -> bytes:
    """Buffer.from(text, 'base64'): skips characters outside the alphabet, padding optional."""
    clean = re.sub(r"[^A-Za-z0-9+/\-_=]", "", text).replace("-", "+").replace("_", "/")
    clean = clean.split("=", 1)[0]
    clean = clean[: len(clean) - (len(clean) % 4 == 1)]
    return base64.b64decode(clean + "=" * (-len(clean) % 4))


def _binary(text: str) -> bytes:
    """Buffer.from(text, 'binary'): the low byte of each UTF-16 code unit."""
    return text.encode("utf-16-le", "surrogatepass")[0::2]


def decode_words(value: str) -> str:
    """RFC 2047 encoded words, decoded in place (node-imap decodeWords, without cross-header state)."""
    words: list[dict[str, Any]] = []
    pend = -1
    for m in _ENCWORD.finditer(value):
        consecutive = pend > -1 and _LWS_ONLY.fullmatch(value[pend : m.start()]) is not None
        word = {
            "consecutive": consecutive,
            "charset": m.group(1).lower(),
            "encoding": m.group(2).lower(),
            "chunk": m.group(3),
            "start": m.start(),
            "end": m.end(),
            "pend": pend,
        }
        last = words[-1] if words else None
        if (
            consecutive
            and last is not None
            and word["charset"] == last["charset"]
            and word["encoding"] == last["encoding"] == "q"
        ):
            last["end"] = m.end()
            last["chunk"] += word["chunk"]
        else:
            words.append(word)
        pend = m.end()

    replacements: list[tuple[int, int, str]] = []
    i = 0
    while i < len(words):
        word = words[i]
        if word["encoding"] == "q":
            data = _binary(_QENC.sub(lambda q: " " if q.group(0) == "_" else chr(int(q.group(1), 16)), word["chunk"]))
        else:
            data = _node_base64(word["chunk"])
        end = word["end"]
        # A multi-byte character split across adjacent encoded words: join them until it decodes.
        while (
            word["encoding"] == "b"
            and i + 1 < len(words)
            and words[i + 1]["consecutive"]
            and words[i + 1]["encoding"] == "b"
            and words[i + 1]["charset"] == word["charset"]
            and not _decodes_cleanly(data, word["charset"])
        ):
            i += 1
            data += _node_base64(words[i]["chunk"])
            end = words[i]["end"]
        decoded = _whatwg_decode(data, word["charset"])
        if decoded is None:  # unknown charset: node-imap substitutes the raw bytes
            replacements.append((word["start"], end, data.decode("latin-1")))
        else:
            replacements.append((word["pend"] if word["consecutive"] else word["start"], end, decoded))
        i += 1

    for start, end, text in reversed(replacements):
        value = value[:start] + text + value[end:]
    return value


def _decodes_cleanly(data: bytes, label: str) -> bool:
    name = _WHATWG_ALIASES.get(label.strip().lower(), label.strip().lower())
    try:
        data.decode("cp1252" if name == "windows-1252" else name)
    except (UnicodeDecodeError, LookupError):
        return False
    return True


def parse_header(text: str) -> dict[str, list[str]]:
    lines = text.split("\r\n")
    header: dict[str, list[str]] = {}
    name: str | None = None
    for i, line in enumerate(lines):
        if not line:
            break
        if line[0] in "\t ":
            if name is None or name not in header:
                continue
            value = line
            if i > 0 and _ENCWORD_END.search(lines[i - 1]) and _ENCWORD_BEGIN.match(value):
                value = value[1:]
            header[name][-1] += value
            continue
        m = _HDR.match(line)
        if not m:
            break
        name = js_trim(m.group(1).lower())
        if m.group(2):
            header.setdefault(name, []).append(m.group(2))
        else:
            header[name] = [""]
    return {k: [decode_words(v) for v in values] for k, values in header.items()}


# --- Part decoding (@n8n/imap 0.26.0 dist/part-data.js) ----------------------------------------

_QP_TRAILING_WS = re.compile(r"[\t ](?=[\n\r\u2028\u2029]|(?![\s\S]))")
_QP_SOFT_BREAK = re.compile(r"=(?:\r\n?|\n|(?![\s\S]))")
_QP_ESCAPE = re.compile(r"=([a-fA-F0-9]{2})")


def qp_decode(text: str) -> str:
    """quoted-printable 1.0.1 `decode`: escapes become code points 0-255; CRLF stays."""
    text = _QP_TRAILING_WS.sub("", text)
    text = _QP_SOFT_BREAK.sub("", text)
    return _QP_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), text)


def _utf8_package_decode(text: str) -> str:
    """utf8 3.0.0 `decode`, wrapped in @n8n/imap's try/catch (returns the input when it throws)."""
    try:
        return bytes(ord(c) for c in text).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return text


def decode_part(data: bytes, encoding: str | None, charset: str | None) -> str:
    """PartData.fromData(data, encoding, charset).toString(), where `data` is the fetched part."""
    text = data.decode("utf-8", "replace")  # getMessage(): chunk.toString('utf8')
    enc = (encoding or "7BIT").upper()
    if enc == "BASE64":
        return _node_base64(text).decode("utf-8", "replace")
    if enc == "QUOTED-PRINTABLE":
        decoded = qp_decode(text)
        if charset is not None and charset.upper() == "UTF-8":
            decoded = _utf8_package_decode(decoded)
        return decoded.encode("utf-8", "replace").decode("utf-8", "replace")
    if enc == "7BIT":
        return "".join(chr(b & 0x7F) for b in text.encode("utf-8", "replace"))
    if enc in ("8BIT", "BINARY"):
        raw = text.encode("utf-8", "replace")
        decoded = _whatwg_decode(raw, charset or "utf-8")
        return decoded if decoded is not None else raw.decode("utf-8", "replace")
    raise ValueError(f"Unknown encoding {encoding}")


# --- The trigger ---------------------------------------------------------------------------------


def _fetch_one(client: imaplib.IMAP4, uid: str, item: str) -> list[Any]:
    status, data = client.uid("FETCH", uid, f"({item})")
    if status != "OK":
        raise imaplib.IMAP4.error(f"FETCH {item} for UID {uid} failed: {data!r}")
    return [d for d in data if d is not None]


def _literal(data: list[Any]) -> bytes:
    for item in data:
        if isinstance(item, tuple):
            return item[1]
    return b""  # the server sent the section as a quoted string or NIL: empty


def _download_text(client: imaplib.IMAP4, uid: str, parts: list[dict[str, Any]], subtype: str) -> str:
    part = next((p for p in parts if p["type"].upper() == "TEXT" and p["subtype"].upper() == subtype.upper()), None)
    if part is None:
        return ""
    try:
        data = _literal(_fetch_one(client, uid, f"BODY.PEEK[{part['partID']}]"))
        return decode_part(data, part["encoding"], part["charset"])
    except (ValueError, imaplib.IMAP4.error):
        return ""  # downloadText() swallows errors and returns ''


def read_message(client: imaplib.IMAP4, uid: str) -> dict[str, Any]:
    struct = _find_item(_parse(_tokens(_fetch_one(client, uid, "BODYSTRUCTURE"))), "BODYSTRUCTURE")
    parts = flatten_parts(struct) if isinstance(struct, list) else []
    header = parse_header(_literal(_fetch_one(client, uid, "BODY.PEEK[HEADER]")).decode("utf-8", "replace"))
    item: dict[str, Any] = {
        "textHtml": _download_text(client, uid, parts, "html"),
        "textPlain": _download_text(client, uid, parts, "plain"),
    }
    for name, values in header.items():
        if values and name in TOP_LEVEL_HEADERS:
            item[name] = values[0]
    return item


def fetch_unseen(
    *, host: str, port: int, secure: bool, user: str, password: str, mailbox: str, mark_seen: bool = True
) -> list[dict[str, Any]]:
    """Every UNSEEN message in `mailbox` as n8n's simple item, then mark them all \\Seen."""
    client = (
        imaplib.IMAP4_SSL(host, port, ssl_context=ssl.create_default_context()) if secure else imaplib.IMAP4(host, port)
    )
    try:
        client.login(user, password)
        status, data = client.select(_quote(mailbox))
        if status != "OK":
            raise imaplib.IMAP4.error(f"SELECT {mailbox} failed: {data!r}")
        status, data = client.uid("SEARCH", None, "UNSEEN")
        if status != "OK":
            raise imaplib.IMAP4.error(f"SEARCH UNSEEN failed: {data!r}")
        uids = data[0].decode().split() if data and data[0] else []
        items = [read_message(client, uid) for uid in uids]
        if uids and mark_seen:
            client.uid("STORE", ",".join(uids), "+FLAGS", "(\\Seen)")
        return items
    finally:
        with contextlib.suppress(imaplib.IMAP4.error, OSError):
            client.logout()


def _quote(mailbox: str) -> str:
    return '"' + mailbox.replace("\\", "\\\\").replace('"', '\\"') + '"'
