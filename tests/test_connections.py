"""Data-source readiness and imports remain honest and merchant-scoped."""
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.store import WorkspaceStore
from test_workflows import workspace


PASSWORD = "Radar123!"


def register(email, workspace_name="Connection merchant", invite_token=""):
    client = TestClient(main.app)
    response = client.post("/api/auth/register", json={
        "name": "Connection owner", "email": email, "password": PASSWORD,
        "workspace_name": workspace_name, "invite_token": invite_token,
    })
    assert response.status_code == 200, response.text
    return client, response.json()


def return_csv(reason="Laptop is too wide"):
    timestamp = datetime.now(timezone.utc).isoformat()
    return (
        "id,product_id,product_name,variant_id,order_id,order_item_id,return_id,reason,returned_at\n"
        f"connection-item,connection-bag,Connection Bag,connection-v,connection-order,connection-line,connection-return,{reason},{timestamp}\n"
    )


def upload(client, platform="generic", reason="Laptop is too wide"):
    return client.post(f"/api/import?platform={platform}", files={
        "file": ("merchant-returns.csv", return_csv(reason), "text/csv"),
    })


def test_connections_show_readiness_without_global_credentials(workspace, monkeypatch):
    _, demo = workspace
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "private-store.myshopify.com")
    monkeypatch.setenv("SHOPIFY_ACCESS_TOKEN", "private-shopify-token")
    monkeypatch.setenv("WOOCOMMERCE_CONSUMER_KEY", "private-woocommerce-key")
    anonymous = TestClient(main.app)
    assert anonymous.get("/api/connections").status_code == 401

    owner, _ = register("connections@example.com")
    response = owner.get("/api/connections")
    assert response.status_code == 200, response.text
    data = response.json()
    statuses = {connector["id"]: connector["status"] for connector in data["connectors"]}
    assert statuses == {"csv": "available", "shopify": "available", "woocommerce": "available"}
    assert data["coverage"]["returns"]["records"] == 0
    assert data["coverage"]["sales"]["status"] == "not_imported"
    assert data["imports"] == []
    assert not any(secret in json.dumps(data) for secret in ["private-store", "private-shopify-token", "private-woocommerce-key"])

    sample = demo.get("/api/connections").json()
    assert sample["coverage"]["returns"]["records"] == 160
    assert sample["source_counts"].get("synthetic") == 160
    assert sample["imports"] == []  # Seeded examples are not a successful store import.
    assert next(item for item in sample["connectors"] if item["id"] == "shopify")["status"] == "available"


def test_csv_platform_provenance_history_and_reimports_are_scoped(workspace):
    root, _ = workspace
    owner, session = register("import-owner@example.com")
    other, _ = register("other-merchant@example.com")

    response = upload(owner, "shopify")
    assert response.status_code == 200, response.text
    db = WorkspaceStore(root, session["workspace"]["id"])
    record = db.get("returns", "connection-item")
    assert record["source"] == "csv" and record["source_platform"] == "shopify"
    connections = owner.get("/api/connections").json()
    assert connections["coverage"]["returns"]["records"] == 1
    assert connections["source_counts"].get("csv") == 1
    assert connections["source_counts"].get("shopify", 0) == 0
    assert len(connections["imports"]) == 1
    run = connections["imports"][0]
    assert run["platform"] == "shopify" and run["records"] == 1 and run["at"]
    assert db.get("import_runs", run["id"]) is not None

    response = upload(owner, "woocommerce", "Zipper gets stuck")
    assert response.status_code == 200, response.text
    assert len(db.all("returns")) == 1
    assert db.get("returns", "connection-item")["reason"] == "Zipper gets stuck"
    assert db.get("returns", "connection-item")["source_platform"] == "woocommerce"
    assert len(owner.get("/api/connections").json()["imports"]) == 2
    empty = other.get("/api/connections").json()
    assert empty["imports"] == [] and empty["coverage"]["returns"]["records"] == 0
    assert other.get("/api/connections", headers={"X-Workspace-ID": session["workspace"]["id"]}).status_code == 403


def test_invalid_import_does_not_record_a_successful_connection(workspace):
    root, _ = workspace
    owner, session = register("invalid-import@example.com")
    assert upload(owner, "unsupported-platform").status_code == 422
    bad = owner.post("/api/import?platform=shopify", files={"file": ("bad.csv", "unsupported\nvalue\n", "text/csv")})
    assert bad.status_code == 422
    db = WorkspaceStore(root, session["workspace"]["id"])
    assert not db.all("import_runs") and not db.all("returns")
    assert owner.get("/api/connections").json()["imports"] == []


def test_viewer_can_check_connection_status_but_cannot_import(workspace):
    _, _ = workspace
    owner, _ = register("connector-owner@example.com")
    invitation = owner.post("/api/workspace/invites", json={"email": "connector-viewer@example.com", "role": "viewer"})
    assert invitation.status_code == 200
    viewer, _ = register("connector-viewer@example.com", invite_token=invitation.json()["token"])
    assert viewer.get("/api/connections").status_code == 200
    assert upload(viewer, "shopify").status_code == 403
    assert owner.get("/api/connections").json()["imports"] == []


def test_demo_account_is_private_real_login_and_idempotent(workspace):
    from backend.demo_account import create_demo_account

    root, _ = workspace
    result = create_demo_account(root, "ui-demo@example.com", PASSWORD)
    assert result["created"] and result["email"] == "ui-demo@example.com"
    assert result["returns"] == 160 and result["products"] == 10
    db = WorkspaceStore(root, result["workspace_id"])
    assert all(item["source"] == "synthetic" for item in db.all("returns"))
    assert len(root.all("returns")) == 160  # The separate global sample remains untouched.
    assert not db.all("ai_calls") and db.get("meta", "gemini_last_call") is None

    client = TestClient(main.app)
    login = client.post("/api/auth/login", json={"email": result["email"], "password": PASSWORD})
    assert login.status_code == 200, login.text
    session = client.get("/api/auth/session").json()
    assert session["role"] == "owner" and not session["demo"]
    assert session["workspace"]["id"] == result["workspace_id"]
    assert client.get("/api/dashboard").json()["counts"]["returns"] == 160
    user = root.get("auth_users", session["user"]["id"])
    original_hash = user["password_hash"]
    assert original_hash != PASSWORD and ":" in original_hash

    action = db.get("actions", "listing-p1")
    db.put("actions", action["id"], {**action, "text": "My UI review edit", "status": "dismissed"})
    repeat = create_demo_account(root, "ui-demo@example.com", "Different123!")
    assert not repeat["created"] and repeat["workspace_id"] == result["workspace_id"]
    assert root.get("auth_users", session["user"]["id"])["password_hash"] == original_hash
    assert db.get("actions", "listing-p1")["text"] == "My UI review edit"
    assert len(db.all("returns")) == 160 and len(db.all("products")) == 10

    other, _ = register("ordinary-merchant@example.com")
    assert other.get("/api/dashboard").json()["counts"]["returns"] == 0
    assert other.get("/api/dashboard", headers={"X-Workspace-ID": result["workspace_id"]}).status_code == 403


def test_demo_seed_refuses_to_overwrite_an_existing_merchant(workspace):
    from backend.demo_account import create_demo_account

    root, _ = workspace
    _, session = register("existing-merchant@example.com", "Real Merchant")
    before = root.get("auth_users", session["user"]["id"])
    with pytest.raises(ValueError):
        create_demo_account(root, "existing-merchant@example.com", "Different123!")
    assert root.get("auth_users", session["user"]["id"]) == before
    db = WorkspaceStore(root, session["workspace"]["id"])
    assert not db.all("returns") and not db.all("products")
