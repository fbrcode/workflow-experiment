import re

import pytest

from mocks.telegram_text import EntityParseError, parse_html, parse_markdown_v2, render


def test_markdown_v2_bold_italic_and_escapes() -> None:
    text, entities = parse_markdown_v2(
        r"*Morning Brew*" + "\n" + r"_Summary with models/x\-y_" + "\n" + r"Price: 1\.5\!"
    )
    assert text == "Morning Brew\nSummary with models/x-y\nPrice: 1.5!"
    assert entities == [
        {"offset": 0, "length": 12, "type": "bold"},
        {"offset": 13, "length": 23, "type": "italic"},
    ]


def test_markdown_v2_offsets_are_utf16() -> None:
    text, entities = parse_markdown_v2("🧠 *x*")
    assert text == "🧠 x"
    assert entities == [{"offset": 3, "length": 1, "type": "bold"}]  # emoji = 2 UTF-16 units


@pytest.mark.parametrize("char", list("_*[]()~`>#+-=|{}.!"))
def test_markdown_v2_reserved_chars_must_be_escaped(char: str) -> None:
    escaped_ok, _ = parse_markdown_v2("a \\" + char + " b")
    assert escaped_ok == f"a {char} b"


@pytest.mark.parametrize("src", ["end.", "a-b", "x (y)", "#tag", "a = b", "wow!", "{x}"])
def test_markdown_v2_unescaped_reserved_is_rejected(src: str) -> None:
    with pytest.raises(EntityParseError, match="is reserved and must be escaped"):
        parse_markdown_v2(src)


def test_markdown_v2_unclosed_entity() -> None:
    with pytest.raises(EntityParseError, match="Can't find end of Bold entity at byte offset 2"):
        parse_markdown_v2("a *bold")


def test_markdown_v2_nested_and_code() -> None:
    text, entities = parse_markdown_v2("*bold _ital_* `co\\`de` [link](https://x\\.y/a\\)b)")
    assert text == "bold ital co`de link"
    assert {"offset": 0, "length": 9, "type": "bold"} in entities
    assert {"offset": 5, "length": 4, "type": "italic"} in entities
    assert {"offset": 10, "length": 5, "type": "code"} in entities
    assert {"offset": 16, "length": 4, "type": "text_link", "url": "https://x.y/a)b"} in entities


def test_markdown_v2_trims_whitespace_like_telegram() -> None:
    text, entities = parse_markdown_v2("\n\n  *x*  \n")
    assert text == "x"
    assert entities == [{"offset": 0, "length": 1, "type": "bold"}]


def test_html_alert_template() -> None:
    src = "<b>Unable to summarize message</b>\n<i>Attempted at 2026-11-14T12:00:00.000-03:00</i>\n\nError <b>boom</b>"
    text, entities = parse_html(src)
    assert text.startswith("Unable to summarize message\nAttempted at")
    assert entities[0] == {"offset": 0, "length": 27, "type": "bold"}


def test_html_unescaped_angle_bracket_fails() -> None:
    # Q-15: the baseline alert does not escape values, so "<" in a subject breaks the alert.
    with pytest.raises(EntityParseError, match=re.escape('Unsupported start tag "foo@bar.com"')):
        parse_html("<b>From</b>: Foo <foo@bar.com>")


def test_html_entities_and_links() -> None:
    text, entities = parse_html('a &lt;b&gt; &amp; <a href="https://e.x/?a=1&amp;b=2">here</a>')
    assert text == "a <b> & here"
    assert entities == [{"offset": 8, "length": 4, "type": "text_link", "url": "https://e.x/?a=1&b=2"}]


def test_html_unmatched_end_tag() -> None:
    with pytest.raises(EntityParseError, match="Unmatched end tag"):
        parse_html("<b>x</i>")


def test_render_rejects_unknown_parse_mode() -> None:
    with pytest.raises(ValueError, match="unsupported parse_mode"):
        render("x", "BBCode")
