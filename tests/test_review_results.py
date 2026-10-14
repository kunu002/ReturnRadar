"""Comparable purchase cohorts prevent premature claims about fewer requests."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from backend import reviews
from backend.reviews import CycleCreate, CycleUpdate, create_cycle, update_cycle, review_summary
from backend.store import LocalStore, WorkspaceStore


@pytest.fixture
def result_workspace(tmp_path, monkeypatch):
    db = LocalStore(str(tmp_path / "results.db"))
    db.put("products", "p1", {"id": "p1", "name": "Everyday Trainers", "source": "shopify", "connector_id": "store-a"})
    clock = {"date": datetime(2026, 9, 10, 10, tzinfo=timezone.utc)}
    monkeypatch.setattr(reviews, "now", lambda: clock["date"].isoformat())
    return db, clock


def plan(**overrides):
    return CycleCreate(**{"product_id": "p1", "issue": "Size and fit", "goal": "Fewer requests from incorrect sizing.",
                          "change_plan": "Add verified foot-length measurements to the size guide.",
                          "review_days": 7, "follow_up_days": 7, **overrides})


def order(db, key, timestamp, quantity=1, **overrides):
    value = {"id": key, "created_at": timestamp.isoformat(), "status": "paid", "source": "shopify", "connector_id": "store-a",
             "items": [{"id": key + "-line", "product_id": "p1", "variant_id": "v1", "quantity": quantity}], **overrides}
    db.put("orders", key, value)
    return value


def request(db, key, sold, timestamp, quantity=1, **overrides):
    db.put("returns", key, {"id": key, "return_id": "request-" + key, "product_id": "p1", "order_id": sold["id"],
                            "order_item_id": sold["items"][0]["id"], "variant_id": "v1", "quantity": quantity,
                            "status": "open", "source": "shopify", "connector_id": "store-a", "issue": "Size and fit",
                            "returned_at": timestamp.isoformat(), **overrides})


def cohorts(db, anchor, before_orders=10, after_orders=10, before_requests=3, after_requests=1):
    for prefix, count, shift, requests in (("before", before_orders, -5, before_requests), ("after", after_orders, 2, after_requests)):
        purchase = anchor + timedelta(days=shift)
        for number in range(count):
            sold = order(db, f"{prefix}-{number}", purchase)
            if number < requests:
                request(db, f"{prefix}-{number}", sold, purchase + timedelta(days=2))


def sync(db, anchor, timestamp, **overrides):
    db.put("sync_runs", "full-update", {"id": "full-update", "connector_id": "store-a", "source": "shopify", "at": timestamp.isoformat(),
                                         "coverage": {"start": (anchor - timedelta(days=7)).isoformat(), "end": timestamp.isoformat()}, **overrides})


def activate(db):
    cycle = create_cycle(db, plan(), "owner")
    return update_cycle(db, cycle["id"], CycleUpdate(status="active", applied_note="Verified the measurements against current stock."), "owner")


def test_each_purchase_has_equal_followup_and_comparison_waits_for_complete_period(result_workspace):
    db, clock = result_workspace
    anchor = clock["date"]
    cohorts(db, anchor)
    cycle = activate(db)
    assert cycle["applied_note"] == "Verified the measurements against current stock."
    assert cycle["results"]["eligible_at"] == (anchor + timedelta(days=14)).isoformat()
    clock["date"] = anchor + timedelta(days=7)
    sync(db, anchor, clock["date"])
    waiting = review_summary(db)["cycles"][0]["results"]
    assert waiting["status"] == "waiting" and not waiting["ready"]
    assert waiting["after"]["request_share"] is None
    clock["date"] = anchor + timedelta(days=14)
    sync(db, anchor, clock["date"])
    results = review_summary(db)["cycles"][0]["results"]
    assert results["ready"] and results["status"] == "improving"
    assert results["before"]["sold_units"] == results["after"]["sold_units"] == 10
    assert results["before"]["request_share"] == 30
    assert results["after"]["request_share"] == 10
    assert results["change_pp"] == -20
    assert results["confidence"] == "early_signal"
    assert results["request_count_change"] == -2
    assert "does not prove" in results["note"]


def test_request_count_can_rise_while_unit_share_falls(result_workspace):
    db, clock = result_workspace
    anchor = clock["date"]
    cohorts(db, anchor, before_orders=5, after_orders=20, before_requests=1, after_requests=3)
    activate(db)
    clock["date"] += timedelta(days=14)
    sync(db, anchor, clock["date"])
    result = review_summary(db)["cycles"][0]["results"]
    assert result["change_pp"] == -5
    assert result["before"]["return_request_count"] == 1
    assert result["after"]["return_request_count"] == 3
    assert result["request_count_change"] == 2
    assert result["request_count_change_pct"] == 200
    assert result["sales_change_pct"] == 300
    assert result["status"] == "improving"  # Refers to item share, never the request count.


def test_requests_after_fixed_followup_are_excluded_and_zero_is_valid(result_workspace):
    db, clock = result_workspace
    anchor = clock["date"]
    cohorts(db, anchor, before_requests=2, after_requests=0)
    sold = db.get("orders", "after-0")
    request(db, "late-after", sold, anchor + timedelta(days=10))  # Day eight for this purchase.
    activate(db)
    clock["date"] += timedelta(days=14)
    sync(db, anchor, clock["date"])
    result = review_summary(db)["cycles"][0]["results"]
    assert result["ready"]
    assert result["after"]["request_share"] == 0
    assert result["after"]["matched_requested_units"] == 0
    assert result["after"]["return_request_count"] == 0
    assert result["request_count_change"] == -2


def test_request_ids_deduplicate_multiple_items_in_one_product_request(result_workspace):
    db, clock = result_workspace
    anchor = clock["date"]
    cohorts(db, anchor, before_requests=0, after_requests=0)
    sold = db.get("orders", "before-0")
    sold["items"].append({"id": "second-line", "product_id": "p1", "variant_id": "v1", "quantity": 1})
    db.put("orders", sold["id"], sold)
    at = anchor - timedelta(days=3)
    request(db, "first-item", sold, at, return_id="same-request")
    request(db, "second-item", sold, at, return_id="same-request", order_item_id="second-line")
    activate(db)
    clock["date"] += timedelta(days=14)
    sync(db, anchor, clock["date"])
    result = review_summary(db)["cycles"][0]["results"]
    assert result["before"]["matched_requested_units"] == 2
    assert result["before"]["return_request_count"] == 1
    assert result["before"]["orders"] == 10


def test_missing_request_identity_does_not_invent_number_of_requests(result_workspace):
    db, clock = result_workspace
    anchor = clock["date"]
    cohorts(db, anchor)
    record = db.get("returns", "before-0")
    record.pop("return_id")
    db.put("returns", record["id"], record)
    activate(db)
    clock["date"] += timedelta(days=14)
    sync(db, anchor, clock["date"])
    result = review_summary(db)["cycles"][0]["results"]
    assert result["ready"] and result["change_pp"] == -20
    assert result["before"]["return_request_count"] is None
    assert result["before"]["known_request_count"] == 2
    assert result["before"]["missing_request_id_records"] == 1
    assert result["request_count_change"] is None


@pytest.mark.parametrize("problem", ["stale_update", "missing_history", "unknown_date", "bad_quantity", "missing_sync"])
def test_incomplete_history_or_sales_details_prevent_rate_claims(result_workspace, problem):
    db, clock = result_workspace
    anchor = clock["date"]
    cohorts(db, anchor)
    activate(db)
    clock["date"] += timedelta(days=14)
    if problem != "missing_sync":
        sync(db, anchor, clock["date"])
    if problem == "stale_update":
        sync(db, anchor, anchor + timedelta(days=7))
    elif problem == "missing_history":
        sync(db, anchor, clock["date"], coverage={"start": anchor.isoformat(), "end": clock["date"].isoformat()})
    elif problem in {"unknown_date", "bad_quantity"}:
        value = db.get("orders", "before-0")
        if problem == "unknown_date":
            value.pop("created_at")
        else:
            value["items"][0]["quantity"] = True
        db.put("orders", value["id"], value)
    result = review_summary(db)["cycles"][0]["results"]
    assert not result["ready"] and result["status"] == "coverage_gap"
    assert result["change_pp"] is None
    assert result["request_count_change"] is None


@pytest.mark.parametrize("incorrect", [{"variant_id": "wrong"}, {"source": "csv"}, {"connector_id": "other-store"}, {"quantity": 2}, {"returned_at": "not-a-date"}, {"order_id": "unknown-order"}])
def test_invalid_request_link_suppresses_comparison(result_workspace, incorrect):
    db, clock = result_workspace
    anchor = clock["date"]
    cohorts(db, anchor)
    value = db.get("returns", "after-0")
    value.update(incorrect)
    db.put("returns", value["id"], value)
    activate(db)
    clock["date"] += timedelta(days=14)
    sync(db, anchor, clock["date"])
    result = review_summary(db)["cycles"][0]["results"]
    assert not result["ready"] and result["status"] == "unmatched"
    assert result["after"]["request_share"] is None
    assert result["after"]["return_request_count"] is None


def test_refunds_and_cancelled_or_declined_requests_do_not_count(result_workspace):
    db, clock = result_workspace
    anchor = clock["date"]
    cohorts(db, anchor, before_requests=1, after_requests=0)
    sold = db.get("orders", "after-0")
    at = anchor + timedelta(days=3)
    request(db, "cancelled", sold, at, status="canceled")
    request(db, "declined", sold, at, status="declined")
    request(db, "refund-only", sold, at, record_kind="refund_feedback", status="closed")
    activate(db)
    clock["date"] += timedelta(days=14)
    sync(db, anchor, clock["date"])
    result = review_summary(db)["cycles"][0]["results"]
    assert result["after"]["matched_requested_units"] == 0
    assert result["after"]["request_share"] == 0
    assert result["after"]["return_request_count"] == 0


def test_bulk_purchase_does_not_replace_independent_order_sample(result_workspace):
    db, clock = result_workspace
    anchor = clock["date"]
    for prefix, shift in (("before", -5), ("after", 2)):
        order(db, prefix, anchor + timedelta(days=shift), quantity=50)
    activate(db)
    clock["date"] += timedelta(days=14)
    sync(db, anchor, clock["date"])
    result = review_summary(db)["cycles"][0]["results"]
    assert result["before"]["sold_units"] == result["after"]["sold_units"] == 50
    assert result["before"]["orders"] == result["after"]["orders"] == 1
    assert result["status"] == "insufficient" and not result["ready"]


def test_woocommerce_refund_connection_does_not_invent_zero_requests(result_workspace):
    db, clock = result_workspace
    anchor = clock["date"]
    db.put("products", "p1", {"id": "p1", "name": "Everyday Trainers", "source": "woocommerce", "connector_id": "store-a"})
    cohorts(db, anchor, before_requests=0, after_requests=0)
    for value in db.all("orders"):
        value["source"] = "woocommerce"
        db.put("orders", value["id"], value)
    activate(db)
    clock["date"] += timedelta(days=14)
    sync(db, anchor, clock["date"], source="woocommerce")
    result = review_summary(db)["cycles"][0]["results"]
    assert result["status"] == "coverage_gap" and not result["ready"]
    assert result["before"]["request_share"] is None
    assert result["after"]["request_share"] is None
    assert "return-request history is not available" in result["after"]["coverage_note"]
    assert "separate, complete request history" in result["next_step"]


def test_results_freeze_on_completion_and_checkpoints_keep_change_intact(result_workspace):
    db, clock = result_workspace
    anchor = clock["date"]
    cohorts(db, anchor)
    cycle = activate(db)
    clock["date"] += timedelta(days=7)
    continued = update_cycle(db, cycle["id"], CycleUpdate(status="active", decision="continue", outcome_note="Size guide is live; wait for the full return follow-up.", next_check_in_days=7), "owner")
    assert continued["target_review_date"] == (anchor + timedelta(days=14)).isoformat()
    assert continued["applied_at"] == anchor.isoformat()
    assert continued["checkpoints"][0]["decision"] == "continue"
    clock["date"] += timedelta(days=7)
    sync(db, anchor, clock["date"])
    completed = update_cycle(db, cycle["id"], CycleUpdate(status="completed", decision="keep", outcome_note="Keep the guide provisionally and repeat with new purchases."), "owner")
    assert completed["results"]["ready"]
    assert completed["final_decision"] == "keep"
    request(db, "late-import-of-existing-request", db.get("orders", "after-1"), anchor + timedelta(days=4))
    clock["date"] += timedelta(days=7)
    saved = review_summary(db)["cycles"][0]
    assert saved["results"] == completed["results"]
    assert len(saved["checkpoints"]) == 2
    assert saved["results"]["after"]["return_request_count"] == 1


def test_assignment_requires_active_same_workspace_editor_and_hides_email(result_workspace):
    root, clock = result_workspace
    first, other = WorkspaceStore(root, "first"), WorkspaceStore(root, "other")
    first.put("products", "p1", root.get("products", "p1"))
    for uid, role, active in (("owner", "owner", True), ("analyst", "analyst", True), ("viewer", "viewer", True), ("removed", "analyst", False)):
        first.put("members", uid, {"id": uid, "name": uid.title(), "email": uid + "@example.com", "role": role, "active": active})
    other.put("members", "foreign", {"id": "foreign", "name": "Foreign", "role": "owner", "active": True})
    for invalid in ("viewer", "removed", "foreign"):
        with pytest.raises(HTTPException) as failure:
            create_cycle(first, plan(owner_id=invalid), "owner")
        assert failure.value.status_code == 422
    cycle = create_cycle(first, plan(owner_id="analyst"), "owner")
    assert cycle["owner_name"] == "Analyst" and cycle["owner_active"]
    assert "owner_email" not in cycle
    summary = review_summary(first)
    assert {member["id"] for member in summary["assignees"]} == {"owner", "analyst"}
    assert all("email" not in member for member in summary["assignees"])
    assigned = update_cycle(first, cycle["id"], CycleUpdate(owner_id="owner"), "owner")
    assert assigned["owner_name"] == "Owner" and assigned["status"] == "planned"
    member = first.get("members", "owner")
    first.put("members", "owner", {**member, "active": False})
    assert not review_summary(first)["cycles"][0]["owner_active"]


def test_feedback_uses_equal_windows_and_does_not_compare_partial_after_period(result_workspace):
    db, clock = result_workspace
    anchor = clock["date"]
    for prefix, shift in (("before", -2), ("after", 2)):
        for number in range(5):
            sold = order(db, f"{prefix}-{number}", anchor + timedelta(days=shift-1))
            request(db, f"{prefix}-{number}", sold, anchor + timedelta(days=shift))
    cycle = activate(db)
    clock["date"] += timedelta(days=3)
    assert not review_summary(db)["cycles"][0]["assessment"]["ready"]
    extra = order(db, "out-of-window", anchor + timedelta(days=8))
    request(db, "out-of-window", extra, anchor + timedelta(days=9), issue="Product quality")
    clock["date"] += timedelta(days=10)
    current = review_summary(db)["cycles"][0]
    assert current["assessment"]["ready"]
    assert current["baseline"]["feedback_count"] == current["after"]["feedback_count"] == 5
    assert current["after"]["to"] == (anchor + timedelta(days=7)).isoformat()


def test_checkin_decisions_require_an_applied_change_and_meaningful_notes(result_workspace):
    db, _ = result_workspace
    cycle = create_cycle(db, plan(), "owner")
    with pytest.raises(HTTPException):
        update_cycle(db, cycle["id"], CycleUpdate(status="planned", decision="continue", outcome_note="Not yet applied"), "owner")
    update_cycle(db, cycle["id"], CycleUpdate(status="active"), "owner")
    with pytest.raises(HTTPException):
        update_cycle(db, cycle["id"], CycleUpdate(status="active", decision="continue", outcome_note="ok"), "owner")
    with pytest.raises(HTTPException):
        update_cycle(db, cycle["id"], CycleUpdate(status="active", decision="revise", outcome_note="Change needs revision."), "owner")
    completed = update_cycle(db, cycle["id"], CycleUpdate(status="completed", decision="revise", outcome_note="Close this test and plan a verified packaging change."), "owner")
    assert completed["final_decision"] == "revise"
    assert completed["results"]["status"] == "collecting"
    assert not completed["results"]["ready"]


def test_concurrent_edit_cannot_overwrite_a_saved_review_decision(result_workspace, monkeypatch):
    db, _ = result_workspace
    cycle = activate(db)
    actual_claim = db.claim

    def edit_before_claim(collection, key, eligible, updates):
        current = db.get(collection, key)
        db.put(collection, key, {**current, "version": current["version"] + 1, "outcome_note": "A teammate has saved a fresh check-in."})
        return actual_claim(collection, key, eligible, updates)

    monkeypatch.setattr(db, "claim", edit_before_claim)
    with pytest.raises(HTTPException) as conflict:
        update_cycle(db, cycle["id"], CycleUpdate(status="completed", outcome_note="Attempted close from the older view."), "owner")
    assert conflict.value.status_code == 409
    saved = db.get("review_cycles", cycle["id"])
    assert saved["status"] == "active"
    assert saved["outcome_note"] == "A teammate has saved a fresh check-in."
