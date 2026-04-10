#!/usr/bin/env python3
"""
Local dev server for the dashboard.

- Serves index.html + static files
- Exposes POST /api/translate to proxy Anthropic (avoids CORS + keeps API key off the browser)

Usage (PowerShell):
  $env:ANTHROPIC_API_KEY="..."
  python server.py
  # open http://localhost:8000
"""

from __future__ import annotations

import json
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError


ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
ANTHROPIC_VERSION = os.environ.get("ANTHROPIC_VERSION", "2023-06-01")


def _json_response(handler: SimpleHTTPRequestHandler, status: int, payload: object) -> None:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


def _text_response(handler: SimpleHTTPRequestHandler, status: int, text: str) -> None:
    raw = text.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/plain; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


class Handler(SimpleHTTPRequestHandler):
    def do_POST(self) -> None:
        if self.path != "/api/translate":
            _text_response(self, 404, "Not found")
            return

        key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not key:
            _text_response(self, 500, "Missing ANTHROPIC_API_KEY environment variable")
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            _text_response(self, 400, "Invalid Content-Length")
            return

        try:
            body = self.rfile.read(length).decode("utf-8")
            req_json = json.loads(body)
        except Exception:
            _text_response(self, 400, "Invalid JSON body")
            return

        items = req_json.get("items", [])
        if not isinstance(items, list):
            _text_response(self, 400, "items must be an array")
            return

        prompt = (
            "You are a professional legal translator. Translate these Portuguese legal gazette "
            "(Diário da República) entries to English.\n"
            "Return ONLY a JSON array (no markdown, no backticks) with objects: "
            "[{idx, title_en, summary_en}].\n"
            "Be precise with legal terminology.\n\n"
            "Entries:\n"
            + json.dumps(items, ensure_ascii=False, indent=2)
        )

        anthropic_payload = {
            "model": ANTHROPIC_MODEL,
            "max_tokens": 4000,
            "messages": [{"role": "user", "content": prompt}],
        }

        headers = {
            "Content-Type": "application/json",
            "x-api-key": key,
            "anthropic-version": ANTHROPIC_VERSION,
        }

        try:
            req = Request(
                ANTHROPIC_URL,
                data=json.dumps(anthropic_payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urlopen(req, timeout=60) as resp:
                raw = resp.read().decode("utf-8")
        except HTTPError as e:
            msg = e.read().decode("utf-8", errors="replace")
            _text_response(self, 502, f"Anthropic error ({e.code}): {msg}")
            return
        except Exception as e:
            _text_response(self, 502, f"Anthropic request failed: {e}")
            return

        try:
            data = json.loads(raw)
            text = "".join(block.get("text", "") for block in (data.get("content") or []) if isinstance(block, dict))
            cleaned = text.replace("```json", "").replace("```", "").strip()
            translations = json.loads(cleaned)
            if not isinstance(translations, list):
                raise ValueError("Model did not return a JSON array")
        except Exception as e:
            _text_response(self, 502, f"Could not parse model output as JSON: {e}")
            return

        _json_response(self, 200, translations)


def main() -> None:
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"Serving on http://{host}:{port}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()

