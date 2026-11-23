"""Local stand-in for the Anthropic Messages API (E2-T2 spike, demo mode, no key).

Serves POST /v1/messages: JSON, stream:true native SSE, a tools array answered with a tool_use
block and a tool_result follow-up answered with text. It requires x-api-key to equal MOCK_EXPECT_KEY
and an anthropic-version header, so a 200 proves the gateway injected the key. GET /_last reports
the header NAMES of the last /v1/messages request and whether the key matched; it never reports or
logs prompt text, nor any header value.

Every result from this server is "MOCK upstream, not api.anthropic.com".
Knobs (environment): MOCK_HOST [127.0.0.1], MOCK_PORT [9100], MOCK_EXPECT_KEY [required],
MOCK_CHUNK_DELAY_S [0.02] pause between SSE events, MOCK_TOKENS_IN [11], MOCK_TOKENS_OUT [7].
"""

from __future__ import annotations

import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = os.environ.get("MOCK_HOST", "127.0.0.1")
PORT = int(os.environ.get("MOCK_PORT", "9100"))
EXPECT_KEY = os.environ.get("MOCK_EXPECT_KEY", "")
CHUNK_DELAY_S = float(os.environ.get("MOCK_CHUNK_DELAY_S", "0.02"))
TOKENS_IN = int(os.environ.get("MOCK_TOKENS_IN", "11"))
TOKENS_OUT = int(os.environ.get("MOCK_TOKENS_OUT", "7"))
MODEL = "mock-model"
WORDS = ["Hello", " from", " the", " mock", " upstream"]

LAST: dict[str, object] = {}


def has_tool_result(body: dict) -> bool:
    for message in body.get("messages", []):
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    return True
    return False


def build_message(body: dict) -> dict:
    """The non-stream response for a request body; shape follows the Messages API."""
    if body.get("tools") and not has_tool_result(body):
        tool = body["tools"][0]["name"]
        content: list[dict] = [
            {"type": "tool_use", "id": "toolu_mock_1", "name": tool, "input": {"query": "demo"}}
        ]
        stop = "tool_use"
    else:
        content = [{"type": "text", "text": "".join(WORDS)}]
        stop = "end_turn"
    return {
        "id": "msg_mock_1",
        "type": "message",
        "role": "assistant",
        "model": body.get("model", MODEL),
        "content": content,
        "stop_reason": stop,
        "stop_sequence": None,
        "usage": {"input_tokens": TOKENS_IN, "output_tokens": TOKENS_OUT},
    }


def sse_events(message: dict) -> list[tuple[str, dict]]:
    """Native event sequence: message_start, content blocks, message_delta, message_stop."""
    start = dict(message, content=[], stop_reason=None)
    start["usage"] = {"input_tokens": TOKENS_IN, "output_tokens": 1}
    events: list[tuple[str, dict]] = [
        ("message_start", {"type": "message_start", "message": start})
    ]
    for index, block in enumerate(message["content"]):
        if block["type"] == "text":
            opener = {"type": "text", "text": ""}
            deltas = [{"type": "text_delta", "text": word} for word in WORDS]
        else:
            opener = {"type": "tool_use", "id": block["id"], "name": block["name"], "input": {}}
            deltas = [{"type": "input_json_delta", "partial_json": json.dumps(block["input"])}]
        events.append(
            (
                "content_block_start",
                {"type": "content_block_start", "index": index, "content_block": opener},
            )
        )
        for delta in deltas:
            events.append(
                (
                    "content_block_delta",
                    {"type": "content_block_delta", "index": index, "delta": delta},
                )
            )
        events.append(("content_block_stop", {"type": "content_block_stop", "index": index}))
    events.append(
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": message["stop_reason"], "stop_sequence": None},
                "usage": {"output_tokens": TOKENS_OUT},
            },
        )
    )
    events.append(("message_stop", {"type": "message_stop"}))
    return events


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Method and path only. Never the request line of a malformed request, never a body."""
        path = self.path.split("?")[0] if self.command else "-"
        sys.stderr.write(f"mock {self.command or '-'} {path}\n")

    def _json(self, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _error(self, status: int, kind: str, message: str) -> None:
        self._json(status, {"type": "error", "error": {"type": kind, "message": message}})

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/_last":
            self._json(200, LAST)
        elif self.path == "/_health":
            self._json(200, {"ok": True})
        else:
            self._error(404, "not_found_error", "unknown path")

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        if self.path.split("?")[0] != "/v1/messages":
            self._error(404, "not_found_error", "unknown path")
            return
        names = sorted({name.lower() for name in self.headers})
        key_ok = bool(EXPECT_KEY) and self.headers.get("x-api-key") == EXPECT_KEY
        LAST.clear()
        LAST.update(
            {
                "header_names": names,
                "x_api_key_matches": key_ok,
                "has_apikey_header": "apikey" in names,
                "has_authorization_header": "authorization" in names,
                "x_api_key_count": len(self.headers.get_all("x-api-key") or []),
            }
        )
        if not key_ok:
            self._error(401, "authentication_error", "invalid x-api-key")
            return
        if "anthropic-version" not in names:
            self._error(400, "invalid_request_error", "anthropic-version header is required")
            return
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            self._error(400, "invalid_request_error", "body is not JSON")
            return
        message = build_message(body)
        if not body.get("stream"):
            self._json(200, message)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for name, payload in sse_events(message):
            frame = f"event: {name}\ndata: {json.dumps(payload)}\n\n".encode()
            self.wfile.write(f"{len(frame):x}\r\n".encode() + frame + b"\r\n")
            self.wfile.flush()
            time.sleep(CHUNK_DELAY_S)
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()


def main() -> None:
    if not EXPECT_KEY:
        sys.exit("MOCK_EXPECT_KEY is required")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
