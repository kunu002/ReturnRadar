"""Read-only live adapters, credential isolation, SSRF controls, and matching item IDs."""
import json
import os
import socket
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import time

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from backend import connectors
from backend.store import LocalStore, WorkspaceStore
from test_workflows import workspace


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CONNECTOR_KEY_PATH", str(tmp_path / "connector.key"))
    monkeypatch.delenv("CONNECTOR_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.delenv("STORAGE_BACKEND", raising=False)
    return WorkspaceStore(LocalStore(str(tmp_path / "test.db")), "merchant-one")


def shopify_input():
    return connectors.ConnectorInput(platform="shopify", url="merchant.myshopify.com", access_token="private-shopify-token")


def woo_input():
    return connectors.ConnectorInput(platform="woocommerce", url="https://shop.example.com/store", consumer_key="ck_private-key", consumer_secret="cs_private-secret")


@pytest.mark.parametrize("platform,url", [
    ("woocommerce", "http://shop.example.com"), ("woocommerce", "https://user:password@shop.example.com"),
    ("woocommerce", "https://shop.example.com:8443"), ("woocommerce", "https://shop.example.com/?next=http://localhost"),
    ("woocommerce", "https://127.0.0.1"), ("woocommerce", "https://localhost"),
    ("woocommerce", "https://shop.example.com/%2e%2e"), ("woocommerce", "https://shop.example.com/../admin"),
    ("shopify", "https://merchant.myshopify.com.evil.example"), ("shopify", "https://merchant.myshopify.com/admin"),
])
def test_connection_input_rejects_untrusted_urls(platform, url):
    with pytest.raises(ValidationError):
        connectors.ConnectorInput(platform=platform, url=url, access_token="token", consumer_key="ck_key", consumer_secret="cs_secret")


@pytest.mark.parametrize("addresses", [["127.0.0.1"], ["10.0.0.1"], ["169.254.169.254"], ["::1"], ["fc00::1"], ["8.8.8.8", "192.168.1.1"]])
def test_dns_rejects_private_addresses_and_mixed_answers(monkeypatch, addresses):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443)) for address in addresses])
    with pytest.raises(connectors.ConnectionError):
        connectors.public_addresses("shop.example.com")


def test_requests_pin_checked_public_address_preserve_tls_and_deny_redirects(monkeypatch):
    monkeypatch.setattr(connectors, "public_addresses", lambda host: ["8.8.8.8"])
    original = httpx.Client
    seen = []
    status = [200]
    def handle(request):
        seen.append(request)
        return httpx.Response(status[0], json={"ok": True}, headers={"Location": "http://169.254.169.254/latest/meta-data"})
    monkeypatch.setattr(connectors.httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    result, _ = connectors.secure_request("GET", "https://shop.example.com/wp-json/wc/v3/orders", auth=("ck_hidden", "cs_hidden"))
    assert result == {"ok": True}
    assert seen[0].url.host == "8.8.8.8"
    assert seen[0].headers["host"] == "shop.example.com"
    assert seen[0].extensions["sni_hostname"] == "shop.example.com"
    assert "ck_hidden" not in str(seen[0].url)
    status[0] = 302
    with pytest.raises(connectors.ConnectionError, match="redirected"):
        connectors.secure_request("GET", "https://shop.example.com/wp-json/wc/v3/orders")
    assert len(seen) == 2  # The redirect destination is never contacted.


@pytest.mark.parametrize("platform,status,message", [
    ("shopify", 401, "may be expired"),
    ("shopify", 403, "store's access restrictions"),
    ("woocommerce", 401, "consumer key and consumer secret"),
    ("woocommerce", 403, "connected account can read products and orders"),
])
def test_denied_requests_distinguish_invalid_credentials_from_refused_access_without_leaking_secrets(monkeypatch, caplog, platform, status, message):
    monkeypatch.setattr(connectors, "public_addresses", lambda host: ["8.8.8.8"])
    original = httpx.Client
    secret = "private-credential-never-log"
    def handle(request):
        assert request.url.host == "8.8.8.8"
        return httpx.Response(status, json={"errors": secret}, headers={"X-Request-ID": secret})
    monkeypatch.setattr(connectors.httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    url = ("https://merchant.myshopify.com/admin/api/2026-07/graphql.json" if platform == "shopify"
           else "https://shop.example.com/wp-json/wc/v3/orders")
    with pytest.raises(connectors.ConnectionError, match=message) as error:
        connectors.secure_request("POST", url, headers={"X-Shopify-Access-Token": secret}, auth=(secret, secret), payload={"secret": secret})
    assert error.value.status_code == status
    assert secret not in str(error.value)
    assert secret not in caplog.text
    assert f"platform={platform} operation=data status={status} request_id=unavailable" in caplog.text
    assert "merchant.myshopify.com" not in caplog.text and "shop.example.com" not in caplog.text


def test_denied_request_logs_only_a_bounded_shopify_request_identifier(monkeypatch, caplog):
    monkeypatch.setattr(connectors, "public_addresses", lambda host: ["8.8.8.8"])
    original = httpx.Client
    request_id = "12345678-1234-abcd-ef00-123456789abc"
    monkeypatch.setattr(connectors.httpx, "Client", lambda **kwargs: original(
        transport=httpx.MockTransport(lambda request: httpx.Response(401, json={"error": "private upstream body"}, headers={"X-Request-ID": request_id})), **kwargs))
    with pytest.raises(connectors.ConnectionError):
        connectors.secure_request("POST", "https://merchant.myshopify.com/admin/oauth/access_token", form_data={"client_secret": "private-secret"})
    assert f"operation=authorization status=401 request_id={request_id}" in caplog.text
    assert "private" not in caplog.text


def test_credentials_are_encrypted_and_bound_to_workspace(db, monkeypatch):
    monkeypatch.setattr(connectors, "verify_connection", lambda body: {"name": "Merchant Store"})
    body = shopify_input()
    public = connectors.save_connection(db, body)
    stored = db.get("store_connections", public["id"])
    assert body.access_token not in json.dumps(stored)
    assert "credentials" not in public
    assert public["shopify_auth"] == "access_token"
    assert "credentials" not in json.dumps(connectors.public_connections(db))
    assert os.stat(os.environ["CONNECTOR_KEY_PATH"]).st_mode & 0o777 == 0o600
    assert connectors._credentials(db, stored).access_token == body.access_token
    other = WorkspaceStore(db.root, "other-merchant")
    assert not connectors.public_connections(other)
    with pytest.raises(connectors.ConnectionError, match="Reconnect"):
        connectors._credentials(other, stored)
    connectors.disconnect_connection(db, public["id"])
    assert db.get("store_connections", public["id"])["credentials"] is None
    assert not connectors.public_connections(db)
    with pytest.raises(connectors.ConnectionError, match="Reconnect"):
        connectors.sync_connection(db, public["id"])


def test_production_requires_persistent_encryption_key(db, monkeypatch):
    monkeypatch.setenv("K_SERVICE", "returnradar")
    with pytest.raises(connectors.ConnectionError):
        connectors._cipher()
    monkeypatch.setenv("CONNECTOR_ENCRYPTION_KEY", Fernet.generate_key().decode())
    assert connectors._cipher().decrypt(connectors._cipher().encrypt(b"private")) == b"private"


def page(nodes, next_page=False, cursor=None):
    return {"nodes": nodes, "pageInfo": {"hasNextPage": next_page, "endCursor": cursor}}


def mock_shopify(monkeypatch, *, fail_orders=False):
    timestamp = datetime.now(timezone.utc).isoformat()
    product_id, variant_id, line_id = "gid://shopify/Product/1", "gid://shopify/ProductVariant/1", "gid://shopify/LineItem/1"
    variant = {"id": variant_id, "title": "Green", "sku": "GREEN", "price": "50.00"}
    product = {"id": product_id, "title": "Everyday Tee", "description": "Cotton shirt in green.", "featuredImage": None, "variants": page([variant], True, "variant-page")}
    line = {"id": line_id, "quantity": 3, "currentQuantity": 2, "variant": {"id": variant_id, "product": {"id": product_id}}, "discountedTotalSet": {"shopMoney": {"amount": "150", "currencyCode": "USD"}}}
    return_line = {"id": "gid://shopify/ReturnLineItem/1", "quantity": 1, "returnReason": "SIZE_TOO_SMALL", "returnReasonNote": "Sleeves too tight", "fulfillmentLineItem": {"lineItem": line}}
    return_record = {"id": "gid://shopify/Return/1", "status": "CLOSED", "createdAt": timestamp, "returnLineItems": page([return_line])}
    order = {"id": "gid://shopify/Order/1", "createdAt": timestamp, "cancelledAt": None, "displayFinancialStatus": "PARTIALLY_REFUNDED", "displayFulfillmentStatus": "FULFILLED", "currencyCode": "USD", "lineItems": page([line]), "returns": page([return_record])}
    calls = []
    def graphql(body, query, variables=None):
        calls.append((body, query, variables))
        assert "mutation" not in query.lower()
        if "ConnectionCheck" in query:
            return {"shop": {"name": "Clothing Store", "myshopifyDomain": "merchant.myshopify.com"}, "currentAppInstallation": {"accessScopes": [{"handle": "read_"+area} for area in ("products", "orders", "returns")]}}
        if "StoreProducts" in query:
            return {"products": page([product])}
        if "StoreVariants" in query:
            assert variables["cursor"] == "variant-page"
            return {"product": {"variants": page([{**variant, "id": "gid://shopify/ProductVariant/2", "title": "Blue"}])}}
        if "StoreOrders" in query:
            if fail_orders:
                raise connectors.ConnectionError("Store update temporarily unavailable.")
            assert "created_at:>=" in variables["filter"]
            return {"orders": page([order])}
        raise AssertionError(query)
    monkeypatch.setattr(connectors, "_graphql", graphql)
    return calls, timestamp


def test_shopify_sync_uses_each_merchants_token_exact_item_links_and_stable_ids(db, monkeypatch):
    calls, timestamp = mock_shopify(monkeypatch)
    connection = connectors.save_connection(db, shopify_input())
    summary = connectors.sync_connection(db, connection["id"])
    assert summary["products"] == summary["orders"] == summary["returns"] == 1
    assert summary["coverage"]["days"] == 60
    product, order, returned = db.all("products")[0], db.all("orders")[0], db.all("returns")[0]
    assert len(product["variants"]) == 2
    assert returned["order_id"] == order["id"] and returned["order_item_id"] == order["items"][0]["id"]
    assert returned["product_id"] == product["id"] and returned["quantity"] == 1
    assert order["items"][0]["quantity"] == 3
    assert returned["returned_at"] == timestamp and returned["status"] == "closed"
    assert "request date" in returned["date_basis"].lower()
    assert all(call[0].access_token == "private-shopify-token" for call in calls)
    connectors.sync_connection(db, connection["id"])
    assert len(db.all("returns")) == len(db.all("orders")) == len(db.all("products")) == 1
    assert len(db.all("sync_runs")) == 2
    assert len(db.all("snapshots")) == 1
    assert db.get("store_connections", connection["id"])["last_sync"]["returns"] == 1


def test_failed_pagination_does_not_import_partial_store_data(db, monkeypatch):
    mock_shopify(monkeypatch, fail_orders=True)
    connection = connectors.save_connection(db, shopify_input())
    with pytest.raises(connectors.ConnectionError):
        connectors.sync_connection(db, connection["id"])
    assert not db.all("products") and not db.all("orders") and not db.all("returns")
    assert not db.all("sync_runs")
    assert db.get("store_connections", connection["id"])["status"] == "attention"
    assert db.get("store_connections", connection["id"])["sync_lease"] is None


def test_shopify_read_scopes_are_checked_before_saving(db, monkeypatch):
    monkeypatch.setattr(connectors, "_graphql", lambda *args: {"shop": {"name": "Store", "myshopifyDomain": "merchant.myshopify.com"}, "currentAppInstallation": {"accessScopes": [{"handle": "read_products"}]}})
    with pytest.raises(connectors.ConnectionError, match="read products, orders, and returns"):
        connectors.save_connection(db, shopify_input())
    assert not db.all("store_connections")


def test_graphql_scope_error_is_recognized_even_when_http_request_succeeds(monkeypatch):
    monkeypatch.setattr(connectors, "secure_request", lambda *args, **kwargs: ({
        "errors": [{"message": "private upstream message", "extensions": {"code": "ACCESS_DENIED"}}], "data": None,
    }, {}))
    with pytest.raises(connectors.ConnectionError, match="read products, orders, and returns") as error:
        connectors.verify_connection(shopify_input())
    assert error.value.status_code is None
    assert "private upstream" not in str(error.value)


def mock_woo(monkeypatch, *, reason="Shoes too narrow", ambiguous=False):
    timestamp = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    line = {"id": 11, "product_id": 2, "variation_id": 3, "quantity": 2, "total": "80.00"}
    order = {"id": 1, "date_created_gmt": timestamp, "status": "completed", "currency": "GBP", "line_items": [line], "refunds": [{"id": 4}]}
    refund_line = {**line, "id": 22, "quantity": -1, "total": "-40.00", "meta_data": [{"key": "_refunded_item_id", "value": "11"}]}
    if ambiguous:
        order["line_items"].append({**line, "id": 12})
        refund_line["meta_data"] = []
    refund = {"id": 4, "date_created_gmt": timestamp, "amount": "40.00", "reason": reason, "line_items": [refund_line]}
    product = {"id": 2, "name": "Walking Shoes", "description": "<p>Lightweight walking shoes</p>", "short_description": "<p>Regular width</p>", "images": [], "sku": "SHOES", "price": "40.00", "variations": [3]}
    calls = []
    def woo(body, resource, params=None):
        calls.append((body, resource, params))
        assert body.consumer_key == "ck_private-key" and body.consumer_secret == "cs_private-secret"
        if resource == "products":
            return [product], {"x-wp-totalpages": "1"}
        if resource == "orders":
            return [order], {"x-wp-totalpages": "1"}
        if resource == "orders/1/refunds":
            return [refund], {"x-wp-totalpages": "1"}
        raise AssertionError(resource)
    monkeypatch.setattr(connectors, "_woo", woo)
    return calls


def test_woocommerce_refund_feedback_links_original_sales_items_without_received_claim(db, monkeypatch):
    mock_woo(monkeypatch)
    connection = connectors.save_connection(db, woo_input())
    summary = connectors.sync_connection(db, connection["id"])
    assert summary["orders"] == summary["refunds"] == summary["returns"] == 1
    order, feedback, refund = db.all("orders")[0], db.all("returns")[0], db.all("refunds")[0]
    assert feedback["order_item_id"] == order["items"][0]["id"] == refund["items"][0]["order_item_id"]
    assert feedback["record_kind"] == "refund_feedback" and feedback["source"] == "woocommerce"
    assert feedback["status"] == "closed" and not feedback["physical_return_confirmed"]
    assert feedback["quantity"] == refund["items"][0]["quantity"] == 1
    assert order["items"][0]["quantity"] == 2
    assert db.all("products")[0]["description"] == "Lightweight walking shoes"
    connectors.sync_connection(db, connection["id"])
    assert len(db.all("refunds")) == len(db.all("returns")) == len(db.all("orders")) == 1


@pytest.mark.parametrize("reason,ambiguous", [("", False), ("Narrow shoes", True)])
def test_woocommerce_does_not_invent_feedback_or_item_links(db, monkeypatch, reason, ambiguous):
    mock_woo(monkeypatch, reason=reason, ambiguous=ambiguous)
    connection = connectors.save_connection(db, woo_input())
    result = connectors.sync_connection(db, connection["id"])
    assert result["refunds"] == 1 and result["returns"] == 0
    assert not db.all("returns")
    if ambiguous:
        assert db.all("refunds")[0]["items"][0]["order_item_id"] is None


@pytest.mark.parametrize("platform", ["shopify", "woocommerce"])
def test_connection_endpoints_enforce_roles_workspace_isolation_and_job_results(workspace, tmp_path, monkeypatch, platform):
    from fastapi.testclient import TestClient
    from backend import main
    from test_connections import register

    monkeypatch.setenv("CONNECTOR_KEY_PATH", str(tmp_path / "api-connector.key"))
    monkeypatch.delenv("CONNECTOR_ENCRYPTION_KEY", raising=False)
    monkeypatch.setattr(main, "analyze", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Sync must not silently run analysis")))
    if platform == "shopify":
        mock_shopify(monkeypatch)
        body = shopify_input()
    else:
        mock_woo(monkeypatch)
        body = woo_input()
    root, demo = workspace
    owner, session = register(f"live-{platform}-owner@example.com")
    other, _ = register(f"live-{platform}-other@example.com")
    invite = owner.post("/api/workspace/invites", json={"email": f"live-{platform}-analyst@example.com", "role": "analyst"})
    analyst, _ = register(f"live-{platform}-analyst@example.com", invite_token=invite.json()["token"])
    invite = owner.post("/api/workspace/invites", json={"email": f"live-{platform}-viewer@example.com", "role": "viewer"})
    viewer, _ = register(f"live-{platform}-viewer@example.com", invite_token=invite.json()["token"])

    assert TestClient(main.app).post("/api/connections/test", json=body.model_dump()).status_code == 401
    assert demo.post("/api/connections/test", json=body.model_dump()).status_code == 409
    for restricted in (analyst, viewer):
        assert restricted.post("/api/connections/test", json=body.model_dump()).status_code == 403
        assert restricted.post("/api/connections", json=body.model_dump()).status_code == 403
    assert owner.post("/api/connections/test", json=body.model_dump()).json()["verified"] is True
    saved = owner.post("/api/connections", json=body.model_dump())
    assert saved.status_code == 200, saved.text
    connection_id = saved.json()["id"]
    assert "credentials" not in saved.json()
    assert viewer.get("/api/connections").json()["stores"][0]["id"] == connection_id
    assert not other.get("/api/connections").json()["stores"]
    assert other.post(f"/api/connections/{connection_id}/sync").status_code == 404
    assert other.get("/api/connections", headers={"X-Workspace-ID": session["workspace"]["id"]}).status_code == 403
    assert viewer.post(f"/api/connections/{connection_id}/sync").status_code == 403
    assert analyst.post(f"/api/connections/{connection_id}/disconnect").status_code == 403
    job = analyst.post(f"/api/connections/{connection_id}/sync")
    assert job.status_code == 200, job.text
    event = next(event for event in analyst.get("/api/jobs").json() if event["id"] == job.json()["event_id"])
    assert event["kind"] == "connector_sync" and event["status"] == "complete", event
    assert event["result"]["products"] == 1 and event["result"]["orders"] == 1
    private = WorkspaceStore(root, session["workspace"]["id"])
    assert not private.all("analysis_reports")
    assert len(private.all("orders")) == 1
    assert owner.post(f"/api/connections/{connection_id}/disconnect").status_code == 200
    assert not owner.get("/api/connections").json()["stores"]
    assert owner.post(f"/api/connections/{connection_id}/sync").status_code == 404
    assert len(private.all("orders")) == 1  # Disconnecting preserves merchant records.


def test_sync_lease_prevents_parallel_updates_and_credential_changes(db, monkeypatch):
    monkeypatch.setattr(connectors, "verify_connection", lambda body: {"name": "Store"})
    connection = connectors.save_connection(db, shopify_input())
    started, release = Event(), Event()
    def fetch(*args):
        started.set()
        assert release.wait(5)
        return [], [], [], []
    monkeypatch.setattr(connectors, "_shopify_data", fetch)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(connectors.sync_connection, db, connection["id"])
        assert started.wait(5)
        try:
            with pytest.raises(connectors.ConnectionError, match="already updating"):
                connectors.sync_connection(db, connection["id"])
            with pytest.raises(connectors.ConnectionError, match="Wait"):
                connectors.disconnect_connection(db, connection["id"])
            with pytest.raises(connectors.ConnectionError, match="Wait"):
                connectors.save_connection(db, shopify_input())
        finally:
            release.set()
        assert future.result(timeout=5)["products"] == 0
    assert db.get("store_connections", connection["id"])["sync_lease"] is None
    assert connectors.disconnect_connection(db, connection["id"])["disconnected"]


def test_expired_connector_lease_can_recover_after_a_crash(db, monkeypatch):
    monkeypatch.setattr(connectors, "verify_connection", lambda body: {"name": "Store"})
    monkeypatch.setattr(connectors, "_shopify_data", lambda *args: ([], [], [], []))
    connection = connectors.save_connection(db, shopify_input())
    record = db.get("store_connections", connection["id"])
    db.put("store_connections", connection["id"], {**record, "sync_lease": time.time()-1900})
    assert connectors.sync_connection(db, connection["id"])["products"] == 0
    assert db.get("store_connections", connection["id"])["sync_lease"] is None


def app_credentials():
    return connectors.ConnectorInput(platform="shopify",url="merchant.myshopify.com",shopify_auth="client_credentials",client_id="client-id",client_secret="private-app-secret")


def test_shopify_app_credentials_exchange_is_reused_and_secrets_stay_encrypted(db,monkeypatch):
    calls=[]
    def request(method,url,**kwargs):
        calls.append((method,url,kwargs))
        if url.endswith("/admin/oauth/access_token"):
            assert kwargs["form_data"]=={"grant_type":"client_credentials","client_id":"client-id","client_secret":"private-app-secret"}
            return {"access_token":"temporary-private-token","expires_in":86400},{}
        assert kwargs["headers"]["X-Shopify-Access-Token"]=="temporary-private-token"
        return {"data":{"shop":{"name":"Owned store","myshopifyDomain":"merchant.myshopify.com"},"currentAppInstallation":{"accessScopes":[{"handle":"read_"+area} for area in ["products","orders","returns"]]}}},{}
    monkeypatch.setattr(connectors,"secure_request",request)
    body=app_credentials()
    assert connectors.verify_connection(body)["verified"]
    public=connectors.save_connection(db,body)
    assert public["shopify_auth"]=="client_credentials"
    assert connectors.public_connections(db)[0]["shopify_auth"]=="client_credentials"
    assert len([call for call in calls if call[1].endswith("/admin/oauth/access_token")])==1
    record=db.get("store_connections",public["id"])
    assert "private-app-secret" not in json.dumps(record)
    decrypted=json.loads(connectors._cipher().decrypt(record["credentials"].encode()))
    assert decrypted["client_secret"]=="private-app-secret"
    assert "temporary-private-token" not in json.dumps(decrypted)
    assert "_temporary_token" not in body.model_dump()
    assert connectors._credentials(db,record).shopify_auth=="client_credentials"


def test_expiring_app_token_is_refreshed_before_next_request(monkeypatch):
    clock=[1000.0];issued=[]
    monkeypatch.setattr(connectors.time,"time",lambda:clock[0])
    def exchange(*args,**kwargs):
        issued.append(True)
        return {"access_token":f"temporary-token-{len(issued)}","expires_in":120},{ }
    monkeypatch.setattr(connectors,"secure_request",exchange)
    body=app_credentials()
    assert connectors._shopify_access_token(body)=="temporary-token-1"
    clock[0]=1059
    assert connectors._shopify_access_token(body)=="temporary-token-1"
    clock[0]=1060
    assert connectors._shopify_access_token(body)=="temporary-token-2"
    assert len(issued)==2


@pytest.mark.parametrize("result",[{"access_token":"token"},{"access_token":"token","expires_in":0},{"access_token":"bad\ntoken","expires_in":86400}])
def test_invalid_app_exchange_never_accepts_or_echoes_credentials(monkeypatch,result):
    monkeypatch.setattr(connectors,"secure_request",lambda *args,**kwargs:(result,{}))
    with pytest.raises(connectors.ConnectionError) as error:
        connectors._shopify_access_token(app_credentials())
    assert "app is installed" in str(error.value)
    assert "private-app-secret" not in str(error.value)


def test_failed_app_authorization_preserves_upstream_status_but_replaces_unsafe_messages(monkeypatch):
    def reject(*args, **kwargs):
        raise connectors.ConnectionError("private-app-secret from unsafe upstream", status_code=401)
    monkeypatch.setattr(connectors, "secure_request", reject)
    with pytest.raises(connectors.ConnectionError, match="confirm the app is installed") as error:
        connectors._shopify_access_token(app_credentials())
    assert error.value.status_code==401
    assert "private-app-secret" not in str(error.value)


def test_public_connection_method_is_optional_for_legacy_records_and_absent_for_other_stores():
    legacy = connectors._public({"id": "legacy", "platform": "shopify", "credentials": "encrypted"})
    assert "shopify_auth" not in legacy and "credentials" not in legacy
    malformed = connectors._public({"id": "malformed", "platform": "shopify", "shopify_auth": "private-token"})
    assert "shopify_auth" not in malformed
    woo = connectors._public({"id": "woo", "platform": "woocommerce", "shopify_auth": "access_token"})
    assert "shopify_auth" not in woo


def test_pasted_credentials_trim_outer_whitespace_but_reject_internal_controls():
    body=connectors.ConnectorInput(platform="shopify",url=" merchant.myshopify.com ",access_token=" token-with-newline\n")
    assert body.access_token=="token-with-newline"
    with pytest.raises(ValidationError):
        connectors.ConnectorInput(platform="shopify",url="merchant.myshopify.com",access_token="token\ninside")
