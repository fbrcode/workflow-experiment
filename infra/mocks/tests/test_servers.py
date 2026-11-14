"""End-to-end tests of both mock servers over real HTTP on ephemeral ports."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Any

import pytest

from mocks.common import MockServer, MockState
from mocks.gemini import GeminiHandler
from mocks.telegram import TelegramHandler

TOKEN = "123456:mock-token"


def _start(handler: type) -> tuple[MockServer, str]:
    server = MockServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _call(
    url: str, body: Any = None, method: str | None = None, headers: dict[str, str] | None = None
) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"))
    req.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"null")


@pytest.fixture
def telegram() -> Iterator[str]:
    TelegramHandler.state = MockState()
    TelegramHandler.known_chats = frozenset({"-1001", "-1002"})
    server, base = _start(TelegramHandler)
    yield base
    server.shutdown()


@pytest.fixture
def gemini() -> Iterator[str]:
    GeminiHandler.state = MockState()
    server, base = _start(GeminiHandler)
    yield base
    server.shutdown()


# --- Telegram -------------------------------------------------------------------------------
def test_send_markdown_v2_returns_rendered_text(telegram: str) -> None:
    status, body = _call(
        f"{telegram}/bot{TOKEN}/sendMessage",
        {"chat_id": "-1001", "text": "*Hi* there\\.", "parse_mode": "MarkdownV2"},
    )
    assert status == 200
    assert body["ok"] is True
    assert body["result"]["text"] == "Hi there."
    assert body["result"]["entities"] == [{"offset": 0, "length": 2, "type": "bold"}]
    _, calls = _call(f"{telegram}/_mock/calls")
    assert [c["params"]["chat_id"] for c in calls] == ["-1001"]


def test_send_form_encoded(telegram: str) -> None:
    req = urllib.request.Request(
        f"{telegram}/bot{TOKEN}/sendMessage",
        data=b"chat_id=-1002&text=plain+text",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert json.loads(resp.read())["result"]["text"] == "plain text"


def test_bad_markdown_is_rejected_like_telegram(telegram: str) -> None:
    status, body = _call(
        f"{telegram}/bot{TOKEN}/sendMessage", {"chat_id": "-1001", "text": "end.", "parse_mode": "MarkdownV2"}
    )
    assert status == 400
    assert body["description"].startswith("Bad Request: can't parse entities: Character '.' is reserved")


def test_unknown_chat_and_bad_token(telegram: str) -> None:
    status, body = _call(f"{telegram}/bot{TOKEN}/sendMessage", {"chat_id": "-9", "text": "x"})
    assert (status, body["description"]) == (400, "Bad Request: chat not found")
    status, body = _call(f"{telegram}/botnot-a-token/sendMessage", {"chat_id": "-1001", "text": "x"})
    assert (status, body["description"]) == (401, "Unauthorized")


def test_fault_injection_is_consumed(telegram: str) -> None:
    status, _ = _call(f"{telegram}/_mock/faults", {"when": {"method": "sendMessage"}, "status": 500, "count": 1})
    assert status == 201
    first, _ = _call(f"{telegram}/bot{TOKEN}/sendMessage", {"chat_id": "-1001", "text": "x"})
    second, _ = _call(f"{telegram}/bot{TOKEN}/sendMessage", {"chat_id": "-1001", "text": "x"})
    assert (first, second) == (500, 200)


def test_message_too_long(telegram: str) -> None:
    status, body = _call(f"{telegram}/bot{TOKEN}/sendMessage", {"chat_id": "-1001", "text": "a" * 4097})
    assert (status, body["description"]) == (400, "Bad Request: message is too long")


# --- Gemini ---------------------------------------------------------------------------------
PROMPT = "Summarize this email cntent ... translated to pt-BR language ... Content >> One. Two! Three? Four."
BODY = {
    "contents": [{"role": "user", "parts": [{"text": PROMPT}]}],
    "systemInstruction": {"parts": [{"text": "You are a helpful assistant"}]},
}
KEY = {"x-goog-api-key": "k"}


def test_generate_content_is_deterministic(gemini: str) -> None:
    url = f"{gemini}/v1beta/models/gemini-flash-lite-latest:generateContent"
    status, first = _call(url, BODY, headers=KEY)
    _, second = _call(url, BODY, headers=KEY)
    assert status == 200
    text = first["candidates"][0]["content"]["parts"][0]["text"]
    assert text == second["candidates"][0]["content"]["parts"][0]["text"]
    assert text.splitlines()[0] == "## Main topics - gemini-flash-lite-latest"
    assert "*   **Topic 3:** Three?" in text
    assert "translated to pt-BR" in text


def test_api_key_required_and_unknown_model(gemini: str) -> None:
    status, body = _call(f"{gemini}/v1beta/models/gemini-2.5-flash:generateContent", BODY)
    assert (status, body["error"]["status"]) == (403, "PERMISSION_DENIED")
    status, body = _call(f"{gemini}/v1beta/models/nope:generateContent", BODY, headers=KEY)
    assert (status, body["error"]["status"]) == (404, "NOT_FOUND")


def test_primary_fault_then_fallback_ok(gemini: str) -> None:
    _call(f"{gemini}/_mock/faults", {"when": {"model": "gemini-flash-lite-latest"}, "status": 503, "count": -1})
    primary, err = _call(f"{gemini}/v1beta/models/gemini-flash-lite-latest:generateContent", BODY, headers=KEY)
    fallback, ok = _call(
        f"{gemini}/v1beta/models/gemini-2.5-flash:generateContent", BODY, headers={"x-goog-api-key": "k"}
    )
    assert (primary, err["error"]["status"]) == (503, "UNAVAILABLE")
    assert fallback == 200
    assert ok["candidates"][0]["content"]["parts"][0]["text"].startswith("## Main topics - gemini-2.5-flash")
    _, calls = _call(f"{gemini}/_mock/calls")
    assert [(c["model"], c["status"], c["fault"]) for c in calls] == [
        ("gemini-flash-lite-latest", 503, True),
        ("gemini-2.5-flash", 200, False),
    ]


def test_stream_sse(gemini: str) -> None:
    req = urllib.request.Request(
        f"{gemini}/v1beta/models/gemini-2.5-flash:streamGenerateContent?alt=sse&key=k",
        data=json.dumps(BODY).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        raw = resp.read().decode()
    assert raw.startswith("data: ")
    assert json.loads(raw[6:])["candidates"][0]["finishReason"] == "STOP"
