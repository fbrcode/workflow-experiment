"""Mock Gemini API (Generative Language API, ``generativelanguage.googleapis.com``).

Routes (``{v}`` is ``v1`` or ``v1beta``):
    GET  /{v}/models                                   list known models
    GET  /{v}/models/{model}                           model metadata
    POST /{v}/models/{model}:generateContent           JSON response
    POST /{v}/models/{model}:streamGenerateContent     JSON array, or SSE with ``?alt=sse``

The API key is required (``x-goog-api-key`` header or ``?key=``), as with the real API.
The reply text is a deterministic function of (prompt, model), so both orchestrators get the
same output for the same prompt. It deliberately contains Markdown that exercises the
Telegram formatter (spec SND-2): a ``##`` heading, ``*   `` bullets, ``**bold**`` and ``---``.

Environment:
    MOCK_PORT           listen port (default 8091)
    MOCK_GEMINI_MODELS  comma-separated model ids served (default
                        "gemini-flash-lite-latest,gemini-2.5-flash"); others get 404

Faults (POST /_mock/faults) match on ``model`` (id without the ``models/`` prefix),
``action`` (generateContent / streamGenerateContent) and ``api_version``.
"""

from __future__ import annotations

import json
import os
import re
import time

from .common import JSON, MockHandler, MockState, Request, serve

_ROUTE_RE = re.compile(r"^/(?P<version>v1|v1beta)/models(?:/(?P<model>[^/:]+)(?::(?P<action>[A-Za-z]+))?)?$")
_STATUS_NAMES = {
    400: "INVALID_ARGUMENT",
    401: "UNAUTHENTICATED",
    403: "PERMISSION_DENIED",
    404: "NOT_FOUND",
    429: "RESOURCE_EXHAUSTED",
    500: "INTERNAL",
    503: "UNAVAILABLE",
    504: "DEADLINE_EXCEEDED",
}
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_MODE_RE = re.compile(r"(translated to|using) (.+?) language")


def google_error(status: int, message: str) -> JSON:
    return {"error": {"code": status, "message": message, "status": _STATUS_NAMES.get(status, "UNKNOWN")}}


def prompt_text(body: JSON) -> str:
    """Concatenate all user text parts of a generateContent request."""
    texts: list[str] = []
    for content in body.get("contents") or []:
        for part in content.get("parts") or []:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                texts.append(part["text"])
    return "\n".join(texts)


def summarize(prompt: str, model: str) -> str:
    """Deterministic fake summary of the email content embedded in the baseline prompt (SUM-4)."""
    content = prompt.split("Content >> ", 1)[1] if "Content >> " in prompt else prompt
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(content) if s.strip()][:3] or ["(empty content)"]
    mode = _MODE_RE.search(prompt)
    language = f"{mode.group(1)} {mode.group(2)}" if mode else "language not specified"
    lines = [f"## Main topics - {model}", ""]
    for number, sentence in enumerate(sentences, start=1):
        lines.append(f"*   **Topic {number}:** {sentence[:200]}")
    lines += ["", "---", f"Mock summary ({language}); {len(content)} chars of input."]
    return "\n".join(lines)


def _tokens(text: str) -> int:
    return max(1, len(text) // 4)


class GeminiHandler(MockHandler):
    service = "mock-gemini"
    state = MockState()
    models: tuple[str, ...] = ("gemini-flash-lite-latest", "gemini-2.5-flash")

    def handle_upstream(self, request: Request) -> None:
        route = _ROUTE_RE.match(request.path)
        if not route:
            self._fail(request, {}, 404, f"Path {request.path} not found")
            return
        version, model, action = route["version"], route["model"], route["action"]
        attrs = {"model": model or "", "action": action or "", "api_version": version}

        api_key = request.headers.get("x-goog-api-key") or request.query.get("key")
        if not api_key:
            self._fail(request, attrs, 403, "Method doesn't allow unregistered callers. Please use an API key.")
            return

        if model is None:  # list models
            self._ok(request, attrs, {"models": [self._model_info(m) for m in self.models]})
            return
        if model not in self.models:
            self._fail(request, attrs, 404, f"models/{model} is not found for API version {version}")
            return
        if action is None and request.method == "GET":
            self._ok(request, attrs, self._model_info(model))
            return
        if request.method != "POST" or action not in ("generateContent", "streamGenerateContent"):
            self._fail(request, attrs, 404, f"Method {action} not found for models/{model}")
            return

        try:
            body = request.json() or {}
        except json.JSONDecodeError:
            self._fail(request, attrs, 400, "Invalid JSON payload received.")
            return
        prompt = prompt_text(body)
        if not prompt:
            self._fail(request, attrs, 400, "* GenerateContentRequest.contents: contents is not specified", body)
            return

        fault = self.state.take_fault(attrs)
        if fault:
            if fault.delay_ms:
                time.sleep(fault.delay_ms / 1000)
            message = fault.description or f"Mock fault {fault.status} for models/{model}"
            self._fail(request, attrs, fault.status, message, body, fault=True)
            return

        text = summarize(prompt, model)
        response: JSON = {
            "candidates": [
                {"content": {"parts": [{"text": text}], "role": "model"}, "finishReason": "STOP", "index": 0}
            ],
            "usageMetadata": {
                "promptTokenCount": _tokens(prompt),
                "candidatesTokenCount": _tokens(text),
                "totalTokenCount": _tokens(prompt) + _tokens(text),
            },
            "modelVersion": model,
            "responseId": f"mock-{len(self.state.calls()) + 1}",
        }
        self.state.record({**attrs, "request": body, "status": 200, "fault": False, "response_text": text})
        self._log(200, request, {"model": model, "action": action})
        if action == "streamGenerateContent":
            if request.query.get("alt") == "sse":
                payload = f"data: {json.dumps(response, ensure_ascii=False)}\r\n\r\n".encode()
                self.send_raw(200, "text/event-stream", payload)
            else:
                self.send_json(200, [response])
        else:
            self.send_json(200, response)

    @staticmethod
    def _model_info(model: str) -> JSON:
        return {
            "name": f"models/{model}",
            "displayName": f"Mock {model}",
            "inputTokenLimit": 1048576,
            "outputTokenLimit": 65536,
            "supportedGenerationMethods": ["generateContent", "streamGenerateContent", "countTokens"],
        }

    def _ok(self, request: Request, attrs: dict[str, str], body: JSON) -> None:
        self._log(200, request, attrs)
        self.send_json(200, body)

    def _fail(
        self,
        request: Request,
        attrs: dict[str, str],
        status: int,
        message: str,
        body: JSON | None = None,
        fault: bool = False,
    ) -> None:
        if attrs.get("action"):
            self.state.record({**attrs, "request": body, "status": status, "fault": fault, "error": message})
        self._log(status, request, {**attrs, "error": message})
        self.send_json(status, google_error(status, message))


def main() -> None:
    configured = os.environ.get("MOCK_GEMINI_MODELS", "")
    if configured.strip():
        GeminiHandler.models = tuple(m.strip().removeprefix("models/") for m in configured.split(",") if m.strip())
    serve(GeminiHandler, int(os.environ.get("MOCK_PORT", "8091")))


if __name__ == "__main__":
    main()
