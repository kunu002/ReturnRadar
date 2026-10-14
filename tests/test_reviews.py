"""Review outcomes require actual feedback and exact sales-item matches."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend import reviews
from backend.reviews import CycleCreate, CycleUpdate, create_cycle, update_cycle, review_summary
from backend.store import LocalStore, WorkspaceStore
from test_workflows import workspace


@pytest.fixture
def review_workspace(tmp_path, monkeypatch):
    db = LocalStore(str(tmp_path / "reviews.db"))
    db.put("products", "p1", {"id": "p1", "name": "Everyday Trainers", "variant": "Size 8"})
    clock = {"date": datetime(2026, 10, 8, 10, tzinfo=timezone.utc)}
    monkeypatch.setattr(reviews, "now", lambda: clock["date"].isoformat())
    return db, clock


def feedback(db, key, timestamp, issue="Size and fit", **overrides):
    db.put("returns", key, {"id": key, "product_id": "p1", "issue": issue, "source": "csv", "reason": "Too small", "status": "received", "returned_at": timestamp.isoformat(), **overrides})


def plan(**overrides):
    return CycleCreate(product_id="p1", issue="Size and fit", goal="Reduce sizing complaints.", change_plan="Add verified foot-length measurements to the sizing guide.", review_days=14, **overrides)


def test_review_preserves_baseline_and_freezes_completed_feedback(review_workspace):
    db, clock = review_workspace
    original = clock["date"]
    for number in range(6):
        feedback(db, f"before-{number}", original - timedelta(days=2), issue="Size and fit" if number < 4 else "Product quality")
    cycle = create_cycle(db, plan(), "analyst")
    assert cycle["baseline"]["feedback_count"] == 6
    assert cycle["baseline"]["issue_count"] == 4
    assert cycle["baseline"]["issue_share"] == 66.7
    assert cycle["status"] == "planned" and cycle["assessment"]["status"] == "planned"
    active = update_cycle(db, cycle["id"], CycleUpdate(status="active"), "owner")
    assert active["applied_by"] == "owner"
    assert active["target_review_date"] == (original + timedelta(days=14)).isoformat()
    clock["date"] += timedelta(days=15)
    for number in range(5):
        feedback(db, f"after-{number}", original + timedelta(days=3), issue="Size and fit" if number == 0 else "Product quality")
    # A later classification edit must not retroactively alter the saved baseline.
    feedback(db, "before-0", original - timedelta(days=2), issue="Product quality")
    current = review_summary(db)["cycles"][0]
    assert current["baseline"]["issue_count"] == 4
    assert current["after"]["feedback_count"] == 5
    assert current["assessment"]["ready"] and current["assessment"]["status"] == "improving"
    assert current["assessment"]["change_pp"] == -46.7
    assert current["review_due"]
    finished = update_cycle(db, cycle["id"], CycleUpdate(status="completed", outcome_note="Sizing concerns are mentioned less often. Keep monitoring."), "admin")
    assert finished["completed_by"] == "admin"
    feedback(db, "new-after-completion", clock["date"] + timedelta(hours=1))
    clock["date"] += timedelta(days=2)
    saved = review_summary(db)
    assert saved["cycles"][0]["after"] == finished["after"]
    assert saved["counts"] == {"planned": 0, "active": 0, "completed": 1, "due": 0}
    assert "does not prove" in saved["cycles"][0]["assessment"]["note"]


def test_feedback_samples_separate_refunds_and_ignore_cancelled_or_unclassified(review_workspace):
    db, clock = review_workspace
    yesterday = clock["date"] - timedelta(days=1)
    feedback(db, "received", yesterday)
    feedback(db, "refund", yesterday, record_kind="refund_feedback", physical_return_confirmed=False, status="closed")
    feedback(db, "refund-incorrect-status", yesterday, record_kind="refund_feedback", physical_return_confirmed=False)
    feedback(db, "cancelled", yesterday, status="canceled")
    feedback(db, "declined", yesterday, status="declined")
    feedback(db, "unknown", yesterday, issue=None)
    feedback(db, "outside-window", yesterday - timedelta(days=20))
    feedback(db, "another-product", yesterday, product_id="other-product")
    cycle = create_cycle(db, plan(), "owner")
    before = cycle["baseline"]
    assert before["feedback_count"] == 3
    assert before["return_feedback_count"] == 1
    assert before["refund_feedback_count"] == 2
    assert before["received_count"] == 1
    assert before["unclassified_count"] == 1
    assert set(before["evidence_ids"]) == {"received", "refund", "refund-incorrect-status"}
    update_cycle(db, cycle["id"], CycleUpdate(status="active"), "owner")
    clock["date"] += timedelta(days=1)
    for number in range(5):
        feedback(db, f"new-{number}", clock["date"] - timedelta(hours=1))
    current = review_summary(db)["cycles"][0]
    assert not current["assessment"]["ready"]
    assert current["assessment"]["change_pp"] is None


def test_review_dates_transitions_and_duplicate_plans_are_guarded(review_workspace):
    db, clock = review_workspace
    with pytest.raises(HTTPException) as missing:
        create_cycle(db, CycleCreate(**{**plan().model_dump(), "product_id": "other-merchant-product"}), "owner")
    assert missing.value.status_code == 404
    cycle = create_cycle(db, plan(), "owner")
    with pytest.raises(HTTPException) as duplicate:
        create_cycle(db, plan(), "owner")
    assert duplicate.value.status_code == 409
    with pytest.raises(HTTPException):
        update_cycle(db, cycle["id"], CycleUpdate(status="completed", outcome_note="Attempted too early"), "owner")
    with pytest.raises(HTTPException) as future:
        update_cycle(db, cycle["id"], CycleUpdate(status="active", applied_at=(clock["date"] + timedelta(days=1)).isoformat()), "owner")
    assert future.value.status_code == 422
    with pytest.raises(HTTPException):
        update_cycle(db, cycle["id"], CycleUpdate(status="active", applied_at="yesterday"), "owner")
    update_cycle(db, cycle["id"], CycleUpdate(status="active"), "owner")
    with pytest.raises(HTTPException):
        update_cycle(db, cycle["id"], CycleUpdate(status="planned"), "owner")
    with pytest.raises(HTTPException) as no_note:
        update_cycle(db, cycle["id"], CycleUpdate(status="completed"), "owner")
    assert no_note.value.status_code == 422
    update_cycle(db, cycle["id"], CycleUpdate(status="completed", outcome_note="Need more feedback. Extend monitoring."), "owner")
    with pytest.raises(HTTPException):
        update_cycle(db, cycle["id"], CycleUpdate(status="active"), "owner")
    assert create_cycle(db, plan(), "owner")["status"] == "planned"
    with pytest.raises(ValidationError):
        CycleCreate(**{**plan().model_dump(), "goal": "    a   "})
    with pytest.raises(ValidationError):
        CycleCreate(**{**plan().model_dump(), "review_days": 6})


def test_received_share_requires_exact_sold_line_and_never_counts_refunds(review_workspace):
    db, clock = review_workspace
    db.put("orders", "o1", {"id": "o1", "status": "completed", "source": "woocommerce", "connector_id": "store-a", "items": [{"id": "line-1", "product_id": "p1", "variant_id": "size-8", "quantity": 10}]})
    db.put("orders", "pending", {"id": "pending", "status": "pending", "items": [{"id": "pending-line", "product_id": "p1", "quantity": 100}]})
    db.put("orders", "legacy", {"id": "legacy", "status": "fulfilled", "items": [{"id": "unknown-quantity", "product_id": "p1"}]})
    feedback(db, "matched", clock["date"], order_id="o1", order_item_id="line-1", quantity=2, source="woocommerce", connector_id="store-a", variant_id="size-8")
    feedback(db, "refund-feedback", clock["date"], order_id="o1", order_item_id="line-1", quantity=8, source="woocommerce", connector_id="store-a", record_kind="refund_feedback", physical_return_confirmed=False)
    db.put("refunds", "refund", {"id": "refund", "order_id": "o1", "items": [{"product_id": "p1", "order_item_id": "line-1", "quantity": 3}]})
    product = review_summary(db)["products"][0]
    assert product["sold_units"] == 10
    assert product["refunded_units"] == 3
    assert product["matched_received_units"] == 2
    assert product["received_share"] == 20
    feedback(db, "unknown-line", clock["date"], order_id="o1", order_item_id="missing", quantity=2, source="woocommerce", connector_id="store-a")
    product = review_summary(db)["products"][0]
    assert product["received_share"] is None
    assert product["unmatched_received_records"] == 1
    assert product["matched_received_units"] == 2


def test_review_sales_reject_cross_store_wrong_variant_and_overcounted_units(review_workspace):
    db, clock = review_workspace
    db.put("orders", "o1", {"id": "o1", "status": "paid", "source": "shopify", "connector_id": "store-a", "items": [{"id": "line-1", "product_id": "p1", "variant_id": "v1", "quantity": 3}]})
    common = {"order_id": "o1", "order_item_id": "line-1", "quantity": 2, "source": "shopify", "connector_id": "store-a", "variant_id": "v1"}
    feedback(db, "first", clock["date"], **common)
    feedback(db, "overflow", clock["date"], **common)
    feedback(db, "other-store", clock["date"], **{**common, "connector_id": "store-b"})
    feedback(db, "other-variant", clock["date"], **{**common, "variant_id": "v2"})
    product = review_summary(db)["products"][0]
    assert product["sold_units"] == 3
    assert product["matched_received_units"] == 2
    assert product["unmatched_received_records"] == 3
    assert product["received_share"] is None


def test_shopify_open_and_closed_requests_show_recorded_sales_share_without_receipt(review_workspace):
    db, clock = review_workspace
    db.put("products", "p1", {"id": "p1", "name": "Sample Trainers", "variant": "Size 8", "source": "synthetic"})
    db.put("orders", "shopify-order", {"id": "shopify-order", "status": "paid", "source": "shopify", "connector_id": "store-a", "items": [{"id": "line-1", "product_id": "p1", "variant_id": "v1", "quantity": 20}]})
    common = {"order_id": "shopify-order", "order_item_id": "line-1", "source": "shopify", "connector_id": "store-a", "variant_id": "v1"}
    feedback(db, "open-request", clock["date"], status="OPEN", quantity=4, **common)
    feedback(db, "closed-request", clock["date"], status="closed", quantity=1, **common)
    feedback(db, "cancelled-request", clock["date"], status="CANCELED", quantity=5, **common)
    feedback(db, "declined-request", clock["date"], status="declined", quantity=5, **common)
    feedback(db, "refund-only", clock["date"], status="closed", quantity=10, record_kind="refund_feedback", physical_return_confirmed=False, **common)
    product = review_summary(db)["products"][0]
    assert product["matched_requested_units"] == 5
    assert product["request_share"] == 25
    assert product["unmatched_requested_records"] == 0
    assert product["matched_received_units"] == 0
    assert product["received_share"] is None
    assert product["source"] == "synthetic"
    assert "recorded sales only" in product["request_note"]
    assert "does not confirm physical receipt" in product["request_note"]


@pytest.mark.parametrize("incorrect", [
    {"variant_id": "another-variant"},
    {"quantity": 4},
    {"quantity": None},
    {"connector_id": "another-store"},
    {"source": "csv"},
])
def test_invalid_request_matches_or_quantities_suppress_a_misleading_share(review_workspace, incorrect):
    db, clock = review_workspace
    db.put("orders", "order", {"id": "order", "status": "paid", "source": "shopify", "connector_id": "store-a", "items": [{"id": "line", "product_id": "p1", "variant_id": "v1", "quantity": 3}]})
    common = {"order_id": "order", "order_item_id": "line", "source": "shopify", "connector_id": "store-a", "variant_id": "v1", "quantity": 1, "status": "open"}
    feedback(db, "a-valid-request", clock["date"], **common)
    feedback(db, "b-invalid-request", clock["date"], **{**common, **incorrect})
    product = review_summary(db)["products"][0]
    assert product["matched_requested_units"] == 1
    assert product["unmatched_requested_records"] == 1
    assert product["request_share"] is None
    assert product["matched_requested_units"] <= product["sold_units"]
    assert product["received_share"] is None


def test_review_cycles_are_workspace_scoped(review_workspace):
    root, clock = review_workspace
    merchant_a, merchant_b = WorkspaceStore(root, "merchant-a"), WorkspaceStore(root, "merchant-b")
    merchant_a.put("products", "p1", root.get("products", "p1"))
    cycle = create_cycle(merchant_a, plan(), "owner-a")
    assert review_summary(merchant_a)["counts"]["planned"] == 1
    assert review_summary(merchant_b)["cycles"] == []
    with pytest.raises(HTTPException) as missing:
        update_cycle(merchant_b, cycle["id"], CycleUpdate(status="active"), "owner-b")
    assert missing.value.status_code == 404
    assert not review_summary(root)["cycles"]


def test_review_api_enforces_roles_and_merchant_memberships(workspace):
    from fastapi.testclient import TestClient
    from backend import main

    root, _ = workspace

    def account(email, invitation=""):
        client = TestClient(main.app)
        response = client.post("/api/auth/register", json={"name": "Review teammate", "email": email, "password": "Review123!", "workspace_name": "Review merchant", "invite_token": invitation})
        assert response.status_code == 200, response.text
        return client, response.json()

    owner, session = account("review-owner@example.com")
    merchant = WorkspaceStore(root, session["workspace"]["id"])
    merchant.put("products", "p1", {"id": "p1", "name": "Review Trainers", "variant": "Size 8"})
    invitation = owner.post("/api/workspace/invites", json={"email": "review-analyst@example.com", "role": "analyst"}).json()
    analyst, _ = account("review-analyst@example.com", invitation["token"])
    invitation = owner.post("/api/workspace/invites", json={"email": "review-viewer@example.com", "role": "viewer"}).json()
    viewer, _ = account("review-viewer@example.com", invitation["token"])
    assert TestClient(main.app).get("/api/reviews").status_code == 401
    assert viewer.get("/api/reviews").status_code == 200
    assert viewer.post("/api/reviews", json=plan().model_dump()).status_code == 403
    created = analyst.post("/api/reviews", json=plan().model_dump())
    assert created.status_code == 200, created.text
    cycle = created.json()
    assert analyst.patch(f"/api/reviews/{cycle['id']}", json={"status": "active"}).status_code == 403
    assert viewer.patch(f"/api/reviews/{cycle['id']}", json={"status": "active"}).status_code == 403
    assert owner.patch(f"/api/reviews/{cycle['id']}", json={"status": "active"}).status_code == 200
    stranger, _ = account("review-other-merchant@example.com")
    assert stranger.get("/api/reviews").json()["cycles"] == []
    assert stranger.patch(f"/api/reviews/{cycle['id']}", json={"status": "completed", "outcome_note": "Attempt to modify another store"}).status_code == 404
    assert stranger.get("/api/reviews", headers={"X-Workspace-ID": session["workspace"]["id"]}).status_code == 403
