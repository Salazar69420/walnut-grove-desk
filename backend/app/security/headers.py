"""Pure ASGI middleware: security headers on every response, client address context."""
from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.services.events import current_ip

CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; connect-src 'self'; font-src 'self'; frame-ancestors 'none'; "
    "base-uri 'none'; form-action 'self'"
)

SECURITY_HEADERS = [
    (b"content-security-policy", CSP.encode()),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
]


def client_ip_from_scope(scope: Scope) -> str:
    """Peer address; behind the local Caddy proxy, the address Caddy saw."""
    client = scope.get("client")
    peer = client[0] if client else ""
    if peer in ("127.0.0.1", "::1"):
        for k, v in scope.get("headers", []):
            if k == b"x-forwarded-for":
                parts = [p.strip() for p in v.decode("latin-1").split(",") if p.strip()]
                if parts:
                    return parts[-1][:64]
    return peer[:64]


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        token = current_ip.set(client_ip_from_scope(scope))
        path = scope.get("path", "")

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {k.lower() for k, _ in headers}
                for k, v in SECURITY_HEADERS:
                    if k not in present:
                        headers.append((k, v))
                if (path.startswith("/api") or path == "/healthz") and b"cache-control" not in present:
                    headers.append((b"cache-control", b"no-store"))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            current_ip.reset(token)
