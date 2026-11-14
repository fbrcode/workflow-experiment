"""Telegram message-text parsing for the mock Bot API.

Implements the documented formatting rules (https://core.telegram.org/bots/api#formatting-options)
closely enough to (a) reject what Telegram would reject, so fault F-3 can happen naturally, and
(b) return the rendered plain ``text`` + ``entities`` that Telegram echoes back in the sent
Message, because the baseline stores ``result.text`` (spec SND-4).

Known approximations (documented in docs/infra.md):
- error descriptions mimic Telegram's wording but are not byte-identical in every case;
- custom emoji, expandable blockquotes and nested-quote edge cases are not modelled;
- in HTML mode a bare ``&`` or ``>`` is accepted literally (Telegram is lenient in practice).
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

MAX_MESSAGE_UTF16 = 4096
MDV2_RESERVED = set("_*[]()~`>#+-=|{}.!")
_ERROR_NAMES = {
    "bold": "Bold",
    "italic": "Italic",
    "underline": "Underline",
    "strikethrough": "Strikethrough",
    "spoiler": "Spoiler",
    "text_link": "TextUrl",
}


class EntityParseError(ValueError):
    """Raised with Telegram's description text (without the 'Bad Request: ' prefix)."""


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


@dataclass
class _Open:
    kind: str
    start: int  # UTF-16 offset in the rendered text
    byte_offset: int  # UTF-8 offset in the source, for error messages
    extra: dict[str, object] | None = None  # e.g. {"url": ...} for text_link


class _Builder:
    def __init__(self, src: str) -> None:
        self.src = src
        self.parts: list[str] = []
        self.length = 0  # UTF-16 length of rendered text so far
        self.entities: list[dict[str, object]] = []
        self.stack: list[_Open] = []

    def byte_offset(self, index: int) -> int:
        return len(self.src[:index].encode("utf-8"))

    def emit(self, text: str) -> None:
        self.parts.append(text)
        self.length += utf16_len(text)

    def open(self, kind: str, index: int) -> None:
        self.stack.append(_Open(kind, self.length, self.byte_offset(index)))

    def close(self, extra: dict[str, object] | None = None) -> None:
        opened = self.stack.pop()
        self.add(opened.kind, opened.start, self.length - opened.start, extra or opened.extra)

    def add(self, kind: str, offset: int, length: int, extra: dict[str, object] | None = None) -> None:
        if length > 0:  # Telegram drops empty entities
            self.entities.append({"offset": offset, "length": length, "type": kind, **(extra or {})})

    def result(self) -> tuple[str, list[dict[str, object]]]:
        return _trim("".join(self.parts), self.entities)


def _trim(text: str, entities: list[dict[str, object]]) -> tuple[str, list[dict[str, object]]]:
    """Telegram strips leading/trailing whitespace from the message and clips entities."""
    stripped_left = text.lstrip()
    lead = utf16_len(text) - utf16_len(stripped_left)
    out = stripped_left.rstrip()
    total = utf16_len(out)
    clipped: list[dict[str, object]] = []
    for ent in entities:
        start = max(int(ent["offset"]) - lead, 0)  # type: ignore[arg-type]
        end = min(int(ent["offset"]) + int(ent["length"]) - lead, total)  # type: ignore[arg-type]
        if end > start:
            clipped.append({**ent, "offset": start, "length": end - start})
    clipped.sort(key=lambda e: (int(e["offset"]), -int(e["length"])))  # type: ignore[arg-type]
    return out, clipped


# --------------------------------------------------------------------------------------------
# MarkdownV2
# --------------------------------------------------------------------------------------------
def parse_markdown_v2(src: str) -> tuple[str, list[dict[str, object]]]:
    b = _Builder(src)
    i, n = 0, len(src)

    def toggle(kind: str, index: int) -> None:
        if b.stack and b.stack[-1].kind == kind:
            b.close()
        elif any(o.kind == kind for o in b.stack):
            inner = b.stack[-1]
            raise EntityParseError(
                f"Can't find end of {_ERROR_NAMES.get(inner.kind, inner.kind)} entity "
                f"at byte offset {inner.byte_offset}"
            )
        else:
            b.open(kind, index)

    while i < n:
        c = src[i]
        at_line_start = i == 0 or src[i - 1] == "\n"

        if c == "\\":
            if i + 1 < n and 1 <= ord(src[i + 1]) <= 126:
                b.emit(src[i + 1])
                i += 2
            else:
                b.emit(c)
                i += 1
            continue

        if c == ">" and at_line_start:
            if not (b.stack and b.stack[-1].kind == "blockquote"):
                b.open("blockquote", i)
            i += 1
            continue

        if c == "\n":
            b.emit(c)
            i += 1
            if b.stack and b.stack[-1].kind == "blockquote" and (i >= n or src[i] != ">"):
                opened = b.stack.pop()
                b.add("blockquote", opened.start, b.length - 1 - opened.start)
            continue

        if c == "`":
            fence = 3 if src.startswith("```", i) else 1
            kind = "pre" if fence == 3 else "code"
            j = i + fence
            body: list[str] = []
            while j < n and not src.startswith("`" * fence, j):
                if src[j] == "\\" and j + 1 < n:
                    body.append(src[j + 1])
                    j += 2
                else:
                    body.append(src[j])
                    j += 1
            if j >= n:
                raise EntityParseError(
                    f"Can't find end of {kind.capitalize()} entity at byte offset {b.byte_offset(i)}"
                )
            content = "".join(body)
            extra: dict[str, object] | None = None
            if kind == "pre" and "\n" in content:
                first, rest = content.split("\n", 1)
                if first and " " not in first:
                    extra, content = {"language": first}, rest
            start = b.length
            b.emit(content)
            b.add(kind, start, b.length - start, extra)
            i = j + fence
            continue

        if c == "*":
            toggle("bold", i)
            i += 1
            continue
        if c == "_":
            if src.startswith("__", i):
                toggle("underline", i)
                i += 2
            else:
                toggle("italic", i)
                i += 1
            continue
        if c == "~":
            toggle("strikethrough", i)
            i += 1
            continue
        if c == "|" and src.startswith("||", i):
            toggle("spoiler", i)
            i += 2
            continue
        if c == "[":
            b.open("text_link", i)
            i += 1
            continue
        if c == "]" and b.stack and b.stack[-1].kind == "text_link":
            if i + 1 >= n or src[i + 1] != "(":
                raise EntityParseError(f"Can't find end of a URL at byte offset {b.byte_offset(i)}")
            j, url = i + 2, []
            while j < n and src[j] != ")":
                if src[j] == "\\" and j + 1 < n:
                    url.append(src[j + 1])
                    j += 2
                else:
                    url.append(src[j])
                    j += 1
            if j >= n:
                raise EntityParseError(f"Can't find end of a URL at byte offset {b.byte_offset(i)}")
            b.close({"url": "".join(url)})
            i = j + 1
            continue

        if c in MDV2_RESERVED:
            raise EntityParseError(f"Character '{c}' is reserved and must be escaped with the preceding '\\'")

        b.emit(c)
        i += 1

    if b.stack and b.stack[-1].kind == "blockquote":
        opened = b.stack.pop()
        b.add("blockquote", opened.start, b.length - opened.start)
    if b.stack:
        inner = b.stack[-1]
        raise EntityParseError(
            f"Can't find end of {_ERROR_NAMES.get(inner.kind, inner.kind)} entity at byte offset {inner.byte_offset}"
        )
    return b.result()


# --------------------------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------------------------
_HTML_TAGS = {
    "b": "bold",
    "strong": "bold",
    "i": "italic",
    "em": "italic",
    "u": "underline",
    "ins": "underline",
    "s": "strikethrough",
    "strike": "strikethrough",
    "del": "strikethrough",
    "tg-spoiler": "spoiler",
    "span": "spoiler",
    "a": "text_link",
    "code": "code",
    "pre": "pre",
    "blockquote": "blockquote",
}
_TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9-]*)(\s[^<>]*)?>")
_ENTITY_RE = re.compile(r"&(lt|gt|amp|quot|#\d+|#x[0-9a-fA-F]+);")
_HREF_RE = re.compile(r"""href\s*=\s*(?:"([^"]*)"|'([^']*)')""")


def parse_html(src: str) -> tuple[str, list[dict[str, object]]]:
    b = _Builder(src)
    names: list[str] = []  # tag names parallel to b.stack
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == "<":
            m = _TAG_RE.match(src, i)
            if not m:
                bad = re.match(r"</?([^\s>]*)", src[i:])
                raise EntityParseError(
                    f'Unsupported start tag "{bad.group(1) if bad else ""}" at byte offset {b.byte_offset(i)}'
                )
            closing, name, attrs = m.group(1) == "/", m.group(2).lower(), m.group(3) or ""
            if name not in _HTML_TAGS or (name == "span" and "tg-spoiler" not in attrs):
                kind = "end" if closing else "start"
                raise EntityParseError(f'Unsupported {kind} tag "{name}" at byte offset {b.byte_offset(i)}')
            if closing:
                if not names:
                    raise EntityParseError(f"Unexpected end tag at byte offset {b.byte_offset(i)}")
                if names[-1] != name:
                    raise EntityParseError(
                        f"Unmatched end tag at byte offset {b.byte_offset(i)}, "
                        f'expected "</{names[-1]}>", found "</{name}>"'
                    )
                names.pop()
                b.close()
            else:
                b.open(_HTML_TAGS[name], i)
                if name == "a":
                    href = _HREF_RE.search(attrs)
                    url = (href.group(1) if href.group(1) is not None else href.group(2)) if href else ""
                    b.stack[-1].extra = {"url": html.unescape(url)}
                names.append(name)
            i = m.end()
            continue
        if c == "&":
            m = _ENTITY_RE.match(src, i)
            if m:
                b.emit(html.unescape(m.group(0)))
                i = m.end()
                continue
        b.emit(c)
        i += 1
    if names:
        raise EntityParseError(f'Can\'t find end tag corresponding to start tag "{names[-1]}"')
    return b.result()


def render(text: str, parse_mode: str | None) -> tuple[str, list[dict[str, object]]]:
    """Return (plain_text, entities) or raise EntityParseError/ValueError like Telegram would."""
    mode = (parse_mode or "").strip()
    if mode == "MarkdownV2":
        out = parse_markdown_v2(text)
    elif mode.upper() == "HTML":
        out = parse_html(text)
    elif mode in ("", "Markdown"):  # legacy Markdown is not used by the baseline; pass through
        out = _trim(text, [])
    else:
        raise ValueError(f"unsupported parse_mode {mode!r}")
    return out
