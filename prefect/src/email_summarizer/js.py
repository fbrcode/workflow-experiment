"""The bits of JavaScript semantics that the n8n code relies on, for the Python port.

The golden vectors were produced by V8, so wherever Python and JavaScript disagree in a way the
vectors can see, the port follows JavaScript:

- `\\s`, `trim()`: JavaScript's whitespace set (ECMA-262 WhiteSpace + LineTerminator), which is not
  Python's (`str.isspace()` also accepts U+001C..U+001F and U+0085, and JS accepts U+FEFF).
- Regex anchors with the `m` flag see four line terminators, `.` refuses the same four, and `$`
  without `m` means end of input (Python's `$` also matches before a final newline).
- `${value}` prints `null`/`undefined`; `.length` counts UTF-16 code units.
- `new Date(string)` for mail dates.
"""

from __future__ import annotations

import email.utils
from datetime import UTC, datetime, timedelta
from typing import Any, Final

# ECMA-262 WhiteSpace (TAB, VT, FF, SP, NBSP, Zs, ZWNBSP) + LineTerminator (LF, CR, LS, PS), as code
# points: the characters themselves are invisible in source.
_WHITESPACE_CODE_POINTS: Final = (
    *(0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x20, 0xA0, 0x1680),
    *range(0x2000, 0x200B),
    *(0x2028, 0x2029, 0x202F, 0x205F, 0x3000, 0xFEFF),
)
WHITESPACE: Final = "".join(map(chr, _WHITESPACE_CODE_POINTS))
_LINE_TERMINATORS: Final = "".join(map(chr, (0x0A, 0x0D, 0x2028, 0x2029)))


def _class_body(chars: str) -> str:
    return "".join(f"\\u{ord(c):04x}" for c in chars)


S: Final = f"[{_class_body(WHITESPACE)}]"  # JS \s
NOT_S: Final = f"[^{_class_body(WHITESPACE)}]"  # JS \S
S_BODY: Final = _class_body(WHITESPACE)  # for use inside a larger character class
DOT: Final = f"[^{_class_body(_LINE_TERMINATORS)}]"  # JS . (no s flag)
BOL: Final = rf"(?:\A|(?<=[{_class_body(_LINE_TERMINATORS)}]))"  # JS ^ with the m flag
EOL: Final = rf"(?=[{_class_body(_LINE_TERMINATORS)}]|\Z)"  # JS $ with the m flag
END: Final = r"\Z"  # JS $ without the m flag


def trim(value: str) -> str:
    """String.prototype.trim()."""
    return value.strip(WHITESPACE)


def string(value: Any) -> str:
    """String(value), which is what a template literal does to `${value}`."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def truthy(value: Any) -> bool:
    """JavaScript truthiness for the JSON values a row can hold."""
    return value not in (None, False, 0, "")


def length(value: str) -> int:
    """`.length`: UTF-16 code units, so an astral character such as an emoji counts twice."""
    return len(value.encode("utf-16-le", "surrogatepass")) // 2


def parse_date(value: str | None) -> datetime | None:
    """`new Date(value)` for an email Date header, as a UTC datetime; None means Invalid Date.

    RFC 2822 dates (with or without a weekday, numeric offsets or zone names such as GMT and EST,
    trailing comments, single-digit hours) parse the same way in V8 and in `email.utils`, which
    golden finding G-4 checked for every fixture and edge case. A date without a zone is taken
    as UTC, the time zone of the n8n container.
    """
    if value is None:
        return None
    parsed = email.utils.parsedate_tz(value)
    if parsed is None:
        return None
    try:
        naive = datetime(*parsed[:6], tzinfo=UTC)
    except ValueError:
        return None
    return naive - timedelta(seconds=parsed[9] or 0)
