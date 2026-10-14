"""Explicit, create-only UI fixtures for one existing merchant workspace.

These fixtures are provisioned only by an explicit sample-data choice or an
operator's seed command. They never alter membership or copy local merchant
data or credentials, and remain labelled as synthetic.
"""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from .product_actions import buyer_guide, product_category
from .reviews import _snapshot
from .seed import seed
from .store import WorkspaceStore, now
from .variety import CATALOG, seed_sales_sample, seed_variety


FIXTURE_ID = "merchant-ui-samples-v1"
PREFIX = "rr-sample-v1-"
COLLECTIONS = ("products", "snapshots", "orders", "returns", "actions", "review_cycles")
ID_FIELDS = {"id", "product_id", "return_id", "order_id", "order_item_id", "variant_id", "listing_snapshot_id"}
DATE_FIELDS = {"at", "captured_at", "created_at", "fulfilled_at", "returned_at", "approved_at"}


class _MemoryStore:
    """Generate fixtures without touching a database or calling an AI provider."""
    def __init__(self):
        self.collections = {}

    def all(self, collection):
        return [deepcopy(value) for value in self.collections.get(collection, {}).values()]

    def get(self, collection, key):
        return deepcopy(self.collections.get(collection, {}).get(key))

    def put(self, collection, key, value):
        self.collections.setdefault(collection, {})[key] = deepcopy(value)

    def create(self, collection, key, value):
        if self.get(collection, key) is not None:
            return False
        self.put(collection, key, value)
        return True


def _date(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _transform(value, anchor, generated_at):
    if isinstance(value, list):
        return [_transform(item, anchor, generated_at) for item in value]
    if not isinstance(value, dict):
        return value
    transformed = {}
    for field, item in value.items():
        if field in ID_FIELDS and isinstance(item, str):
            transformed[field] = PREFIX + item
        elif field == "evidence_ids":
            transformed[field] = [PREFIX + key for key in item]
        elif field in DATE_FIELDS and item:
            transformed[field] = (anchor + (_date(item) - generated_at)).isoformat()
        else:
            transformed[field] = _transform(item, anchor, generated_at)
    return transformed


def _fixtures(anchor, owner):
    memory = _MemoryStore()
    seed(memory)
    for product in memory.all("products"):
        memory.put("products", product["id"], {**product, "category": "Bags"})
    seed_variety(memory)
    seed_sales_sample(memory)
    generated_at = _date(memory.get("meta", "initialized")["at"])
    prepared = _MemoryStore()
    for collection in COLLECTIONS:
        for record in memory.all(collection):
            value = _transform(record, anchor, generated_at)
            value.update(source="synthetic", synthetic=True, sample_fixture=FIXTURE_ID)
            if collection == "products":
                value.update(category=product_category(value), currency="INR")
                value["sku"] = "SAMPLE-" + value["sku"] if not value["sku"].startswith("SAMPLE-") else value["sku"]
            elif collection == "returns":
                value.setdefault("quantity", 1)
            elif collection == "orders":
                value.setdefault("created_at", value.get("fulfilled_at"))
                value.setdefault("status", "completed")
                for item in value["items"]:
                    item.setdefault("currency", "INR")
                    original_product = memory.get("products", item["product_id"][len(PREFIX):])
                    item.setdefault("net_total", str((original_product or {}).get("price") or 0))
            elif collection == "actions":
                # Legacy local fixtures include an approved dimension example.
                # Explicit workspace seeding must never publish invented facts.
                value.update(status="draft", measurements_verified=False, guidance_verified=False)
                value.pop("approved_at", None)
            prepared.put(collection, value["id"], value)
    records = prepared.all("returns")
    for product in prepared.all("products"):
        evidence = [record for record in records if record["product_id"] == product["id"]]
        issue = Counter(record["issue"] for record in evidence).most_common(1)[0][0]
        key = PREFIX + "buyer-guide-" + product["id"][len(PREFIX):]
        prepared.put("actions", key, {"id": key, "product_id": product["id"],
                     **buyer_guide(product), "status": "draft", "source": "synthetic", "synthetic": True,
                     "sample_fixture": FIXTURE_ID, "issue": issue,
                     "evidence_ids": [record["id"] for record in evidence]})
    # These plans let merchants assess the review workflow without inventing
    # an applied intervention, a completed result or historical sync coverage.
    for original_id, goal in (("demo-sneakers", "Reduce sizing-related return requests."),
                              ("demo-mug", "Reduce complaints about lid leakage."),
                              ("demo-toy", "Reduce return requests caused by missing pieces.")):
        product_id = PREFIX + original_id
        category = next(item for item in CATALOG if item[0] == original_id)
        key = PREFIX + "review-" + original_id
        prepared.put("review_cycles", key, {"id": key, "product_id": product_id,
                     "issue": category[6], "goal": goal, "change_plan": category[8],
                     "review_days": 14, "follow_up_days": 14, "status": "planned",
                     "source": "synthetic", "synthetic": True, "sample_fixture": FIXTURE_ID,
                     "created_at": anchor.isoformat(), "created_by": owner["id"],
                     "owner_id": owner["id"], "owner_name": owner.get("name") or "Review owner",
                     "applied_at": None, "applied_by": None, "applied_note": "",
                     "completed_at": None, "completed_by": None, "outcome_note": "", "checkpoints": [],
                     "target_review_date": (anchor + timedelta(days=14)).isoformat(),
                     "baseline": _snapshot(prepared, product_id, category[6], anchor - timedelta(days=14), anchor)})
    return {collection: prepared.all(collection) for collection in COLLECTIONS}


def _create_missing(db, collection, values):
    """Use Firestore create preconditions; a concurrent decision is preserved."""
    if not values:
        return 0
    if not hasattr(db.root, "db") or not hasattr(db.root, "ref"):
        return sum(db.create(collection, value["id"], value) for value in values)
    from google.api_core.exceptions import AlreadyExists
    created = 0
    for offset in range(0, len(values), 200):
        chunk = values[offset:offset + 200]
        batch = db.root.db.batch()
        for value in chunk:
            batch.create(db.root.ref(db.prefix + collection, value["id"]), value)
        try:
            batch.commit()
            created += len(chunk)
        except AlreadyExists:
            # A record appeared after planning. Create only the remaining records.
            created += sum(db.create(collection, value["id"], value) for value in chunk)
    return created


def seed_workspace_samples(root, workspace_id, owner_id, *, apply=False):
    """Add fixed, explicitly synthetic samples to an authorized existing namespace.

    The caller must first authorize the intended workspace and owner; ordinary
    merchant workspaces require an explicit operator choice, while onboarding
    may seed only the signed-in account's separate private sample workspace.
    ``apply=False`` returns a preview and performs no writes. Reruns preserve all
    records, decisions and dates, including during recovery from a partial run.
    """
    if not workspace_id or workspace_id == "demo" or not root.get("workspaces", workspace_id):
        raise ValueError("Choose an existing merchant workspace; the global demo cannot be seeded here.")
    db = WorkspaceStore(root, workspace_id)
    owner = db.get("members", owner_id)
    if not owner or not owner.get("active") or owner.get("role") not in {"owner", "admin"}:
        raise ValueError("The selected user must be an active Owner or Admin in this workspace.")
    manifest = db.get("meta", FIXTURE_ID)
    if manifest and (manifest.get("sample_fixture") != FIXTURE_ID or not manifest.get("synthetic")):
        raise ValueError("The sample manifest conflicts with another record. No data was changed.")
    anchor = _date(manifest["at"] if manifest else now())
    fixtures = _fixtures(anchor, owner)
    missing = {}
    for collection, values in fixtures.items():
        existing = {value["id"]: value for value in db.all(collection)}
        for value in values:
            previous = existing.get(value["id"])
            if previous and (previous.get("source") != "synthetic" or previous.get("sample_fixture") != FIXTURE_ID):
                raise ValueError("A sample ID conflicts with an existing record. No data was changed.")
        missing[collection] = [value for value in values if value["id"] not in existing]
    result = {"workspace_id": workspace_id, "synthetic": True, "applied": apply,
              "fixture": FIXTURE_ID, "anchor": anchor.isoformat(),
              "totals": {collection: len(values) for collection, values in fixtures.items()},
              "missing": {collection: len(values) for collection, values in missing.items()}}
    if not apply:
        return result
    if not manifest:
        pending = {"id": FIXTURE_ID, "sample_fixture": FIXTURE_ID, "synthetic": True,
                   "at": anchor.isoformat(), "created_by": owner_id, "status": "pending"}
        if not db.create("meta", FIXTURE_ID, pending):
            raise ValueError("Sample provisioning started concurrently. Run again to reuse its dates.")
    result["created"] = {collection: _create_missing(db, collection, values) for collection, values in missing.items()}
    db.claim("meta", FIXTURE_ID, lambda value: value.get("status") == "pending",
             {"status": "completed", "totals": result["totals"], "completed_at": now()})
    return result
