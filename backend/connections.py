"""Workspace-scoped store coverage and import history."""
from collections import Counter
from .connectors import public_connections


def connection_summary(db):
    records=db.all("returns")
    imports=sorted(db.all("import_runs"),key=lambda run:run["at"],reverse=True)[:20]
    return {
        "connectors":[
            {"id":"csv","name":"Return CSV","status":"available","method":"manual_import"},
            {"id":"shopify","name":"Shopify","status":"available","method":"store_connection"},
            {"id":"woocommerce","name":"WooCommerce","status":"available","method":"store_connection"},
        ],
        "stores":public_connections(db),
        "coverage":{"returns":{"records":len(records),"refund_feedback":sum(r.get("record_kind")=="refund_feedback" for r in records)},
                    "sales":{"status":"available" if db.all("orders") else "not_imported","orders":len(db.all("orders"))},
                    "refunds":{"records":len(db.all("refunds"))}},
        "source_counts":dict(Counter(r["source"] for r in records)),
        "platform_counts":dict(Counter(r.get("source_platform","generic") for r in records if r["source"]=="csv")),
        "imports":imports,
        "updates":sorted(db.all("sync_runs"),key=lambda run:run["at"],reverse=True)[:20],
        "active_updates":[{"id":event["id"],"connector_id":event.get("connector_id"),"status":event["status"]}
                          for event in db.all("events") if event["kind"]=="connector_sync" and event["status"] in ("pending","queued","processing")],
    }
