from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse
from urllib.error import URLError

from .service import AnalyzerService


class ApiHandler(BaseHTTPRequestHandler):
    service = AnalyzerService()
    server_version = "StockAnalyzer/0.1"

    def _send(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 5_000_000:
            raise ValueError("Тело запроса больше 5 МБ")
        return self.rfile.read(length)

    def _send_file(self, relative_path: str) -> None:
        web_root = Path(__file__).resolve().parent.parent / "web"
        requested = (web_root / relative_path.lstrip("/")).resolve()
        if web_root not in requested.parents and requested != web_root:
            self._send(403, {"error": "Недопустимый путь"})
            return
        if not requested.is_file():
            self._send(404, {"error": "Файл не найден"})
            return
        body = requested.read_bytes()
        content_type = mimetypes.guess_type(str(requested))[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
            content_type += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/health":
            self._send(200, {"status": "ok", "version": "0.1.0", "trading": False})
        elif path == "/api/companies":
            self._send(200, {"items": self.service.companies()})
        elif path in {"/", "/index.html"}:
            self._send_file("index.html")
        elif path.startswith("/assets/"):
            self._send_file(path)
        else:
            self._send(404, {"error": "Маршрут не найден"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            raw = self._body()
            if path == "/api/import/companies.csv":
                self._send(200, self.service.import_csv(raw.decode("utf-8-sig")))
                return
            if path == "/api/import/moex":
                self._send(200, self.service.import_moex())
                return
            if path == "/api/import/moex-blue-chips":
                self._send(200, self.service.import_blue_chips())
                return
            payload = json.loads(raw or b"{}")
            if path == "/api/analyze":
                self._send(200, self.service.analyze(payload))
            elif path == "/api/official-short-debt":
                ticker = str(payload.get("ticker", ""))
                if not ticker:
                    raise ValueError("Укажите тикер")
                self._send(200, self.service.official_short_debt(ticker, int(payload.get("year", 2025))))
            else:
                self._send(404, {"error": "Маршрут не найден"})
        except KeyError as exc:
            self._send(404, {"error": str(exc)})
        except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self._send(400, {"error": str(exc)})
        except (URLError, TimeoutError, OSError):
            self._send(502, {"error": "MOEX ISS временно недоступен. Повторите загрузку позже."})

    def log_message(self, format: str, *args: object) -> None:
        return


def run_server(host: str = "127.0.0.1", port: int = 8765) -> None:
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("MVP разрешает прослушивание только localhost")
    server = ThreadingHTTPServer((host, port), ApiHandler)
    print(f"Stock Analyzer API: http://{host}:{port}")
    server.serve_forever()
