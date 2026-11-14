"""Mock Telegram Bot API (``POST /bot<token>/<method>``).

Implements ``getMe`` and ``sendMessage``. ``sendMessage`` validates the text the way Telegram
does (MarkdownV2 / HTML entity parsing, 4096-char limit, known chats) and answers with a
Message whose ``text`` is the rendered plain text, like the real API.

Environment:
    MOCK_PORT                   listen port (default 8090)
    MOCK_TELEGRAM_KNOWN_CHATS   comma-separated chat ids; if set, other chats get
                                "Bad Request: chat not found"

Faults (POST /_mock/faults) match on ``method`` and ``chat_id``.
"""

from __future__ import annotations

import os
import re
import time
from typing import Any

from .common import JSON, MockHandler, MockState, Request, serve
from .telegram_text import MAX_MESSAGE_UTF16, EntityParseError, render, utf16_len

_PATH_RE = re.compile(r"^/bot(?P<token>[^/]+)/(?P<method>[A-Za-z]+)$")
_TOKEN_RE = re.compile(r"^(?P<bot_id>\d+):[A-Za-z0-9_-]+$")
_DEFAULT_ERRORS = {
    400: "Bad Request: mock fault",
    403: "Forbidden: bot was blocked by the user",
    429: "Too Many Requests: retry after 5",
    500: "Internal Server Error",
}


def _error(status: int, description: str) -> JSON:
    body: JSON = {"ok": False, "error_code": status, "description": description}
    if status == 429:
        body["parameters"] = {"retry_after": 5}
    return body


def _chat(chat_id: str) -> JSON:
    try:
        numeric: Any = int(chat_id)
    except ValueError:
        return {"id": chat_id, "type": "channel", "title": chat_id}
    if numeric > 0:
        return {"id": numeric, "type": "private", "first_name": "Mock User"}
    return {"id": numeric, "type": "supergroup", "title": f"Mock chat {numeric}"}


class TelegramHandler(MockHandler):
    service = "mock-telegram"
    state = MockState()
    known_chats: frozenset[str] = frozenset()
    message_seq = 0

    def handle_upstream(self, request: Request) -> None:
        match = _PATH_RE.match(request.path)
        if not match:
            self._reply(request, {}, 404, _error(404, "Not Found"))
            return
        token, method = match["token"], match["method"]
        token_match = _TOKEN_RE.match(token)
        params = request.params()
        attrs = {"method": method, "chat_id": str(params.get("chat_id", ""))}

        if not token_match:
            self._reply(request, attrs, 401, _error(401, "Unauthorized"), params)
            return

        fault = self.state.take_fault(attrs)
        if fault:
            if fault.delay_ms:
                time.sleep(fault.delay_ms / 1000)
            description = fault.description or _DEFAULT_ERRORS.get(fault.status, "Mock fault")
            self._reply(request, attrs, fault.status, _error(fault.status, description), params, fault=True)
            return

        bot = {"id": int(token_match["bot_id"]), "is_bot": True, "first_name": "Mock Bot", "username": "mock_bot"}
        if method == "getMe":
            self._reply(request, attrs, 200, {"ok": True, "result": bot}, params)
        elif method == "sendMessage":
            self._send_message(request, attrs, params, bot)
        else:
            self._reply(request, attrs, 404, _error(404, "Not Found: method not found"), params)

    def _send_message(self, request: Request, attrs: dict[str, str], params: dict[str, Any], bot: JSON) -> None:
        chat_id = str(params.get("chat_id") or "")
        text = params.get("text")
        if not chat_id:
            self._reply(request, attrs, 400, _error(400, "Bad Request: chat_id is empty"), params)
            return
        if self.known_chats and chat_id not in self.known_chats:
            self._reply(request, attrs, 400, _error(400, "Bad Request: chat not found"), params)
            return
        if not isinstance(text, str) or not text.strip():
            self._reply(request, attrs, 400, _error(400, "Bad Request: message text is empty"), params)
            return
        try:
            rendered, entities = render(text, params.get("parse_mode"))
        except EntityParseError as exc:
            self._reply(request, attrs, 400, _error(400, f"Bad Request: can't parse entities: {exc}"), params)
            return
        except ValueError:
            self._reply(request, attrs, 400, _error(400, "Bad Request: unsupported parse_mode"), params)
            return
        if not rendered:
            self._reply(request, attrs, 400, _error(400, "Bad Request: message text is empty"), params)
            return
        if utf16_len(rendered) > MAX_MESSAGE_UTF16:
            self._reply(request, attrs, 400, _error(400, "Bad Request: message is too long"), params)
            return

        type(self).message_seq += 1
        message: JSON = {
            "message_id": type(self).message_seq,
            "from": bot,
            "chat": _chat(chat_id),
            "date": int(time.time()),
            "text": rendered,
        }
        if entities:
            message["entities"] = entities
        self._reply(request, attrs, 200, {"ok": True, "result": message}, params)

    def _reply(
        self,
        request: Request,
        attrs: dict[str, str],
        status: int,
        body: JSON,
        params: dict[str, Any] | None = None,
        fault: bool = False,
    ) -> None:
        self.state.record(
            {
                "method": attrs.get("method"),
                "chat_id": attrs.get("chat_id"),
                "params": params or {},
                "status": status,
                "fault": fault,
                "response": body,
            }
        )
        self._log(status, request, {"tg_method": attrs.get("method"), "chat_id": attrs.get("chat_id")})
        self.send_json(status, body)


def main() -> None:
    known = os.environ.get("MOCK_TELEGRAM_KNOWN_CHATS", "")
    TelegramHandler.known_chats = frozenset(c.strip() for c in known.split(",") if c.strip())
    serve(TelegramHandler, int(os.environ.get("MOCK_PORT", "8090")))


if __name__ == "__main__":
    main()
