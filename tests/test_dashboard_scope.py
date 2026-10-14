"""A dashboard source selection applies to decisions and products, too."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.analysis import dashboard
from backend.store import LocalStore


@pytest.fixture
def source_workspace(tmp_path, monkeypatch):
    db = LocalStore(str(tmp_path / "sources.db"))
    monkeypatch.setattr(main, "store", db)
    monkeypatch.setattr(main, "DEMO_MODE", True)
    monkeypatch.setattr(main, "SERVICE_ROLE", "api")
    monkeypatch.setenv("AUTH_PROVIDER", "local")
    timestamp = datetime.now(timezone.utc)
    for key, source in [("shop", "shopify"), ("sample", "synthetic"), ("file", "csv"), ("woo", "woocommerce"), ("shop-without-feedback", "shopify")]:
        db.put("products", key, {"id": key, "name": key, "variant": "One variant", "source": source})
        db.put("analysis_reports", key, {"id": key, "observations": []})
    for source, key, issue in [("shopify", "shop", "Size & fit"), ("synthetic", "sample", "Color mismatch"), ("csv", "file", "Packaging"), ("woocommerce", "woo", "Product quality")]:
        for index in range(5):
            record_id = f"{source}-{index}"
            db.put("returns", record_id, {"id": record_id, "product_id": key, "reason": issue, "issue": issue, "source": source, "status": "received", "returned_at": timestamp.isoformat()})
        db.put("actions", f"{key}-draft", {"id": f"{key}-draft", "product_id": key, "source": source, "status": "draft", "kind": "listing"})
        db.put("actions", f"{key}-fit", {"id": f"{key}-fit", "product_id": key, "source": source, "status": "approved", "kind": "fit_check", "measurements_verified": True, "width":26,"height":37,"depth":3})
    # Historical actions may predate the explicit source field.
    db.put("actions", "legacy-shop", {"id": "legacy-shop", "product_id": "shop", "status": "draft", "kind": "inspection"})
    # The evidence source on an action takes precedence over its product source.
    db.put("actions", "sample-for-shop", {"id": "sample-for-shop", "product_id": "shop", "source": "synthetic", "status": "draft", "kind": "listing"})
    old = (timestamp - timedelta(days=60)).isoformat()
    db.put("returns", "old-shop", {"id": "old-shop", "product_id": "shop", "reason": "Too small", "issue": "Size & fit", "source": "shopify", "status": "received", "returned_at": old})
    db.put("returns", "declined-shop", {"id": "declined-shop", "product_id": "shop", "reason": "Too small", "issue": "Size & fit", "source": "shopify", "status": "declined", "returned_at": timestamp.isoformat()})
    return db, TestClient(main.app, headers={"X-Demo-Workspace": "true"})


def test_shopify_dashboard_counts_match_the_selected_actions(source_workspace):
    _, client = source_workspace
    response = client.get("/api/dashboard?source=shopify&days=28")
    assert response.status_code == 200
    result = response.json()
    assert result["counts"] == {"returns": 5, "patterns": 1, "pending": 2, "fit_ready": 1}
    assert {action["id"] for action in result["actions"]} == {"shop-draft", "legacy-shop", "shop-fit"}
    assert {product["id"] for product in result["products"]} == {"shop", "shop-without-feedback"}
    assert {report["id"] for report in result["reports"]} == {"shop", "shop-without-feedback"}
    assert result["source_counts"] == {"shopify": 5}
    assert {record["source"] for pattern in result["patterns"] for record in pattern["evidence"]} == {"shopify"}
    assert sum(day["returns"] for day in result["trend"]) == 5


@pytest.mark.parametrize("source,expected_pending", [("synthetic", 2), ("csv", 1), ("woocommerce", 1)])
def test_each_source_excludes_decisions_from_other_sources(source_workspace, source, expected_pending):
    db, _ = source_workspace
    result = dashboard(db, source=source)
    assert result["counts"]["returns"] == 5
    assert result["counts"]["patterns"] == 1
    assert result["counts"]["pending"] == expected_pending
    assert result["counts"]["fit_ready"] == 1
    assert {action["source"] for action in result["actions"]} == {source}


def test_all_sources_keeps_the_complete_workspace(source_workspace):
    db, _ = source_workspace
    result = dashboard(db)
    assert result["counts"] == {"returns": 20, "patterns": 4, "pending": 6, "fit_ready": 4}
    assert len(result["products"]) == 5
    assert len(result["reports"]) == 5
    assert len(result["actions"]) == 10


def test_live_checks_and_pending_work_do_not_expire_with_feedback_window(source_workspace):
    db, _ = source_workspace
    for record in db.all("returns"):
        db.put("returns", record["id"], {**record, "returned_at": (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()})
    result = dashboard(db, days=7, source="shopify")
    assert result["counts"] == {"returns": 0, "patterns": 0, "pending": 2, "fit_ready": 1}
    assert {product["id"] for product in result["products"]} == {"shop", "shop-without-feedback"}


def test_products_linked_to_source_feedback_survive_legacy_source_mismatch(source_workspace):
    db, _ = source_workspace
    db.put("returns", "legacy-import", {"id": "legacy-import", "product_id": "sample", "reason": "Too small", "issue": "Size & fit", "source": "shopify", "status": "received", "returned_at": datetime.now(timezone.utc).isoformat()})
    result = dashboard(db, source="shopify")
    assert "sample" in {product["id"] for product in result["products"]}
    assert "sample" in {report["id"] for report in result["reports"]}
    assert "sample-draft" not in {action["id"] for action in result["actions"]}
    assert result["counts"]["pending"] == 2
