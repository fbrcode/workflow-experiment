"""Workflow 2, branch C logic: SND-2, the `format-as-markdown` Code node.

Ported regex by regex from `baseline/EmailSummarizerFromPgCron.json`; the JavaScript original
is quoted above each step. JavaScript regex semantics come from `js` (`\\s`, `.`, and the `^`/`$`
anchors under the `m` flag). The golden vector `snd2-format-as-markdown.json` is the referee.
"""

from __future__ import annotations

import re
from typing import Any, Final

from email_summarizer import js

# summary.replace(/\\n/g, '\n')
_LITERAL_NEWLINE: Final = re.compile(r"\\n")
# /^####\s*(.+)$/gm, /^###\s*(.+)$/gm, /^##\s*(.+)$/gm  ->  '**$1**'
_HEADINGS: Final = tuple(re.compile(rf"{js.BOL}{hashes}{js.S}*({js.DOT}+){js.EOL}") for hashes in ("####", "###", "##"))
# /^---$/gm  ->  ''
_RULE: Final = re.compile(rf"{js.BOL}---{js.EOL}")
# /^(\s*)[*\-]\s{2,}/gm and /^(\s*)[*\-]\s/gm  ->  '$1• '
_BULLET_WIDE: Final = re.compile(rf"{js.BOL}({js.S}*)[*\-]{js.S}{{2,}}")
_BULLET: Final = re.compile(rf"{js.BOL}({js.S}*)[*\-]{js.S}")
# ALL_SPECIAL = /([_\[\]\(\)~`>#\+\-=|{}\.!\*\\])/g ; SPECIAL_NO_STAR is the same without *
_ALL_SPECIAL: Final = re.compile(r"([_\[\]()~`>#+\-=|{}.!*\\])")
_SPECIAL_NO_STAR: Final = re.compile(r"([_\[\]()~`>#+\-=|{}.!\\])")
# /\*\*([\s\S]+?)\*\*/g
_BOLD: Final = re.compile(r"\*\*([\s\S]+?)\*\*")
# /\*([^\s*][^*\n]*?)\*/g
_ITALIC: Final = re.compile(rf"\*([^{js.S_BODY}*][^*\n]*?)\*")
# /@@(?:BOLD|ITAL)(\d+)@@/g
_PLACEHOLDER: Final = re.compile(r"@@(?:BOLD|ITAL)([0-9]+)@@")


def _escape_all(text: str) -> str:
    return _ALL_SPECIAL.sub(r"\\\1", text)


def escape_keep_bold_italic(text: str) -> str:
    """`escapeKeepBoldItalic`: MarkdownV2-escape everything except **bold** and *italic* markers."""
    placeholders: list[str] = []

    def bold(match: re.Match[str]) -> str:
        key = f"@@BOLD{len(placeholders)}@@"
        placeholders.append(f"*{_escape_all(match.group(1))}*")
        return key

    def italic(match: re.Match[str]) -> str:
        key = f"@@ITAL{len(placeholders)}@@"
        placeholders.append(f"_{_escape_all(match.group(1))}_")
        return key

    def restore(match: re.Match[str]) -> str:
        index = int(match.group(1))
        # placeholders[Number(i)] is undefined for an index that doesn't exist; replace() prints it.
        return placeholders[index] if index < len(placeholders) else "undefined"

    text = _BOLD.sub(bold, text)
    text = _ITALIC.sub(italic, text)
    text = _SPECIAL_NO_STAR.sub(r"\\\1", text)
    text = text.replace("*", "\\*")
    return _PLACEHOLDER.sub(restore, text)


def format_as_markdown(row: dict[str, Any]) -> dict[str, Any]:
    """SND-2: the Telegram MarkdownV2 message for a SND-1 row, with its lengths before and after escaping."""
    summary = (
        f"**{js.string(row.get('email_from'))}**\n"
        f"**{js.string(row.get('email_date'))}**\n"
        f"**{js.string(row.get('email_subject'))}**\n"
        f"*Summary {js.string(row.get('translated'))} with {js.string(row.get('model_used'))}*\n"
        "\n"
        f"{js.string(row.get('summary_content'))}"
    )
    summary = _LITERAL_NEWLINE.sub("\n", summary)
    for heading in _HEADINGS:
        summary = heading.sub(r"**\1**", summary)
    summary = _RULE.sub("", summary)
    summary = _BULLET_WIDE.sub("\\1\u2022 ", summary)
    summary = _BULLET.sub("\\1\u2022 ", summary)
    summary = summary.replace('"', "'")

    message = escape_keep_bold_italic(summary)
    return {
        "news_id": row["id"],
        "initialMessageLength": js.length(summary),
        "finalMessageLength": js.length(message),
        "telegramMessage": message,
    }
