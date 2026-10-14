"""Merchant review cycles, using saved feedback baselines rather than causal claims."""
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Literal
import math
import uuid

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .store import now


class CycleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: str = Field(min_length=1, max_length=180)
    issue: str = Field(min_length=2, max_length=100)
    goal: str = Field(min_length=5, max_length=500)
    change_plan: str = Field(min_length=5, max_length=1500)
    review_days: int = Field(default=14, ge=7, le=90)
    follow_up_days: int = Field(default=14, ge=7, le=90)
    owner_id: str = Field(default="", max_length=180)

    @field_validator("product_id", "issue", "goal", "change_plan", mode="before")
    @classmethod
    def trim_text(cls, value):
        if not isinstance(value, str):
            return value
        value = value.strip()
        if not value:
            raise ValueError("Please complete each review field.")
        return value

    @field_validator("owner_id", mode="before")
    @classmethod
    def trim_owner(cls, value):
        return value.strip() if isinstance(value, str) else value


class CycleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["planned", "active", "completed"] | None = None
    outcome_note: str = Field(default="", max_length=1500)
    applied_at: str | None = Field(default=None, max_length=60)
    applied_note: str = Field(default="", max_length=1500)
    owner_id: str | None = Field(default=None, min_length=1, max_length=180)
    decision: Literal["continue", "keep", "revise", "stop"] | None = None
    next_check_in_days: int = Field(default=7, ge=7, le=90)

    @field_validator("outcome_note", "applied_note", mode="before")
    @classmethod
    def trim_note(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("owner_id", mode="before")
    @classmethod
    def trim_owner(cls, value):
        return value.strip() if isinstance(value, str) else value


def _date(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError):
        return None


def _assignees(db):
    # Names and roles support assigning work without disclosing team contact details.
    members = [{"id": member["id"], "name": member.get("name") or "Teammate", "role": member["role"]}
               for member in db.all("members") if member.get("active") and member.get("role") in {"owner", "admin", "analyst"}]
    if not members and getattr(db, "workspace_id", "demo") == "demo":
        members = [{"id": "local-demo", "name": "Demo merchant", "role": "owner"}]
    return sorted(members, key=lambda member: member["name"].lower())


def _owner(db, owner_id, uid):
    selected = owner_id or uid
    member = db.get("members", selected)
    if member:
        if not member.get("active") or member.get("role") not in {"owner", "admin", "analyst"}:
            raise HTTPException(422, "Choose an active Owner, Admin or Analyst from this workspace.")
        return selected, member.get("name") or "Teammate"
    if selected == "local-demo" and getattr(db, "workspace_id", "demo") == "demo":
        return selected, "Demo merchant"
    if not owner_id:
        # The route already authorizes the caller. This also reads historical/demo
        # workspaces whose records predate member storage without trusting an ID.
        return uid, "Review owner"
    raise HTTPException(422, "Choose an active Owner, Admin or Analyst from this workspace.")


def _snapshot(db, product_id, issue, start, end):
    records = []
    unclassified = 0
    for record in db.all("returns"):
        returned_at = _date(record.get("returned_at"))
        if record.get("product_id") != product_id or returned_at is None or not start <= returned_at < end:
            continue
        if record.get("status", "").lower() in {"cancelled", "canceled", "declined"}:
            continue
        if not record.get("issue") or record["issue"] == "Unclassified":
            unclassified += 1
            continue
        records.append(record)
    issue_records = [record for record in records if record["issue"] == issue]
    refund_feedback = sum(record.get("record_kind") == "refund_feedback" for record in records)
    received = sum(record.get("status") == "received" and record.get("record_kind") != "refund_feedback" and record.get("physical_return_confirmed") is not False for record in records)
    return {
        "from": start.isoformat(), "to": end.isoformat(),
        "feedback_count": len(records), "issue_count": len(issue_records),
        "issue_share": round(len(issue_records) / len(records) * 100, 1) if records else None,
        "return_feedback_count": len(records) - refund_feedback,
        "refund_feedback_count": refund_feedback, "received_count": received,
        "unclassified_count": unclassified,
        "source_counts": dict(Counter(record.get("source", "unknown") for record in records)),
        "evidence_ids": [record["id"] for record in issue_records],
    }


_SALE_STATUSES = {"paid", "partially_paid", "fulfilled", "completed", "processing", "refunded", "partially_refunded"}
_REQUEST_STATUSES = {"requested", "open", "closed", "received"}


def _quantity(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _coverage(db, connector_ids, start, end, follow_up_days, timestamp):
    """A completed sync must cover purchases after their full follow-up has elapsed.

    Syncing current orders alone cannot establish a zero-return rate for old orders.
    The saved coverage is a purchase-date range, not a return-event-date range.
    """
    if not connector_ids or None in connector_ids:
        return False, "Connect sales and return requests for the same store to compare complete purchase periods."
    maturity = end + timedelta(days=follow_up_days)
    runs = db.all("sync_runs")
    for connection in db.all("store_connections"):
        last_sync = connection.get("last_sync")
        if isinstance(last_sync, dict):
            runs.append({**last_sync, "connector_id": connection["id"], "coverage": last_sync.get("coverage") or connection.get("coverage")})
    for connector_id in connector_ids:
        intervals = []
        for run in runs:
            if run.get("connector_id") != connector_id or run.get("status") in {"failed", "error"}:
                continue
            coverage = run.get("coverage") or {}
            synced_at, covered_start, covered_end = _date(run.get("at")), _date(coverage.get("start")), _date(coverage.get("end"))
            if not synced_at or not covered_start or not covered_end or not maturity <= synced_at <= timestamp:
                continue
            intervals.append((covered_start, covered_end))
        cursor = start
        for covered_start, covered_end in sorted(intervals):
            if covered_start > cursor:
                break
            cursor = max(cursor, covered_end)
            if cursor >= end:
                break
        if cursor < end:
            return False, "Update the store after the full follow-up period. Older purchase periods may need more sales history."
    return True, "The full purchase period was updated after its return follow-up ended."


def _cohort(db, product, start, end, follow_up_days, timestamp):
    """Return-request units for one fixed purchase cohort with equal observation time."""
    all_orders = {order["id"]: order for order in db.all("orders")}
    lines, order_ids, connectors, sources = {}, set(), set(), Counter()
    invalid_sales_lines = 0
    sold_units = 0
    if product.get("connector_id"):
        connectors.add(product["connector_id"])
    for order in all_orders.values():
        product_lines = [item for item in order.get("items", []) if item.get("product_id") == product.get("id")]
        if not product_lines:
            continue
        if order.get("status", "").lower() not in _SALE_STATUSES:
            # Unknown legacy statuses cannot establish eligible sales.
            if not order.get("status"):
                invalid_sales_lines += len(product_lines)
            continue
        purchased_at = _date(order.get("created_at"))
        if purchased_at is None:
            invalid_sales_lines += len(product_lines)
            continue
        if not start <= purchased_at < end or purchased_at > timestamp:
            continue
        for item in product_lines:
            key = (order["id"], item.get("id"))
            if not item.get("id") or not _quantity(item.get("quantity")) or key in lines:
                invalid_sales_lines += 1
                continue
            lines[key] = {**item, "purchased_at": purchased_at, "source": order.get("source"), "connector_id": order.get("connector_id")}
            sold_units += item["quantity"]
            if order["id"] not in order_ids:
                sources[order.get("source", "unknown")] += 1
            order_ids.add(order["id"])
            connectors.add(order.get("connector_id"))
    requested_by_line = Counter()
    request_ids = set()
    missing_request_ids = 0
    requested_units = unmatched = 0
    for record in db.all("returns"):
        if record.get("product_id") != product.get("id") or record.get("status", "").lower() not in _REQUEST_STATUSES or record.get("record_kind") == "refund_feedback":
            continue
        request_at = _date(record.get("returned_at"))
        if request_at and request_at > timestamp:
            continue
        order = all_orders.get(record.get("order_id"))
        purchased_at = _date(order.get("created_at")) if order else None
        if purchased_at:
            if not start <= purchased_at < end:
                continue
            if order.get("status", "").lower() not in _SALE_STATUSES:
                continue
            if request_at and request_at >= purchased_at + timedelta(days=follow_up_days):
                continue
        elif not request_at or start <= request_at < end + timedelta(days=follow_up_days):
            # No reliable purchase date means this request cannot be allocated to
            # either cohort. Suppress a potentially incomplete share.
            unmatched += 1
            continue
        else:
            continue
        key = (record.get("order_id"), record.get("order_item_id"))
        item = lines.get(key)
        quantity = record.get("quantity")
        matched = bool(item and item.get("variant_id") and item["variant_id"] == record.get("variant_id")
                       and item.get("source") and item["source"] == record.get("source")
                       and item.get("connector_id") == record.get("connector_id")
                       and _quantity(quantity) and request_at and request_at >= purchased_at)
        if not matched or requested_by_line[key] + quantity > item["quantity"]:
            unmatched += 1
            continue
        requested_by_line[key] += quantity
        requested_units += quantity
        if record.get("return_id"):
            request_ids.add((record["return_id"], record["order_id"]))
        else:
            missing_request_ids += 1
    mature = timestamp >= end + timedelta(days=follow_up_days)
    covered, coverage_note = _coverage(db, connectors, start, end, follow_up_days, timestamp)
    request_feed_available = product.get("source") != "woocommerce" and "woocommerce" not in sources
    if not request_feed_available:
        # WooCommerce's current connection supplies refunds, not a complete
        # return-request feed. An empty request collection cannot mean zero.
        covered = False
        coverage_note = "This store provides sales and refunds. Its return-request history is not available for this comparison."
    usable = bool(mature and covered and sold_units and not invalid_sales_lines and not unmatched)
    return {"from": start.isoformat(), "to": end.isoformat(), "sold_units": sold_units, "orders": len(order_ids),
            "matched_requested_units": requested_units,
            "request_share": round(requested_units / sold_units * 100, 1) if usable else None,
            "unmatched_requested_records": unmatched, "invalid_sales_lines": invalid_sales_lines,
            "return_request_count": len(request_ids) if request_feed_available and not missing_request_ids and not unmatched else None,
            "known_request_count": len(request_ids), "missing_request_id_records": missing_request_ids,
            "request_identity_complete": request_feed_available and not missing_request_ids and not unmatched,
            "coverage_complete": covered, "coverage_note": coverage_note, "source_counts": dict(sources),
            "follow_up_complete": mature}


def _results(db, cycle, timestamp):
    product = db.get("products", cycle["product_id"]) or {"id": cycle["product_id"]}
    window_days, follow_up_days = cycle.get("review_days", 14), cycle.get("follow_up_days", 14)
    applied = _date(cycle.get("applied_at"))
    anchor = applied or _date(cycle.get("created_at")) or timestamp
    before = _cohort(db, product, anchor - timedelta(days=window_days), anchor, follow_up_days, timestamp)
    after = _cohort(db, product, anchor, anchor + timedelta(days=window_days), follow_up_days, timestamp)
    eligible_at = anchor + timedelta(days=window_days + follow_up_days) if applied else None
    status, ready, delta, confidence = "planned", False, None, "unavailable"
    if applied:
        if timestamp < anchor + timedelta(days=window_days):
            status = "collecting"
        elif timestamp < eligible_at:
            status = "waiting"
        elif before["invalid_sales_lines"] or after["invalid_sales_lines"]:
            status = "coverage_gap"
        elif not before["sold_units"] or not after["sold_units"]:
            status = "missing_sales"
        elif not before["coverage_complete"] or not after["coverage_complete"]:
            status = "coverage_gap"
        elif before["unmatched_requested_records"] or after["unmatched_requested_records"]:
            status = "unmatched"
        elif min(before["sold_units"], after["sold_units"], before["orders"], after["orders"]) < 5:
            status = "insufficient"
        else:
            ready = True
            delta = round(after["request_share"] - before["request_share"], 1)
            status = "improving" if delta < 0 else "worsening" if delta > 0 else "steady"
            confidence = "directional" if min(before["orders"], after["orders"]) >= 20 else "early_signal"
    labels = {"planned": "Apply the change to start measuring", "collecting": "Collecting purchases after the change",
              "waiting": "Allow time for return requests to arrive", "missing_sales": "Matching sales are needed for both periods",
              "coverage_gap": "Complete sales and follow-up history is needed", "unmatched": "Some return requests need matching sales details",
              "insufficient": "More purchases are needed to compare results", "improving": "A lower share of sold items was requested back",
              "worsening": "A higher share of sold items was requested back", "steady": "Share of items requested back is unchanged"}
    steps = {"planned": "Verify the product facts, apply one change, and record its date.",
             "collecting": "Keep this change consistent while the purchase period completes.",
             "waiting": "Keep monitoring feedback, then update store data when the full follow-up period ends.",
             "missing_sales": "Connect and update the store so each period includes product sales and return requests.",
             "coverage_gap": "Update store data after follow-up. Check that both purchase periods and quantities are included.",
             "unmatched": "Check the order, variant and quantity for unmatched requests before judging the result.",
             "insufficient": "Treat these counts as a small sample and plan another review with a longer purchase period.",
             "improving": "Keep the change provisionally and repeat the review with fresh purchases.",
             "worsening": "Read the new complaints and check the change before planning a revised improvement.",
             "steady": "Review whether the change addressed the complaint, then choose the next useful improvement."}
    same_sources = set(before["source_counts"]) == set(after["source_counts"])
    next_step = steps[status]
    if status == "coverage_gap" and (product.get("source") == "woocommerce" or before["source_counts"].get("woocommerce") or after["source_counts"].get("woocommerce")):
        next_step = "Review refund feedback for product improvements. Return-request results need a separate, complete request history."
    synthetic = bool(product.get("source") == "synthetic" or before["source_counts"].get("synthetic") or after["source_counts"].get("synthetic"))
    request_count_change = after["return_request_count"] - before["return_request_count"] if ready and before["return_request_count"] is not None and after["return_request_count"] is not None else None
    return {"status": status, "label": labels[status], "note": "Each purchase has the same return follow-up. Item share and request count are different measures. This compares recorded return requests, not confirmed receipts, and does not prove the change caused the result. Later requests fall outside this comparison.",
            "next_step": next_step, "ready": ready, "eligible_at": eligible_at.isoformat() if eligible_at else None,
            "window_days": window_days, "follow_up_days": follow_up_days, "minimum_units": 5, "minimum_orders": 5,
            "recommended_units": 20, "recommended_orders": 20, "confidence": confidence, "change_pp": delta,
            "synthetic": synthetic, "before": before, "after": after,
            "request_count_change": request_count_change,
            "request_count_change_pct": round(request_count_change / before["return_request_count"] * 100, 1) if request_count_change is not None and before["return_request_count"] else None,
            "requested_units_change": after["matched_requested_units"] - before["matched_requested_units"] if ready else None,
            "sales_change_pct": round((after["sold_units"] - before["sold_units"]) / before["sold_units"] * 100, 1) if ready and same_sources else None}


def _assessment(cycle, after, timestamp):
    baseline = cycle["baseline"]
    before_count, after_count = baseline["feedback_count"], after["feedback_count"]
    applied = _date(cycle.get("applied_at"))
    full_period = bool(applied and timestamp >= applied + timedelta(days=cycle.get("review_days", 14)))
    ready = bool(full_period and before_count >= 5 and after_count >= 5 and baseline.get("issue_share") is not None and after.get("issue_share") is not None)
    delta = round(after["issue_share"] - baseline["issue_share"], 1) if ready else None
    status = "planned" if not cycle.get("applied_at") else "collecting" if not ready else "improving" if delta < 0 else "worsening" if delta > 0 else "steady"
    label = {"planned": "Change not applied yet", "collecting": "More feedback needed", "improving": "Issue mentioned less often", "worsening": "Issue mentioned more often", "steady": "Issue share is unchanged"}[status]
    synthetic = bool(baseline.get("source_counts", {}).get("synthetic") or after.get("source_counts", {}).get("synthetic"))
    return {
        "status": status, "label": label, "ready": ready, "change_pp": delta,
        "minimum_sample": 5, "synthetic": synthetic,
        "window_complete": full_period,
        "note": "This compares sample feedback only. It does not prove that a change caused an improvement or measure the product's return rate.",
    }


def _present(db, cycle, timestamp=None):
    timestamp = timestamp or _date(now())
    result = dict(cycle)
    product = db.get("products", cycle["product_id"]) or {}
    result["product_name"] = product.get("name", "Product unavailable")
    result["variant"] = product.get("variant", "")
    result["owner_id"] = cycle.get("owner_id") or cycle.get("created_by", "")
    member = db.get("members", result["owner_id"])
    result["owner_name"] = (member.get("name") if member else None) or cycle.get("owner_name") or "Review owner"
    result["owner_active"] = bool(member.get("active") and member.get("role") in {"owner", "admin", "analyst"}) if member else result["owner_id"] == "local-demo" or not db.all("members")
    result["follow_up_days"] = cycle.get("follow_up_days", 14)
    result["checkpoints"] = cycle.get("checkpoints", [])
    result["applied_note"] = cycle.get("applied_note", "")
    measurement_time = _date(cycle.get("completed_at")) if cycle.get("status") == "completed" else timestamp
    measurement_time = measurement_time or timestamp
    if cycle.get("status") == "completed" and cycle.get("completed_snapshot"):
        after = cycle["completed_snapshot"]
    elif cycle.get("applied_at"):
        applied = _date(cycle["applied_at"])
        after = _snapshot(db, cycle["product_id"], cycle["issue"], applied, min(measurement_time + timedelta(microseconds=1), applied + timedelta(days=cycle.get("review_days", 14))))
    else:
        after = {"from": None, "to": None, "feedback_count": 0, "issue_count": 0, "issue_share": None, "return_feedback_count": 0, "refund_feedback_count": 0, "received_count": 0, "unclassified_count": 0, "source_counts": {}, "evidence_ids": []}
    result["after"] = after
    result["assessment"] = _assessment(cycle, after, measurement_time)
    result["results"] = cycle.get("completed_results") or _results(db, cycle, measurement_time)
    target = _date(cycle.get("target_review_date"))
    result["review_due"] = bool(cycle["status"] == "active" and target and target <= timestamp)
    return result


def create_cycle(db, body: CycleCreate, uid):
    if not db.get("products", body.product_id):
        raise HTTPException(404, "Choose a product from this workspace.")
    if any(cycle["product_id"] == body.product_id and cycle["issue"] == body.issue and cycle["status"] in {"planned", "active"} for cycle in db.all("review_cycles")):
        raise HTTPException(409, "A review for this product and issue is already open.")
    timestamp = _date(now())
    owner_id, owner_name = _owner(db, body.owner_id, uid)
    cycle = {"id": uuid.uuid4().hex, **body.model_dump(), "owner_id": owner_id, "owner_name": owner_name, "checkpoints": [], "applied_note": "", "status": "planned", "created_at": timestamp.isoformat(), "created_by": uid, "applied_at": None, "applied_by": None, "completed_at": None, "completed_by": None, "outcome_note": "", "target_review_date": (timestamp + timedelta(days=body.review_days)).isoformat(), "baseline": _snapshot(db, body.product_id, body.issue, timestamp - timedelta(days=body.review_days), timestamp)}
    db.put("review_cycles", cycle["id"], cycle)
    return _present(db, cycle, timestamp)


def update_cycle(db, cycle_id, body: CycleUpdate, uid):
    cycle = db.get("review_cycles", cycle_id)
    if not cycle:
        raise HTTPException(404, "This product review was not found.")
    previous_version = cycle.get("version", 0)
    timestamp = _date(now())
    status = body.status or cycle["status"]
    if body.status is None and body.owner_id is None and not body.decision:
        raise HTTPException(422, "Choose a review decision or assign a review owner.")
    if body.owner_id is not None:
        owner_id, owner_name = _owner(db, body.owner_id, uid)
        cycle.update({"owner_id": owner_id, "owner_name": owner_name})
    if body.decision and (status != "active" or body.decision != "continue") and status != "completed":
        raise HTTPException(422, "Keep monitoring an active review, or complete it with a final decision.")
    if body.decision == "continue" and (cycle["status"] != "active" or status != "active"):
        raise HTTPException(409, "Apply the change before recording a follow-up check-in.")
    if body.decision == "continue" and len(body.outcome_note) < 5:
        raise HTTPException(422, "Add what you checked and learned before scheduling another check-in.")
    if cycle["status"] == "completed":
        if status != "completed":
            raise HTTPException(409, "Start a new review to test another change.")
        if body.applied_at and body.applied_at != cycle.get("applied_at"):
            raise HTTPException(409, "The date of a completed review cannot be changed.")
    if status == "planned" and cycle["status"] != "planned":
        raise HTTPException(409, "An applied change cannot be moved back to planning.")
    if status == "active" and cycle["status"] == "planned":
        applied = _date(body.applied_at) if body.applied_at else timestamp
        if not applied or applied > timestamp or applied < _date(cycle["created_at"]):
            raise HTTPException(422, "The change date must be between the review's creation and today.")
        cycle.update({"applied_at": applied.isoformat(), "applied_by": uid, "applied_note": body.applied_note,
                      "target_review_date": (applied + timedelta(days=cycle["review_days"])).isoformat(),
                      "baseline": _snapshot(db, cycle["product_id"], cycle["issue"], applied - timedelta(days=cycle["review_days"]), applied)})
    elif body.applied_at and body.applied_at != cycle.get("applied_at"):
        raise HTTPException(409, "The applied date is already saved. Start a new review for another change.")
    if status == "completed" and cycle["status"] == "planned":
        raise HTTPException(409, "Record that the change was applied before completing its review.")
    if status == "completed" and cycle["status"] != "completed":
        if not body.outcome_note.strip():
            raise HTTPException(422, "Add an outcome note before completing the review.")
        applied = _date(cycle["applied_at"])
        cycle.update({"completed_at": timestamp.isoformat(), "completed_by": uid,
                      "completed_snapshot": _snapshot(db, cycle["product_id"], cycle["issue"], applied, min(timestamp + timedelta(microseconds=1), applied + timedelta(days=cycle["review_days"]))),
                      "completed_results": _results(db, cycle, timestamp), "final_decision": body.decision or "keep"})
        cycle.setdefault("checkpoints", []).append({"at": timestamp.isoformat(), "by": uid, "decision": body.decision or "keep", "note": body.outcome_note})
    if body.decision == "continue":
        next_check = timestamp + timedelta(days=body.next_check_in_days)
        cycle["target_review_date"] = next_check.isoformat()
        cycle.setdefault("checkpoints", []).append({"at": timestamp.isoformat(), "by": uid, "decision": "continue", "note": body.outcome_note, "next_check_in_date": next_check.isoformat()})
    updates = {"status": status, "updated_at": timestamp.isoformat(), "version": previous_version + 1}
    if "outcome_note" in body.model_fields_set:
        updates["outcome_note"] = body.outcome_note
    cycle.update(updates)
    saved = db.claim("review_cycles", cycle_id, lambda latest: latest.get("version", 0) == previous_version, cycle)
    if not saved:
        raise HTTPException(409, "This review was updated by a teammate. Refresh and try again.")
    return _present(db, cycle, timestamp)


def list_cycles(db):
    return [_present(db, cycle) for cycle in sorted(db.all("review_cycles"), key=lambda item: item["created_at"], reverse=True)]


def _product_metrics(db):
    sold = Counter()
    lines = {}
    for order in db.all("orders"):
        if order.get("status", "").lower() in {"cancelled", "canceled", "pending", "failed", "draft", "on-hold"}:
            continue
        for item in order.get("items", []):
            quantity = item.get("quantity")
            if not _quantity(quantity) or not item.get("id"):
                continue
            sold[item["product_id"]] += quantity
            lines[(order["id"], item["id"])] = {**item, "source": order.get("source"), "connector_id": order.get("connector_id")}
    received = Counter()
    requested = Counter()
    unmatched = Counter()
    unmatched_requested = Counter()
    received_units = Counter()
    requested_units = Counter()
    for record in db.all("returns"):
        status = record.get("status", "").lower()
        if status not in {"requested", "open", "closed", "received"} or record.get("record_kind") == "refund_feedback":
            continue
        key = (record.get("order_id"), record.get("order_item_id"))
        item = lines.get(key)
        quantity = record.get("quantity")
        confirmed_received = status == "received" and record.get("physical_return_confirmed") is not False
        matched = bool(item and item["product_id"] == record.get("product_id") and item.get("variant_id") and item["variant_id"] == record.get("variant_id") and item.get("source") and item["source"] == record.get("source") and item.get("connector_id") == record.get("connector_id") and _quantity(quantity))
        if not matched or requested_units[key] + quantity > item["quantity"]:
            unmatched_requested[record["product_id"]] += 1
            if confirmed_received:
                unmatched[record["product_id"]] += 1
            continue
        requested_units[key] += quantity
        requested[record["product_id"]] += quantity
        if confirmed_received:
            if received_units[key] + quantity > item["quantity"]:
                unmatched[record["product_id"]] += 1
                continue
            received_units[key] += quantity
            received[record["product_id"]] += quantity
    refunded = Counter()
    for refund in db.all("refunds"):
        for item in refund.get("items", []):
            quantity = item.get("quantity", 0)
            if _quantity(quantity):
                refunded[item["product_id"]] += quantity
    return [{"product_id": product["id"], "product_name": product["name"], "source": product.get("source"), "sold_units": sold[product["id"]], "refunded_units": refunded[product["id"]], "matched_requested_units": requested[product["id"]], "request_share": round(requested[product["id"]] / sold[product["id"]] * 100, 1) if sold[product["id"]] and requested[product["id"]] and not unmatched_requested[product["id"]] else None, "unmatched_requested_records": unmatched_requested[product["id"]], "request_note": "Return requests as a share of recorded sales only. Later requests may change this share; a request does not confirm physical receipt.", "matched_received_units": received[product["id"]], "received_share": round(received[product["id"]] / sold[product["id"]] * 100, 1) if sold[product["id"]] and received[product["id"]] and not unmatched[product["id"]] else None, "unmatched_received_records": unmatched[product["id"]], "rate_note": "Only explicitly received returns linked to the exact sold item are counted. Refunds are shown separately; later returns may change this share."} for product in db.all("products")]


def review_summary(db):
    cycles = list_cycles(db)
    return {"cycles": cycles, "assignees": _assignees(db), "counts": {"planned": sum(cycle["status"] == "planned" for cycle in cycles), "active": sum(cycle["status"] == "active" for cycle in cycles), "completed": sum(cycle["status"] == "completed" for cycle in cycles), "due": sum(cycle["review_due"] for cycle in cycles)}, "products": _product_metrics(db)}
