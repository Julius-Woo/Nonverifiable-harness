"""Fail-closed, fixed-upstream HTTP gateway; no forward-proxy behavior."""

import http.client
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


def route_pattern(template):
    parts = re.split(r"(\{[^}]+\}|<[^>]+>)", template)
    return "".join(
        "[A-Za-z0-9_.~-]+" if p.startswith(("{", "<")) else re.escape(p)
        for p in parts
    )


def allowed(method, target, routes):
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or parsed.fragment:
        return False
    path = parsed.path
    if "%" in path or "\\" in path or "//" in path:
        return False
    if any(part in (".", "..") for part in path.split("/")):
        return False
    return any(
        method == route["method"]
        and re.fullmatch(route_pattern(route["path"]), path)
        for route in routes
    )


class Gateway(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    routes = []
    upstream = ""

    def reject(self, code=403):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"error":"route refused by gateway"}')

    def proxy(self):
        if not allowed(self.command, self.path, self.routes):
            return self.reject()
        if (
            self.headers.get("Transfer-Encoding")
            or len(self.headers.get_all("Content-Length", [])) > 1
        ):
            return self.reject(400)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self.reject(400)
        if not 0 <= length <= 1_000_000:
            return self.reject(413)
        if self.command == "GET" and length:
            return self.reject(400)
        body = self.rfile.read(length)
        headers = {
            k: v
            for k, v in self.headers.items()
            if k.lower()
            in (
                "content-type",
                "x-api-key",
                "x-task-token",
                "x-clinic-token",
                "authorization",
            )
        }
        conn = http.client.HTTPConnection(self.upstream, 8080, timeout=15)
        try:
            conn.request(self.command, self.path, body=body, headers=headers)
            response = conn.getresponse()
            payload = response.read(4_000_001)
            if len(payload) > 4_000_000:
                return self.reject(502)
            self.send_response(response.status)
            self.send_header(
                "Content-Type",
                response.getheader("Content-Type", "application/json"),
            )
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except (OSError, http.client.HTTPException):
            self.reject(502)
        finally:
            conn.close()

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = proxy
    do_HEAD = do_OPTIONS = do_CONNECT = do_TRACE = proxy


if __name__ == "__main__":
    with open("/config/routes.json") as handle:
        Gateway.routes = json.load(handle)
    Gateway.upstream = os.environ["UPSTREAM_IP"]
    ThreadingHTTPServer(("0.0.0.0", 8080), Gateway).serve_forever()
