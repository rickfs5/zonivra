"""Local-only HTTP security baseline (MG-SEC-002, SPEC SEC-01). Pure ASGI middleware, no third-party identity or session service.

Threats addressed: other machines (non-loopback peer), DNS rebinding (Host allowlist), cross-site requests from a browser tab
(Origin / Sec-Fetch-Site), CSRF on state-changing calls (header token required), unauthenticated local processes (per-process
session token), WebSocket hijacking (WebSocket upgrades are refused: the application has none).

Not addressed (documented limitation): a process running as the same macOS user can read the token file or the cookie jar and is
trusted; this is an access gate for a local research tool, not user authentication, and gives no authenticity to exported runs.
"""
from __future__ import annotations

import secrets
from urllib.parse import urlsplit

from starlette.responses import JSONResponse

ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost"})
LOOPBACK_PEERS = frozenset({"127.0.0.1", "::1"})
SAFE_METHODS = frozenset({"GET", "HEAD"})
COOKIE_NAME = "mg_session"
HEADER_NAME = "x-mg-token"
OPEN_API_PATHS = frozenset({"/api/health"})  # reveals only status/version; still Host/Origin checked
SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-frame-options", b"DENY"),
    (b"cross-origin-resource-policy", b"same-origin"),
    (b"content-security-policy", b"default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; style-src 'self' 'unsafe-inline'; "
                                 b"script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"),
]


def new_token() -> str:
    return secrets.token_urlsafe(32)


def host_name(host_header: str) -> str | None:
    """Lower-cased hostname of a Host header value (port stripped); None if malformed."""
    h = host_header.strip().lower()
    if not h or any(c in h for c in " ,/\\@?#"):
        return None
    if h.startswith("["):  # bracketed IPv6 literal
        end = h.find("]")
        return h[: end + 1] if end > 0 else None
    return h.split(":", 1)[0]


def _cookie_value(cookie_header: str, name: str) -> str | None:
    for part in cookie_header.split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v
    return None


class LocalSecurity:
    def __init__(self, app, token: str):
        self.app, self.token = app, token.encode()

    def _token_ok(self, supplied: str | None) -> bool:
        return supplied is not None and secrets.compare_digest(supplied.encode(), self.token)

    @staticmethod
    async def _deny(scope, receive, send, status: int, msg_id: str):
        await JSONResponse({"detail": {"msg_id": msg_id}}, status_code=status,
                                  headers={"cache-control": "no-store", **{k.decode(): v.decode() for k, v in SECURITY_HEADERS}})(scope, receive, send)

    async def __call__(self, scope, receive, send):
        kind = scope["type"]
        if kind == "lifespan":
            return await self.app(scope, receive, send)
        if kind == "websocket":  # the application defines no WebSocket endpoint: refuse every upgrade before any handler runs
            await receive()
            return await send({"type": "websocket.close", "code": 1008})
        if kind != "http":
            return
        headers: dict[str, list[str]] = {}
        for k, v in scope.get("headers", []):
            headers.setdefault(k.decode("latin-1").lower(), []).append(v.decode("latin-1"))
        client = (scope.get("client") or (None, None))[0]
        if client not in LOOPBACK_PEERS:
            return await self._deny(scope, receive, send, 403, "mg.refusal.peer_not_allowed")
        hosts = headers.get("host", [])
        name = host_name(hosts[0]) if len(hosts) == 1 else None
        if name not in ALLOWED_HOSTS:  # DNS rebinding: the attacker's name arrives in Host even though the socket is loopback
            return await self._deny(scope, receive, send, 403, "mg.refusal.host_not_allowed")
        if headers.get("sec-fetch-site", ["same-origin"])[0] not in ("same-origin", "none"):
            return await self._deny(scope, receive, send, 403, "mg.refusal.cross_site_request")
        origins = headers.get("origin", [])
        if origins:
            o = urlsplit(origins[0]) if len(origins) == 1 else None
            if o is None or o.scheme != "http" or o.netloc.lower() != hosts[0].strip().lower():  # strict same-origin; "null" fails
                return await self._deny(scope, receive, send, 403, "mg.refusal.origin_not_allowed")
        path, method = scope.get("path", ""), scope.get("method", "GET")
        if path.startswith("/api/") and path not in OPEN_API_PATHS:
            supplied = (headers.get(HEADER_NAME) or [None])[0]
            if supplied is None and method in SAFE_METHODS:  # media/img/download elements cannot set headers: cookie, reads only
                supplied = _cookie_value((headers.get("cookie") or [""])[0], COOKIE_NAME)
            if not self._token_ok(supplied):
                return await self._deny(scope, receive, send, 401, "mg.refusal.unauthorized")

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                have = {k.lower() for k, _ in message.get("headers", [])}
                message["headers"] = list(message.get("headers", [])) + [h for h in SECURITY_HEADERS if h[0] not in have]
            await send(message)

        await self.app(scope, receive, send_with_headers)



class _TooLarge(Exception):
    pass


class BodyLimit:
    """Refuses a JSON body larger than `limit` bytes BEFORE it is parsed, whether or not a Content-Length was declared (chunked bodies are counted as they arrive). Frame uploads have their own
    stricter, larger limit and are exempt (cycle-25 MG-API-025)."""

    def __init__(self, app, limit: int = 65_536, per_path: dict[str, int] | None = None):
        self.app, self.limit, self.per_path = app, limit, per_path or {}   # a few routes legitimately carry a whole observation set: they get their own, still finite, cap

    async def _deny(self, scope, receive, send):
        await JSONResponse({"detail": {"msg_id": "mg.refusal.body_too_large"}}, status_code=413,
                           headers={"cache-control": "no-store", **{k.decode(): v.decode() for k, v in SECURITY_HEADERS}})(scope, receive, send)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS") or scope["path"].endswith("/frames"):
            return await self.app(scope, receive, send)
        limit = self.per_path.get(scope["path"], self.limit)
        cl = dict(scope["headers"]).get(b"content-length")
        if cl is not None and (not cl.isdigit() or int(cl) > limit):
            return await self._deny(scope, receive, send)
        seen, started = 0, False

        async def counted():
            nonlocal seen
            m = await receive()
            if m["type"] == "http.request":
                seen += len(m.get("body", b""))
                if seen > limit:
                    raise _TooLarge()
            return m

        async def watch(m):
            nonlocal started
            if m["type"] == "http.response.start":
                started = True
            await send(m)

        try:
            await self.app(scope, counted, watch)
        except _TooLarge:
            if not started:
                await self._deny(scope, receive, send)
