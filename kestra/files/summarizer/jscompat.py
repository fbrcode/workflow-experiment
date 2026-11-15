"""JavaScript semantics the n8n Code nodes rely on, reproduced in Python.

The golden vectors (tests/golden/vectors) were produced by V8. These helpers cover the places where
Python and JavaScript disagree and the vectors notice: what counts as whitespace (`\\s`, `trim()`),
which characters end a line for `^`/`$`/`.` under the `m` flag, how values print inside template
literals (`null`, `undefined`), how `.length` counts (UTF-16 code units), and how `new Date(str)`
parses mail dates.
"""

from __future__ import annotations

import email.utils
import math
from datetime import UTC, datetime
from typing import Any, Final


class _Undefined:
    """JavaScript `undefined`: a key that doesn't exist on the object."""

    _instance: _Undefined | None = None

    def __new__(cls) -> _Undefined:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "undefined"

    def __bool__(self) -> bool:
        return False


UNDEFINED: Final = _Undefined()

# JS `\s` / String.prototype.trim(): WhiteSpace + LineTerminator (ECMA-262 §12.2, §12.3).
JS_WHITESPACE: Final = (
    "\t\n\x0b\x0c\r \xa0\u1680"
    + "".join(chr(c) for c in range(0x2000, 0x200B))
    + "\u2028\u2029\u202f\u205f\u3000\ufeff"
)
# The same set as the body of a regex character class.
WS: Final = r"\t\n\x0b\x0c\r \xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
# Line terminators: what `.` refuses and what `^`/`$` see under the `m` flag.
LT: Final = r"\n\r\u2028\u2029"
NOT_LT: Final = rf"[^{LT}]"
LINE_START: Final = rf"(?:(?<=[{LT}])|(?<![\s\S]))"  # JS `^` with the m flag
LINE_END: Final = rf"(?=[{LT}]|(?![\s\S]))"  # JS `$` with the m flag
INPUT_END: Final = r"(?![\s\S])"  # JS `$` without the m flag (Python's `$` also matches before a final \n)


def js_trim(value: str) -> str:
    return value.strip(JS_WHITESPACE)


def js_truthy(value: Any) -> bool:
    if value is None or value is UNDEFINED or value is False:
        return False
    if isinstance(value, int | float) and not isinstance(value, bool):
        return not (value == 0 or (isinstance(value, float) and math.isnan(value)))
    if isinstance(value, str):
        return value != ""
    return True


def js_str(value: Any) -> str:
    """`String(value)`, which is what `${value}` does inside a template literal."""
    if value is UNDEFINED:
        return "undefined"
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "Infinity" if value > 0 else "-Infinity"
        if value.is_integer() and abs(value) < 1e21:
            return str(int(value))
        return repr(value)
    if isinstance(value, list):
        return ",".join("" if v is None or v is UNDEFINED else js_str(v) for v in value)
    return "[object Object]"


def utf16_len(value: str) -> int:
    """`.length` of a JS string: UTF-16 code units, so an emoji counts as 2."""
    return len(value.encode("utf-16-le", "surrogatepass")) // 2


def get(obj: dict[str, Any], key: str) -> Any:
    """`obj.key`: the value, or UNDEFINED when the key is missing."""
    return obj.get(key, UNDEFINED)


def drop_undefined(obj: dict[str, Any]) -> dict[str, Any]:
    """JSON.stringify leaves out keys whose value is undefined."""
    return {k: v for k, v in obj.items() if v is not UNDEFINED}


def js_date_utc(value: Any) -> datetime | None:
    """`new Date(value)` for the values ING-3 sees: the instant in UTC, or None for Invalid Date.

    Mail dates are RFC 2822. V8 accepts the same forms that `email.utils` does for every fixture
    and edge case in the vectors (golden finding G-4), including `(UTC)` comments, `GMT`/`EST`
    zone names, a missing weekday and single-digit hours. A date without a zone is read as UTC,
    the n8n container's default time zone.
    """
    if value is None:
        return datetime(1970, 1, 1, tzinfo=UTC)  # new Date(null) is the epoch
    if not isinstance(value, str) or not value.strip():
        return None
    parsed = email.utils.parsedate_tz(value)
    if parsed is not None:
        offset = parsed[9] or 0
        try:
            naive = datetime(*parsed[:6])  # the zone is applied just below
        except ValueError:
            return None
        return datetime.fromtimestamp(naive.replace(tzinfo=UTC).timestamp() - offset, tz=UTC)
    try:
        iso = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return iso if iso.tzinfo else iso.replace(tzinfo=UTC)
