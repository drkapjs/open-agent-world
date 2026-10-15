from dataclasses import replace
from pathlib import Path

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.config import Settings
from backend.hosting.identity import ProfileAccess, VerifiedIdentity
from backend.main import create_app
from backend.request_context import TenantScope, current_request_context


class TestIdentityProvider:
    __test__ = False

    async def authenticate(self, scope):
        # Test-only credential verifier. Production must validate real tokens.
        token = dict(scope["headers"]).get(b"authorization")
        return {b"Bearer alice": VerifiedIdentity("https://id.example", "alice"),
                b"Bearer bob": VerifiedIdentity("https://id.example", "bob"),
                b"Bearer impostor": VerifiedIdentity("https://evil.example", "alice")}.get(token)


def hosted(root: Path, subject="alice", provider=None):
    scope = TenantScope("test-org", "workspace-" + subject)
    settings = replace(Settings.for_data_root(root), tenant_scope=scope)
    access = ProfileAccess(provider or TestIdentityProvider(),
                           VerifiedIdentity("https://id.example", subject), scope, "https://oaw.example")
    app = create_app(settings, profile_access=access)

    @app.get("/context")
    async def context(request: Request):
        context = current_request_context()
        assert context == request.state.request_context
        return {"actor": context.actor.id, "workspace": context.tenant.workspace_id}

    return app


def headers(user):
    return {"Authorization": "Bearer " + user, "Origin": "https://oaw.example"}


def test_hosted_profile_authenticates_even_loopback_and_denies_other_users(tmp_path):
    with TestClient(hosted(tmp_path)) as client:
        for supplied in ({}, {"X-User-ID": "alice", "X-Workspace-ID": "workspace-alice"},
                         headers("bob"), headers("impostor")):
            assert client.get("/api/world", headers=supplied).status_code in {401, 403}
            with pytest.raises(WebSocketDisconnect) as error:
                with client.websocket_connect("/ws/events", headers=supplied):
                    pytest.fail("Unauthenticated or cross-user WebSocket accepted")
            assert error.value.code == 1008
        assert client.get("/health/ready").status_code == 200
        response = client.get("/context", headers={**headers("alice"), "X-Workspace-ID": "workspace-bob"})
        assert response.json()["workspace"] == "workspace-alice"
        assert response.json()["actor"] == VerifiedIdentity("https://id.example", "alice").actor_id
        with client.websocket_connect("/ws/events", headers=headers("alice")) as socket:
            assert socket.receive_json()["type"] == "connection_ready"


def test_two_profiles_keep_worlds_and_restart_ownership_separate(tmp_path):
    with TestClient(hosted(tmp_path / "a")) as alice, TestClient(hosted(tmp_path / "b", "bob")) as bob:
        created = alice.post("/api/nodes", headers=headers("alice"), json={"type": "text", "name": "alice secret"})
        assert created.status_code == 201, created.text
        node_id = created.json()["id"]
        assert bob.get(f"/api/nodes/{node_id}", headers=headers("bob")).status_code == 404
        assert alice.get(f"/api/nodes/{node_id}", headers=headers("bob")).status_code == 403
    with TestClient(hosted(tmp_path / "a")) as restarted:
        assert restarted.get(f"/api/nodes/{node_id}", headers=headers("alice")).status_code == 200
    with pytest.raises(ValueError, match="stored binding"):
        with TestClient(hosted(tmp_path / "a", "bob")):
            pass


def test_profile_rejects_csrf_and_websocket_origin_spoofing(tmp_path):
    with TestClient(hosted(tmp_path)) as client:
        for origin in (None, "https://evil.example", "null"):
            supplied = {"Authorization": "Bearer alice", "X-Forwarded-Host": "oaw.example"}
            if origin is not None:
                supplied["Origin"] = origin
            assert client.post("/api/nodes", headers=supplied, json={"type": "text"}).status_code == 403
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect("/ws/events", headers=supplied):
                    pytest.fail("Cross-origin WebSocket accepted")


def test_existing_desktop_profile_is_not_implicitly_assigned(tmp_path):
    with TestClient(create_app(Settings.for_data_root(tmp_path))):
        pass
    with pytest.raises(ValueError, match="offline"):
        with TestClient(hosted(tmp_path)):
            pass


def test_identity_names_are_not_keys():
    assert VerifiedIdentity("issuer", "subject", "Old").actor_id == VerifiedIdentity("issuer", "subject", "New").actor_id
    assert VerifiedIdentity("issuer", "subject").actor_id != VerifiedIdentity("other", "subject").actor_id


def test_profile_scope_mismatch_fails_before_starting(tmp_path):
    access = ProfileAccess(TestIdentityProvider(), VerifiedIdentity("issuer", "alice"),
                           TenantScope("org", "workspace"), "https://oaw.example")
    with pytest.raises(ValueError, match="same tenant"):
        create_app(Settings.for_data_root(tmp_path), profile_access=access)
