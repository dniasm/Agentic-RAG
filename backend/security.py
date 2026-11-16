"""Web hardening for every response, so the site can be public over HTTPS (Tailscale Funnel, docs/DOCUMENTATION.md §13).

  * Host. A request must name this site: one of ALLOWED_HOSTS (a "*.ts.net" pattern matches any name under it) or a
    local address (localhost, 127.x, ::1, a private-network IP: development and the Vite proxy). Anything else is 400,
    so a hostile name pointed at this machine gets nothing.
  * Origin (cross-site request forgery). The login is a cookie the browser sends by itself, so a change (POST, PUT,
    PATCH, DELETE) is refused (403) when the browser says it comes from another site: its Origin, else its Referer,
    must be this site, a local address, or one of CORS_ORIGINS. A request with neither (curl, tests) is let through:
    browsers always send Origin on a cross-site change.
  * API docs (/docs, /redoc, /openapi.json) only with API_DOCS=true (development): the public site doesn't describe
    its own API. Not decided by the Host header, which a visitor can set to anything.
  * Headers. nosniff (an uploaded file is never run as something else), Referrer-Policy same-origin, no framing,
    and HSTS on the public name (browsers then refuse plain HTTP for it for a year).

The login cookie's Secure flag comes from secure_cookie(): always on for the public name, off only for local
addresses (plain-HTTP development). The page's Content-Security-Policy is set where the page is served (web.py).
"""
import ipaddress
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.config.settings import settings

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
LOCAL_NAMES = {"localhost"}   # names served as local besides loopback and private IPs (the tests add theirs)
DOC_PATHS = ("/docs", "/redoc", "/openapi.json")
HSTS = "max-age=31536000"
ALWAYS = [(b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"same-origin"),
          (b"x-frame-options", b"DENY")]


def _split(csv: str) -> list[str]:
    return [p.strip().lower() for p in csv.split(",") if p.strip()]


def host_name(host: str) -> str:
    """ "example.ts.net:443" -> "example.ts.net"; "[::1]:8000" -> "::1"."""
    host = (host or "").strip().lower()
    if host.startswith("["):
        return host[1:host.find("]")] if "]" in host else host[1:]
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def is_local(host: str) -> bool:
    """localhost, a loopback or a private-network address: development, never the public site."""
    name = host_name(host)
    if name in LOCAL_NAMES or name.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(name)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local


def allowed_host(host: str) -> bool:
    name = host_name(host)
    if not name:
        return False
    if is_local(name):
        return True
    for pattern in _split(settings.allowed_hosts):
        if pattern.startswith("*.") and (name.endswith(pattern[1:]) and len(name) > len(pattern) - 1):
            return True
        if name == pattern:
            return True
    return False


def same_site(source: str, host: str) -> bool:
    """Is the page that sent a change (an Origin or Referer URL) this site, a local page, or a listed origin?"""
    if not source or source == "null":
        return False
    parts = urlsplit(source)
    origin = f"{parts.scheme}://{parts.netloc}".lower()
    if origin in _split(settings.cors_origins):
        return True
    return parts.netloc.lower() == host.lower() or is_local(parts.netloc)


def secure_cookie(host: str) -> bool:
    """The login cookie's Secure flag: on everywhere but local addresses (plain-HTTP development). A visitor who
    sends a local Host header through the tunnel only changes the flag on their own cookie."""
    return not is_local(host)


class SecurityMiddleware:
    """The host and origin checks and the headers above, for every HTTP request (pure ASGI: streams pass through)."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)
        host = headers.get("host", "")
        if not allowed_host(host):
            return await JSONResponse({"detail": "Unknown host"}, status_code=400)(scope, receive, send)
        if scope["method"] in UNSAFE_METHODS:
            source = headers.get("origin") or headers.get("referer")
            if source is not None and not same_site(source, host):
                return await JSONResponse({"detail": "Cross-site request refused"}, status_code=403)(scope, receive, send)
        if not settings.api_docs and (scope["path"] in DOC_PATHS or scope["path"].startswith("/docs/")):
            return await Response(status_code=404)(scope, receive, send)

        extra = list(ALWAYS)
        if not is_local(host):
            extra.append((b"strict-transport-security", HSTS.encode()))

        async def with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                present = {k.lower() for k, _ in message.get("headers", [])}
                message["headers"] = list(message.get("headers", [])) + [(k, v) for k, v in extra if k not in present]
            await send(message)

        await self.app(scope, receive, with_headers)
