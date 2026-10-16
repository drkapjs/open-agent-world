"""Verified external identities bound to one independently stored OAW profile.

An identity provider verifies its own credentials. It must never construct an
identity from an unsigned header, query parameter, or email supplied by a client.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from starlette.responses import JSONResponse, RedirectResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from backend.request_context import (
    ActorKind, ActorRef, TenantScope, bind_request_context, establish_request_context,
)


@dataclass(frozen=True)
class VerifiedIdentity:
    issuer: str
    subject: str
    display_name: str | None = None

    def __post_init__(self):
        if not self.issuer or not self.subject:
            raise ValueError("Identity requires both issuer and subject")

    @property
    def actor_id(self) -> str:
        # Emails and names can change; issuer namespaces the provider's subject.
        value = json.dumps([self.issuer, self.subject], separators=(",", ":"))
        return "external-" + hashlib.sha256(value.encode()).hexdigest()


class IdentityProvider(Protocol):
    async def authenticate(self, scope: Scope) -> VerifiedIdentity | None:
        """Return an authenticated identity, or None for absent/invalid credentials."""


@dataclass(frozen=True)
class ProfileAccess:
    provider: IdentityProvider
    owner: VerifiedIdentity
    tenant: TenantScope
    public_origin: str
    login_path: str | None = None

    def __post_init__(self):
        url = urlsplit(self.public_origin)
        if (url.scheme not in {"http", "https"} or not url.netloc or url.username
                or url.password or url.path or url.query or url.fragment):
            raise ValueError("public_origin must be an exact HTTP(S) origin without a path")
        if self.login_path is not None and (
            not self.login_path.startswith("/") or self.login_path.startswith("//")
        ):
            raise ValueError("login_path must be local to this application")

    def bind_storage(self, root: Path) -> None:
        """Pin owner/scope under the profile process lock before opening services.

        Changing an environment variable must never silently hand an existing
        user's data to a different account. Existing desktop profiles require an
        explicit offline migration; there is no implicit import of host secrets.
        """
        path = root / "hosted-profile.json"
        expected = {"version": 1, "issuer": self.owner.issuer, "subject": self.owner.subject,
                    "tenant": asdict(self.tenant)}
        if path.exists():
            if json.loads(path.read_text(encoding="utf-8")) != expected:
                raise ValueError("Hosted profile owner or scope differs from its stored binding")
            return
        if (root / "database" / "world.sqlite3").exists():
            raise ValueError("Use a new profile or explicitly migrate the existing profile offline")
        root.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(expected), encoding="utf-8")
        temporary.replace(path)


class ProfileAccessMiddleware:
    def __init__(self, app: ASGIApp, *, access: ProfileAccess):
        self.app, self.access = app, access

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        path = scope["path"]
        if scope["type"] == "http" and scope["method"] == "GET" and path in {"/health/live", "/health/ready"}:
            return await self.app(scope, receive, send)
        headers = scope.get("headers", [])
        origins = [v.decode("latin-1") for k, v in headers if k.lower() == b"origin"]
        # Browser sessions need CSRF and WebSocket origin protection. The value
        # comes from deployment configuration, never Host/X-Forwarded headers.
        unsafe = scope["type"] == "websocket" or scope.get("method") not in {"GET", "HEAD", "OPTIONS"}
        if (origins and origins != [self.access.public_origin]) or (unsafe and not origins):
            return await self._deny(scope, receive, send, 403)
        identity = await self.access.provider.authenticate(scope)
        if identity is None:
            if (self.access.login_path and scope["type"] == "http" and scope["method"] == "GET"
                    and not path.startswith(("/api/", "/ws/"))):
                return await RedirectResponse(self.access.login_path, status_code=303)(scope, receive, send)
            return await self._deny(scope, receive, send, 401)
        if (identity.issuer, identity.subject) != (self.access.owner.issuer, self.access.owner.subject):
            return await self._deny(scope, receive, send, 403)
        context = establish_request_context(
            scope, ActorRef(ActorKind.USER, identity.actor_id, identity.display_name),
            auth_method="external_identity", tenant=self.access.tenant,
        )
        with bind_request_context(context):
            await self.app(scope, receive, send)

    @staticmethod
    async def _deny(scope, receive, send, status):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008, "reason": "Profile access denied"})
        else:
            await JSONResponse({"error": {"code": "profile_access_denied", "message": "Profile access denied"}},
                               status_code=status, headers={"Cache-Control": "no-store"})(scope, receive, send)
