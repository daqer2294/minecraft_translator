"""Поддельная Ollama для тестов: /api/tags, /api/ps, /api/version и OpenAI-эндпоинт /v1/chat/completions."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAIN = "qwen2.5:3b"
STRONG = "llama3.1:8b-instruct-q4_K_M"


class FakeOllama:
    """
    reply(model, text) -> ответ. Записывает, какой модели какой текст отправлен:
    self.sent = [(model, text), ...]. Пакетные запросы (JSON-массив) раскладываются по строкам.
    """

    def __init__(self, models=(MAIN, STRONG), reply=None):
        self.models = list(models)
        self.reply = reply or (lambda model, text: "ПЕР " + text)
        self.sent = []
        self.lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, data):
                body = json.dumps(data, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/api/tags":
                    self._send({"models": [{"name": m, "size": 1} for m in fake.models]})
                elif self.path == "/api/ps":
                    self._send({"models": []})
                elif self.path == "/api/version":
                    self._send({"version": "fake"})
                else:
                    self.send_error(404)

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                model = payload["model"]
                user = payload["messages"][-1]["content"]
                system = payload["messages"][0]["content"]
                if "JSON array" in system:
                    items = json.loads(user)
                    with fake.lock:
                        fake.sent += [(model, x) for x in items]
                    content = json.dumps([fake.reply(model, x) for x in items], ensure_ascii=False)
                else:
                    with fake.lock:
                        fake.sent.append((model, user))
                    content = fake.reply(model, user)
                self._send({"choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}]})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def texts_for(self, model):
        return [t for m, t in self.sent if m == model and t != "Hi"]
