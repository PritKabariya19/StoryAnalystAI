"""A throwaway local website for browser tests."""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TEST_WEB = (Path(__file__).parents[2] / "test-web" / "index.html").read_text().replace(
    '<script src="https://cdn.tailwindcss.com"></script>', "<style>.hidden{display:none}</style>")


class Site:
    def __init__(self, pages: dict):
        site = self
        self.hits = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                site.hits.append((self.headers["Host"].split(":")[0], self.path))
                page = pages.get(self.path, "<p>not found</p>")
                if page.startswith("redirect:"):
                    self.send_response(302)
                    self.send_header("Location", page[9:].format(port=site.port))
                    self.end_headers()
                    return
                body = page.replace("{port}", str(site.port)).encode()
                self.send_response(200 if self.path in pages else 404)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
