"""Limits which URLs the service will open.

Only http and https are ever allowed. With BLOCK_PRIVATE_URLS=true (set in the
Dockerfile) private and internal addresses are refused too. Chromium and
requests then connect through a local proxy that checks the address of every
connection, which also covers redirects, page subresources and DNS changes.
"""
import ipaddress
import os
import select
import socket
import socketserver
import threading
from urllib.parse import urlsplit

import requests

_IDLE_TIMEOUT = 60
_lock = threading.Lock()
_server = None


class BlockedURL(requests.RequestException):
    pass


def _block_private() -> bool:
    return os.environ.get("BLOCK_PRIVATE_URLS", "").lower() == "true"


def _public_addresses(host: str, port: int) -> list:
    """Resolve host. Raise unless every address is public."""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global or ip.is_multicast:
            raise BlockedURL(f"Blocked URL: '{host}' is a private or internal address.")
    return infos


def with_scheme(url: str) -> str:
    """Add the missing scheme: http for local addresses, https for the rest."""
    url = url.strip()
    if "://" in url:
        return url
    try:
        host = urlsplit("//" + url).hostname or ""
        local = host == "localhost" or host.endswith(".localhost") or not ipaddress.ip_address(host).is_global
    except ValueError:
        local = False  # an ordinary host name
    return ("http://" if local else "https://") + url


def check_url(url: str) -> None:
    try:
        parts = urlsplit(url)
        host, port = parts.hostname, parts.port
    except ValueError:
        raise BlockedURL(f"Blocked URL: '{url[:100]}' is not a valid URL.")
    if parts.scheme not in ("http", "https") or not host:
        raise BlockedURL(f"Blocked URL: only http and https are allowed, got '{url[:100]}'.")
    if _block_private():
        try:
            _public_addresses(host, port or 80)
        except BlockedURL:
            raise  # a subclass of OSError, so it must come first
        except (OSError, UnicodeError):
            pass  # unresolvable: the request itself reports the network error


def _connect(host: str, port: int) -> socket.socket:
    """Connect to an address that has just been checked."""
    error = OSError("no address")
    for family, kind, proto, _, address in _public_addresses(host, port):
        sock = socket.socket(family, kind, proto)
        sock.settimeout(15)
        try:
            sock.connect(address)
            return sock
        except OSError as exc:
            sock.close()
            error = exc
    raise error


class _Proxy(socketserver.BaseRequestHandler):
    def handle(self):
        client = self.request
        client.settimeout(_IDLE_TIMEOUT)
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = client.recv(65536)
            if not chunk or len(data) > 65536:
                return
            data += chunk
        head, rest = data.split(b"\r\n\r\n", 1)
        lines = head.split(b"\r\n")
        try:
            method, target, version = lines[0].split(b" ", 2)
            tunnel = method == b"CONNECT"
            url = urlsplit(("//" if tunnel else "") + target.decode("latin-1"))
            upstream = _connect(url.hostname, url.port or 80)
        except BlockedURL:
            client.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            return
        except (OSError, ValueError, TypeError):
            client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            return

        with upstream:
            if tunnel:
                client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                upstream.sendall(rest)
            else:
                path = (url.path or "/") + (f"?{url.query}" if url.query else "")
                headers = [line for line in lines[1:]
                           if not line.lower().startswith((b"connection:", b"proxy-connection:"))]
                # One request per connection, so the next one is checked too.
                upstream.sendall(b"\r\n".join(
                    [method + b" " + path.encode("latin-1") + b" " + version,
                     *headers, b"Connection: close", b"", rest]))
            self._pipe(client, upstream)

    @staticmethod
    def _pipe(client, upstream):
        other = {client: upstream, upstream: client}
        try:
            while True:
                ready = select.select(list(other), [], [], _IDLE_TIMEOUT)[0]
                if not ready:
                    return
                for sock in ready:
                    chunk = sock.recv(65536)
                    if not chunk:
                        return
                    other[sock].sendall(chunk)
        except OSError:
            return


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    request_queue_size = 128


def _proxy_url() -> str:
    global _server
    with _lock:
        if _server is None:
            _server = _Server(("127.0.0.1", 0), _Proxy)
            threading.Thread(target=_server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{_server.server_address[1]}"


def browser_options() -> dict:
    """Extra arguments for chromium.launch()."""
    if not _block_private():
        return {}
    # "<-loopback>" stops Chromium skipping the proxy for localhost.
    return {"proxy": {"server": _proxy_url(), "bypass": "<-loopback>"}}


def request_proxies():
    """The `proxies` argument for requests calls."""
    if not _block_private():
        return None
    return {"http": _proxy_url(), "https": _proxy_url()}
