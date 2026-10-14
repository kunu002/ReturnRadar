"""Existing merchant data and decisions survive explicit sample provisioning."""
from datetime import datetime, timedelta

import pytest

from backend.analysis import dashboard
from backend.product_actions import public_check_ready
from backend.reviews import review_summary
from backend.store import LocalStore, WorkspaceStore
from backend.workspace_samples import COLLECTIONS, FIXTURE_ID, PREFIX, seed_workspace_samples


@pytest.fixture
def sample_workspace(tmp_path):
    root = LocalStore(str(tmp_path / "workspace.db"))
    root.put("workspaces", "ks", {"id": "ks", "name": "ks"})
    db = WorkspaceStore(root, "ks")
    db.put("members", "owner", {"id": "owner", "role": "owner", "active": True, "name": "Merchant"})
    return root, db


def test_dry_run_is_write_free_and_reports_varied_fixtures(sample_workspace):
    root, db = sample_workspace
    planned = seed_workspace_samples(root, "ks", "owner")
    assert not planned["applied"]
    assert planned["totals"] == {"products": 18, "snapshots": 10, "orders": 1336,
                                  "returns": 256, "actions": 30, "review_cycles": 3}
    assert planned["missing"] == planned["totals"]
    assert not db.all("meta")
    assert all(not db.all(collection) for collection in COLLECTIONS)


def test_samples_have_fresh_dates_links_drafts_and_no_ai_receipt(sample_workspace, monkeypatch):
    root, db = sample_workspace
    monkeypatch.setenv("GEMINI_API_KEY", "configured-but-must-never-be-used")
    result = seed_workspace_samples(root, "ks", "owner", apply=True)
    assert result["created"] == result["totals"]
    products = {product["id"]: product for product in db.all("products")}
    assert {product["category"] for product in products.values()} == {
        "Bags", "Clothing", "Footwear", "Electronics", "Kitchen", "Home", "Beauty", "Fitness", "Toys"}
    assert all(product["currency"] == "INR" for product in products.values())
    orders = {order["id"]: order for order in db.all("orders")}
    anchor = datetime.fromisoformat(result["anchor"])
    for record in db.all("returns"):
        assert record["source"] == "synthetic" and record["sample_fixture"] == FIXTURE_ID
        assert record["id"].startswith(PREFIX)
        returned_at = datetime.fromisoformat(record["returned_at"])
        assert anchor - timedelta(days=28) <= returned_at <= anchor
        assert record["product_id"] in products
        order = orders[record["order_id"]]
        assert order["status"] == "completed" and datetime.fromisoformat(order["created_at"]) <= returned_at
        item = next(item for item in order["items"] if item["id"] == record["order_item_id"])
        assert item["product_id"] == record["product_id"]
        assert item["variant_id"] == record["variant_id"] and item["quantity"] == record["quantity"] == 1
    for action in db.all("actions"):
        assert action["status"] == "draft" and not public_check_ready(action)
        assert action["product_id"] in products
    assert not db.all("analysis_reports") and not db.all("gemini_receipts")
    assert not db.get("meta", "analysis")
    assert db.get("meta", FIXTURE_ID)["status"] == "completed"
    view = dashboard(db)
    assert view["counts"]["pending"] == 30
    assert len(view["patterns"]) >= 12
    reviews = review_summary(db)
    assert reviews["counts"]["planned"] == 3
    assert all(cycle["results"]["synthetic"] and cycle["results"]["status"] == "planned" for cycle in reviews["cycles"])
    assert all(not cycle["results"]["ready"] for cycle in reviews["cycles"])


def test_existing_real_data_all_namespaces_and_ai_history_preserved(sample_workspace):
    root, db = sample_workspace
    real = {"id": "p1", "name": "Actual Shopify product", "source": "shopify"}
    db.put("products", "p1", real)
    db.put("actions", "fit-p7", {"id": "fit-p7", "status": "dismissed"})
    analysis = {"id": "analysis", "mode": "Gemini", "receipt": "existing"}
    db.put("meta", "analysis", analysis)
    elsewhere = WorkspaceStore(root, "another")
    elsewhere.put("products", "p1", {"id": "p1", "private": True})
    memberships = db.all("members")
    seed_workspace_samples(root, "ks", "owner", apply=True)
    assert db.get("products", "p1") == real
    assert db.get("actions", "fit-p7")["status"] == "dismissed"
    assert db.get("meta", "analysis") == analysis
    assert db.all("members") == memberships
    assert elsewhere.all("products") == [{"id": "p1", "private": True}]


def test_repeat_preserves_dates_and_merchant_decisions(sample_workspace):
    root, db = sample_workspace
    first = seed_workspace_samples(root, "ks", "owner", apply=True)
    action = db.get("actions", PREFIX + "buyer-guide-demo-sneakers")
    action.update(status="dismissed", text="Merchant updated this draft.")
    db.put("actions", action["id"], action)
    before = {collection: db.all(collection) for collection in COLLECTIONS}
    second = seed_workspace_samples(root, "ks", "owner", apply=True)
    assert second["anchor"] == first["anchor"]
    assert all(count == 0 for count in second["created"].values())
    assert {collection: db.all(collection) for collection in COLLECTIONS} == before


@pytest.mark.parametrize("invalid", ["missing_workspace", "global_demo", "missing_member", "inactive", "viewer"])
def test_only_an_existing_active_editor_namespace_can_be_seeded(sample_workspace, invalid):
    root, db = sample_workspace
    workspace_id, owner_id = "ks", "owner"
    if invalid == "missing_workspace":
        workspace_id = "nonexistent"
    elif invalid == "global_demo":
        workspace_id = "demo"
    elif invalid == "missing_member":
        owner_id = "different"
    else:
        db.put("members", "owner", {"id": "owner", "role": "viewer" if invalid == "viewer" else "owner",
                                    "active": invalid != "inactive"})
    with pytest.raises(ValueError):
        seed_workspace_samples(root, workspace_id, owner_id, apply=True)
    assert all(not db.all(collection) for collection in COLLECTIONS)
    assert not db.all("meta")


def test_conflicting_fixture_ids_fail_before_any_write(sample_workspace):
    root, db = sample_workspace
    previous = {"id": PREFIX + "p1", "name": "Real conflicting product", "source": "shopify"}
    db.put("products", previous["id"], previous)
    with pytest.raises(ValueError, match="conflicts"):
        seed_workspace_samples(root, "ks", "owner", apply=True)
    assert db.all("products") == [previous]
    assert not db.all("meta") and not db.all("returns")


def test_partial_run_recovers_using_saved_dates_without_changing_existing_products(sample_workspace, monkeypatch):
    from backend import workspace_samples
    root, db = sample_workspace
    original = workspace_samples._create_missing

    def fail_after_products(store, collection, values):
        if collection == "snapshots":
            raise RuntimeError("Temporary database failure")
        return original(store, collection, values)

    monkeypatch.setattr(workspace_samples, "_create_missing", fail_after_products)
    with pytest.raises(RuntimeError, match="Temporary"):
        seed_workspace_samples(root, "ks", "owner", apply=True)
    manifest = db.get("meta", FIXTURE_ID)
    assert manifest["status"] == "pending" and len(db.all("products")) == 18
    products = db.all("products")
    monkeypatch.setattr(workspace_samples, "_create_missing", original)
    recovered = seed_workspace_samples(root, "ks", "owner", apply=True)
    assert recovered["anchor"] == manifest["at"]
    assert recovered["created"]["products"] == 0
    assert db.all("products") == products
    assert len(db.all("returns")) == 256
    assert db.get("meta", FIXTURE_ID)["status"] == "completed"


@pytest.mark.parametrize("concurrent", [False, True])
def test_firestore_batches_use_create_and_preserve_a_concurrent_decision(sample_workspace, concurrent):
    from google.api_core.exceptions import AlreadyExists
    from backend.workspace_samples import _create_missing
    local_root, _ = sample_workspace
    committed = []

    class FakeBatch:
        def __init__(self):
            self.creates = []

        def create(self, reference, value):
            self.creates.append((reference, value))

        def commit(self):
            committed.append(self.creates)
            if concurrent and len(committed) == 1:
                (collection, key), value = self.creates[0]
                local_root.put(collection, key, {**value, "status": "approved"})
            if any(local_root.get(collection, key) for (collection, key), _ in self.creates):
                raise AlreadyExists("Concurrent record")
            for (collection, key), value in self.creates:
                local_root.create(collection, key, value)

    class FakeFirestore:
        db = None

        def __init__(self):
            self.db = self

        def batch(self):
            return FakeBatch()

        def ref(self, collection, key):
            return collection, key

        def create(self, collection, key, value):
            return local_root.create(collection, key, value)

    scoped = WorkspaceStore(FakeFirestore(), "ks")
    values = [{"id": PREFIX + str(number), "status": "draft"} for number in range(205)]
    assert _create_missing(scoped, "actions", values) == (204 if concurrent else 205)
    assert [len(batch) for batch in committed] == [200, 5]
    assert all(reference[0] == scoped.prefix + "actions" for batch in committed for reference, _ in batch)
    first = local_root.get(scoped.prefix + "actions", values[0]["id"])
    assert first["status"] == ("approved" if concurrent else "draft")
