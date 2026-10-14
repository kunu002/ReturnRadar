"""Invitation recovery without relaxing email, role or workspace authorization."""
import time

from fastapi.testclient import TestClient

from backend import accounts, main
from backend.store import WorkspaceStore
from test_workflows import workspace


def account(email, invite=""):
    client = TestClient(main.app)
    response = client.post("/api/auth/register", json={
        "name": "Review teammate", "email": email, "password": "Invite123!",
        "workspace_name": "Review store", "invite_token": invite,
    })
    assert response.status_code == 200, response.text
    return client, response.json()


def invitation(client, email, role="analyst"):
    response = client.post("/api/workspace/invites", json={"email": email, "role": role})
    assert response.status_code == 200, response.text
    return response.json()["token"]


def test_owner_opening_teammate_invite_gets_identity_guidance_without_consuming_link(workspace):
    db, _ = workspace
    owner, own_session = account("owner@example.com")
    token = invitation(owner, "new-teammate@example.com", "admin")
    result = owner.post("/api/auth/join", json={"token": token})
    assert result.status_code == 403
    assert "different email address" in result.json()["detail"]
    assert "Sign out" in result.json()["detail"]
    assert not db.get("workspace_invites", accounts.digest(token))["used"]
    assert owner.get("/api/auth/identity").json()["user"]["email"] == "owner@example.com"
    member, joined = account("new-teammate@example.com", token)
    assert joined["workspace"] == own_session["workspace"]
    assert joined["role"] == "admin"
    assert member.get("/api/dashboard").status_code == 200
    assert owner.get("/api/auth/session").json()["role"] == "owner"


def test_existing_account_can_join_after_its_old_workspace_access_was_removed(workspace):
    db, _ = workspace
    first_owner, _ = account("first-owner@example.com")
    teammate, first = account("teammate@example.com", invitation(first_owner, "teammate@example.com"))
    assert first_owner.patch(f"/api/workspace/members/{first['user']['id']}", json={"role": "analyst", "active": False}).status_code == 200
    assert teammate.get("/api/auth/session").status_code == 403
    identity = teammate.get("/api/auth/identity")
    assert identity.status_code == 200
    assert set(identity.json()["user"]) == {"id", "name", "email"}
    second_owner, second = account("second-owner@example.com")
    assert teammate.get("/api/dashboard", headers={"X-Workspace-ID": second["workspace"]["id"]}).status_code == 403
    token = invitation(second_owner, "teammate@example.com", "viewer")
    joined = teammate.post("/api/auth/join", json={"token": token})
    assert joined.status_code == 200
    assert joined.json()["workspace"] == second["workspace"]
    assert joined.json()["role"] == "viewer"
    assert teammate.get("/api/dashboard").status_code == 200
    assert teammate.get("/api/dashboard", headers={"X-Workspace-ID": first["workspace"]["id"]}).status_code == 403


def test_expired_used_and_cancelled_links_have_distinct_safe_guidance(workspace):
    db, _ = workspace
    owner, _ = account("owner@example.com")
    member, _ = account("member@example.com")
    for field, updates, expected in [
        ("expires", {"expires": time.time() - 1}, "has expired"),
        ("used", {"used": True}, "already been accepted"),
        ("revoked", {"revoked": True}, "has been cancelled"),
    ]:
        token = invitation(owner, "member@example.com")
        record = db.get("workspace_invites", accounts.digest(token))
        db.put("workspace_invites", record["id"], {**record, **updates})
        response = member.post("/api/auth/join", json={"token": token})
        assert response.status_code == 403
        assert expected in response.json()["detail"]
        assert token not in response.text
    assert member.post("/api/auth/join", json={"token": "missing-link-value-123456789"}).status_code == 403
    assert TestClient(main.app).get("/api/auth/identity").status_code == 401


def test_pending_admin_invitation_requires_creator_to_still_be_owner(workspace):
    db, _ = workspace
    owner, own_session = account("owner@example.com")
    token = invitation(owner, "admin@example.com", "admin")
    scoped = WorkspaceStore(db, own_session["workspace"]["id"])
    creator = scoped.get("members", own_session["user"]["id"])
    scoped.put("members", creator["id"], {**creator, "role": "admin"})
    client = TestClient(main.app)
    response = client.post("/api/auth/register", json={"name": "Admin", "email": "admin@example.com", "password": "Invite123!", "invite_token": token})
    assert response.status_code == 403
    assert "can no longer add" in response.json()["detail"]
    assert not db.get("workspace_invites", accounts.digest(token))["used"]
    assert db.get("auth_users", "local-" + accounts.digest("admin@example.com")) is None


def test_join_keeps_other_workspace_memberships_and_single_use_role(workspace):
    db, _ = workspace
    invite_owner, invited = account("inviter@example.com")
    existing, original = account("existing@example.com")
    token = invitation(invite_owner, "existing@example.com", "viewer")
    response = existing.post("/api/auth/join", json={"token": token})
    assert response.status_code == 200 and response.json()["role"] == "viewer"
    assert {item["role"] for item in existing.get("/api/auth/workspaces").json()} == {"owner", "viewer"}
    assert existing.get("/api/auth/session", headers={"X-Workspace-ID": original["workspace"]["id"]}).json()["role"] == "owner"
    assert existing.post("/api/workspace/invites", json={"email": "other@example.com", "role": "analyst"}, headers={"X-Workspace-ID": invited["workspace"]["id"]}).status_code == 403
    retry = existing.post("/api/auth/join", json={"token": token})
    assert retry.status_code == 403 and "already been accepted" in retry.json()["detail"]
