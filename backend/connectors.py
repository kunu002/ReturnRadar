"""Merchant-owned, read-only store connections. Credentials never leave this module."""
import hashlib
import ipaddress
import json
import logging
import os
import re
import socket
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

import httpx
from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

from .models import ReturnRecord
from .store import now


logger = logging.getLogger("returnradar.connectors")


class ConnectionError(ValueError):
    """Safe merchant-facing failure; upstream responses may contain secrets."""

    def __init__(self, message, *, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class ConnectorInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    platform: Literal["shopify", "woocommerce"]
    url: str = Field(min_length=3, max_length=300)
    access_token: str = Field(default="", max_length=500)
    shopify_auth: Literal["access_token", "client_credentials"] = "access_token"
    client_id: str = Field(default="", max_length=200)
    client_secret: str = Field(default="", max_length=500)
    consumer_key: str = Field(default="", max_length=200)
    consumer_secret: str = Field(default="", max_length=200)
    _temporary_token: str = PrivateAttr(default="")
    _token_expires_at: float = PrivateAttr(default=0)

    @model_validator(mode="after")
    def credentials(self):
        self.url = normalize_url(self.platform, self.url)
        for field in ("access_token", "client_id", "client_secret", "consumer_key", "consumer_secret"):
            setattr(self, field, getattr(self, field).strip())
        if self.platform == "shopify":
            if self.shopify_auth=="client_credentials":
                if not self.client_id or not self.client_secret:
                    raise ValueError("Enter your Shopify app Client ID and Client secret.")
                self.access_token=""
            else:
                if not self.access_token:
                    raise ValueError("Enter the store access token.")
                self.client_id=self.client_secret=""
        if self.platform == "woocommerce" and not (self.consumer_key.startswith("ck_") and self.consumer_secret.startswith("cs_")):
            raise ValueError("Enter the WooCommerce consumer key and consumer secret.")
        if any(ord(c) < 32 for c in self.access_token + self.client_id + self.client_secret + self.consumer_key + self.consumer_secret):
            raise ValueError("Connection details contain invalid characters.")
        return self


def normalize_url(platform, value):
    value = value.strip()
    if platform == "shopify" and "://" not in value:
        value = "https://" + value
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError("Enter a valid store address.") from None
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host or parsed.username or parsed.password or parsed.query or parsed.fragment or port not in (None, 443):
        raise ValueError("Use your store's HTTPS address without a login, query, or port.")
    if platform == "shopify":
        if host in ("dev.shopify.com", "admin.shopify.com"):
            raise ValueError("Enter your store's myshopify.com address, not the app settings page.")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.myshopify\.com", host) or parsed.path.rstrip("/"):
            raise ValueError("Enter your store's myshopify.com address.")
        return "https://" + host
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host) or "." not in host or any(part in host for part in ("..",)):
        raise ValueError("Enter a public WooCommerce store address.")
    try:
        literal_address = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not literal_address.is_global:
            raise ValueError("Enter a public WooCommerce store address.")
    path = parsed.path.rstrip("/")
    if not re.fullmatch(r"(?:/[A-Za-z0-9_-]+)*", path):
        raise ValueError("Enter the store home address, including its installation folder if needed.")
    return "https://" + host + path


def public_addresses(host):
    """Reject every non-public DNS answer; never allow mixed public/private answers."""
    try:
        values = sorted({answer[4][0] for answer in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})
        if not values or any(not ipaddress.ip_address(value).is_global for value in values):
            raise ConnectionError("Connect a public store with a secure HTTPS address.")
        return values
    except (OSError, ValueError):
        raise ConnectionError("The store address could not be reached. Check the address and try again.") from None


def secure_request(method, url, *, headers=None, auth=None, params=None, payload=None, form_data=None):
    """Pin the checked IP while retaining TLS verification and the original host/SNI."""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.port not in (None, 443) or not parsed.hostname or parsed.username or parsed.password:
        raise ConnectionError("Connect a public store with a secure HTTPS address.")
    host = parsed.hostname
    address = public_addresses(host)[0]
    target = "[" + address + "]" if ":" in address else address
    pinned = urlunsplit(("https", target, parsed.path, parsed.query, ""))
    request_headers = {**(headers or {}), "Host": host, "Accept": "application/json"}
    try:
        with httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as client:
            with client.stream(method, pinned, headers=request_headers, auth=auth, params=params, json=payload, data=form_data,
                               extensions={"sni_hostname": host}) as response:
                if response.status_code in (401, 403):
                    shopify = bool(re.fullmatch(r"[a-z0-9][a-z0-9-]*\.myshopify\.com", host))
                    request_id = response.headers.get("x-request-id", "")
                    if not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", request_id):
                        request_id = "unavailable"
                    logger.warning("Store request rejected: platform=%s operation=%s status=%d request_id=%s",
                                   "shopify" if shopify else "woocommerce",
                                   "authorization" if shopify and parsed.path == "/admin/oauth/access_token" else "data",
                                   response.status_code, request_id)
                    if response.status_code == 401:
                        message = ("Shopify could not verify this access token. It may be expired or belong to another store. Enter a current Admin access token, or choose App credentials for a store in your own Shopify organization."
                                   if shopify else "The store could not verify these connection details. Check the consumer key and consumer secret.")
                    else:
                        message = ("Shopify refused access to this store. Check the app installation and the store's access restrictions."
                                   if shopify else "The store refused access. Check that the connected account can read products and orders, and that the store allows the connection.")
                    raise ConnectionError(message, status_code=response.status_code)
                if response.status_code == 429:
                    raise ConnectionError("Your store is busy. Wait a minute before updating its data again.")
                if 300 <= response.status_code < 400:
                    raise ConnectionError("The store redirected the request. Enter its final HTTPS store address.")
                if response.status_code >= 400:
                    raise ConnectionError("The store could not provide this data. Check its connection settings and try again.")
                chunks = bytearray()
                for chunk in response.iter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > 10_000_000:
                        raise ConnectionError("This store response is too large. Contact support before syncing this store.")
                return json.loads(chunks), dict(response.headers)
    except ConnectionError:
        raise
    except (httpx.HTTPError, ValueError, OSError):
        raise ConnectionError("The store could not be reached. Check its address and try again shortly.") from None


def _cipher():
    key = os.getenv("CONNECTOR_ENCRYPTION_KEY", "")
    if not key:
        if os.getenv("K_SERVICE") or os.getenv("STORAGE_BACKEND") == "firestore":
            raise ConnectionError("Store connections need to be enabled by your workspace administrator.")
        path = Path(os.getenv("CONNECTOR_KEY_PATH", "data/connector.key"))
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, "wb") as keyfile:
                keyfile.write(Fernet.generate_key())
        os.chmod(path, 0o600)
        key = path.read_bytes()
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError):
        raise ConnectionError("Store connections need to be enabled by your workspace administrator.") from None


def _workspace(db):
    return getattr(db, "workspace_id", "demo")


def _public(record):
    allowed = ("id", "platform", "url", "name", "status", "connected_at", "last_sync", "last_error", "coverage")
    result = {key: record.get(key) for key in allowed}
    if record.get("platform") == "shopify" and record.get("shopify_auth") in ("access_token", "client_credentials"):
        result["shopify_auth"] = record["shopify_auth"]
    return result


def public_connections(db):
    return [_public(record) for record in db.all("store_connections") if record["status"] != "disconnected"]


def _credentials(db, record):
    try:
        data = json.loads(_cipher().decrypt(record["credentials"].encode()))
        if data.pop("workspace_id") != _workspace(db) or data.pop("connection_id") != record["id"]:
            raise InvalidToken
        return ConnectorInput.model_validate(data)
    except (InvalidToken, KeyError, TypeError, ValueError):
        raise ConnectionError("Reconnect this store before updating its data.") from None


def _shopify_access_token(body):
    if body.shopify_auth=="access_token":
        return body.access_token
    if body._temporary_token and time.time()<body._token_expires_at-60:
        return body._temporary_token
    message="Shopify could not authorize this app. Check the Client ID and Client secret, confirm the app is installed, and use a store in your own Shopify organization."
    try:
        result,_=secure_request("POST",body.url+"/admin/oauth/access_token",form_data={"grant_type":"client_credentials","client_id":body.client_id,"client_secret":body.client_secret})
    except ConnectionError as error:
        raise ConnectionError(message, status_code=error.status_code) from None
    token=result.get("access_token") if isinstance(result,dict) else None
    expires=result.get("expires_in") if isinstance(result,dict) else None
    if not isinstance(token,str) or not token or len(token)>500 or any(ord(c)<33 for c in token) or not isinstance(expires,(int,float)) or isinstance(expires,bool) or expires<=60 or expires>86400:
        raise ConnectionError(message)
    body._temporary_token=token
    body._token_expires_at=time.time()+expires
    return token


def _graphql(body, query, variables=None):
    version = os.getenv("SHOPIFY_API_VERSION", "2026-07")
    if not re.fullmatch(r"20\d{2}-(01|04|07|10)", version):
        raise ConnectionError("Store connections need to be enabled by your workspace administrator.")
    result, _ = secure_request("POST", body.url + f"/admin/api/{version}/graphql.json",
                              headers={"X-Shopify-Access-Token": _shopify_access_token(body)},
                              payload={"query": query, "variables": variables or {}})
    if any(isinstance(error, dict) and isinstance(error.get("extensions"), dict)
           and error["extensions"].get("code") == "ACCESS_DENIED" for error in result.get("errors") or []):
        raise ConnectionError("Allow this connection to read products, orders, and returns in Shopify, then try again.")
    if result.get("errors") or not result.get("data"):
        raise ConnectionError("Shopify could not provide the requested data. Check access to products, orders, and returns.")
    return result["data"]


def _woo(body, resource, params=None):
    return secure_request("GET", body.url + "/wp-json/wc/v3/" + resource,
                          auth=(body.consumer_key, body.consumer_secret), params=params or {})


def verify_connection(body):
    if body.platform == "shopify":
        data = _graphql(body, "query ConnectionCheck { shop { name myshopifyDomain } currentAppInstallation { accessScopes { handle } } }")
        scopes = {item["handle"] for item in data["currentAppInstallation"]["accessScopes"]}
        if any(not ({"read_" + area, "write_" + area} & scopes) for area in ("products", "orders", "returns")):
            raise ConnectionError("Allow this connection to read products, orders, and returns in Shopify, then try again.")
        if data["shop"]["myshopifyDomain"].lower() != urlsplit(body.url).hostname:
            raise ConnectionError("The access token belongs to a different Shopify store.")
        name = data["shop"]["name"]
    else:
        for resource in ("products", "orders"):
            records, _ = _woo(body, resource, {"per_page": 1})
            if not isinstance(records, list):
                raise ConnectionError("This address did not provide WooCommerce store data.")
        name = urlsplit(body.url).hostname
    return {"platform": body.platform, "url": body.url, "name": name, "verified": True}


def save_connection(db, body):
    connection_id = hashlib.sha256((body.platform + ":" + body.url).encode()).hexdigest()[:24]
    if _updating(db, connection_id):
        raise ConnectionError("Wait for this store update to finish before reconnecting it.")
    verified = verify_connection(body)
    old = db.get("store_connections", connection_id) or {}
    encrypted = _cipher().encrypt(json.dumps({**body.model_dump(), "workspace_id": _workspace(db), "connection_id": connection_id}).encode()).decode()
    record = {"id": connection_id, "platform": body.platform, "url": body.url, "name": verified["name"], "status": "connected",
              "connected_at": old.get("connected_at") or now(), "last_sync": old.get("last_sync"), "last_error": None,
              "coverage": old.get("coverage"), "credentials": encrypted}
    if body.platform == "shopify":
        record["shopify_auth"] = body.shopify_auth
    db.put("store_connections", connection_id, record)
    return _public(record)


def disconnect_connection(db, connection_id):
    record = db.get("store_connections", connection_id)
    if not record or record["status"] == "disconnected":
        raise ConnectionError("This store connection was not found.")
    if _updating(db, connection_id):
        raise ConnectionError("Wait for this store update to finish before disconnecting it.")
    db.put("store_connections", connection_id, {**record, "status": "disconnected", "credentials": None, "last_error": None})
    return {"disconnected": True}


def _updating(db, connection_id):
    record = db.get("store_connections", connection_id) or {}
    if record.get("sync_lease") and time.time() - record["sync_lease"] < 1800:
        return True
    return any(event.get("connector_id") == connection_id and
               (event["status"] in ("pending", "queued") or event["status"] == "processing" and time.time() - event.get("lease_at", 0) < 1800)
               for event in db.all("events") if event.get("kind") == "connector_sync")


def _id(connection_id, value):
    return "store-" + connection_id + "-" + hashlib.sha256(str(value).encode()).hexdigest()[:24]


def _pages(fetch):
    cursor = None
    seen = set()
    for _ in range(500):
        connection = fetch(cursor)
        yield from connection["nodes"]
        info = connection["pageInfo"]
        if not info["hasNextPage"]:
            return
        cursor = info.get("endCursor")
        if not cursor or cursor in seen:
            raise ConnectionError("The store returned an incomplete page of data. Try updating again.")
        seen.add(cursor)
    raise ConnectionError("This store has more data than one update can process. Contact support to extend its coverage.")


PRODUCTS_QUERY = """query StoreProducts($cursor:String){products(first:25,after:$cursor){nodes{id title description featuredImage{url} variants(first:10){nodes{id title sku price} pageInfo{hasNextPage endCursor}}} pageInfo{hasNextPage endCursor}}}"""
VARIANTS_QUERY = """query StoreVariants($id:ID!,$cursor:String){product(id:$id){variants(first:100,after:$cursor){nodes{id title sku price} pageInfo{hasNextPage endCursor}}}}"""
ORDERS_QUERY = """query StoreOrders($cursor:String,$filter:String!){orders(first:10,after:$cursor,query:$filter,sortKey:CREATED_AT){nodes{id createdAt cancelledAt displayFinancialStatus displayFulfillmentStatus currencyCode lineItems(first:10){nodes{id quantity currentQuantity discountedTotalSet{shopMoney{amount currencyCode}} variant{id product{id}}} pageInfo{hasNextPage endCursor}} returns(first:5){nodes{id createdAt status returnLineItems(first:1){nodes{... on ReturnLineItem{id quantity returnReason returnReasonNote fulfillmentLineItem{lineItem{id variant{id product{id}}}}}} pageInfo{hasNextPage endCursor}}} pageInfo{hasNextPage endCursor}}} pageInfo{hasNextPage endCursor}}}"""
LINES_QUERY = """query StoreOrderLines($id:ID!,$cursor:String){order(id:$id){lineItems(first:100,after:$cursor){nodes{id quantity currentQuantity discountedTotalSet{shopMoney{amount currencyCode}} variant{id product{id}}} pageInfo{hasNextPage endCursor}}}}"""
RETURNS_QUERY = """query StoreOrderReturns($id:ID!,$cursor:String){order(id:$id){returns(first:20,after:$cursor){nodes{id createdAt status returnLineItems(first:1){nodes{... on ReturnLineItem{id quantity returnReason returnReasonNote fulfillmentLineItem{lineItem{id variant{id product{id}}}}}} pageInfo{hasNextPage endCursor}}} pageInfo{hasNextPage endCursor}}}}"""
RETURN_LINES_QUERY = """query StoreReturnLines($id:ID!,$cursor:String){return(id:$id){returnLineItems(first:100,after:$cursor){nodes{... on ReturnLineItem{id quantity returnReason returnReasonNote fulfillmentLineItem{lineItem{id variant{id product{id}}}}}} pageInfo{hasNextPage endCursor}}}}"""


def _nested(body, first, query, owner_id, path):
    def fetch(cursor):
        if cursor is None:
            return first
        data = _graphql(body, query, {"id": owner_id, "cursor": cursor})
        for part in path:
            data = data[part]
        return data
    return list(_pages(fetch))


def _shopify_data(db, body, connection_id, start, end):
    products, orders, returns = [], [], []
    for item in _pages(lambda cursor: _graphql(body, PRODUCTS_QUERY, {"cursor": cursor})["products"]):
        variants = _nested(body, item["variants"], VARIANTS_QUERY, item["id"], ("product", "variants"))
        products.append({"id": _id(connection_id, item["id"]), "upstream_id": item["id"], "name": item["title"],
                         "description": item["description"], "listing_claim": item["description"], "image": (item.get("featuredImage") or {}).get("url"),
                         "variant": "Multiple variants" if len(variants) > 1 else (variants[0]["title"] if variants else "Default"),
                         "sku": variants[0].get("sku", "") if variants else "", "price": float(variants[0]["price"]) if variants and variants[0].get("price") else None,
                         "source": "shopify", "connector_id": connection_id,
                         "variants": [{**variant, "id": _id(connection_id, variant["id"])} for variant in variants]})
    query_filter = f"status:any created_at:>={start} created_at:<={end}"
    for order in _pages(lambda cursor: _graphql(body, ORDERS_QUERY, {"cursor": cursor, "filter": query_filter})["orders"]):
        lines = _nested(body, order["lineItems"], LINES_QUERY, order["id"], ("order", "lineItems"))
        order_id = _id(connection_id, order["id"])
        items = []
        for line in lines:
            variant = line.get("variant") or {}
            product_id = (variant.get("product") or {}).get("id")
            if not product_id:
                continue
            money = (line.get("discountedTotalSet") or {}).get("shopMoney") or {}
            items.append({"id": _id(connection_id, line["id"]), "product_id": _id(connection_id, product_id),
                          "variant_id": _id(connection_id, variant["id"]), "quantity": line["quantity"],
                          "net_total": money.get("amount"), "currency": money.get("currencyCode", order["currencyCode"])})
        orders.append({"id": order_id, "upstream_id": order["id"], "created_at": order["createdAt"],
                       "status": "cancelled" if order.get("cancelledAt") else order["displayFinancialStatus"].lower(),
                       "fulfillment_status": order["displayFulfillmentStatus"], "source": "shopify", "connector_id": connection_id, "items": items})
        order_returns = _nested(body, order["returns"], RETURNS_QUERY, order["id"], ("order", "returns"))
        for return_item in order_returns:
            for line in _nested(body, return_item["returnLineItems"], RETURN_LINES_QUERY, return_item["id"], ("return", "returnLineItems")):
                if not line.get("id"):
                    continue
                order_line = (line.get("fulfillmentLineItem") or {}).get("lineItem") or {}
                variant = order_line.get("variant") or {}
                product_id = (variant.get("product") or {}).get("id")
                if not product_id:
                    continue
                record_id = _id(connection_id, line["id"])
                existing = db.get("returns", record_id) or {}
                record = ReturnRecord(id=record_id, product_id=_id(connection_id, product_id), variant_id=_id(connection_id, variant["id"]),
                                      order_id=order_id, order_item_id=_id(connection_id, order_line["id"]), return_id=_id(connection_id, return_item["id"]),
                                      reason=line.get("returnReasonNote") or line.get("returnReason") or "No reason provided", returned_at=return_item["createdAt"],
                                      status=return_item["status"].lower(), source="shopify", issue=existing.get("issue"), listing_snapshot_id=existing.get("listing_snapshot_id"))
                returns.append({**record.model_dump(), "upstream_id": line["id"], "quantity": line["quantity"], "connector_id": connection_id,
                                "date_basis": "Return request date; physical receipt is not established by this status."})
    return products, orders, returns, []


def _woo_pages(body, resource, params=None):
    for page in range(1, 501):
        items, headers = _woo(body, resource, {**(params or {}), "per_page": 100, "page": page})
        if not isinstance(items, list):
            raise ConnectionError("WooCommerce returned incomplete store data. Try updating again.")
        yield from items
        total = int(headers.get("x-wp-totalpages", 0) or 0)
        if (total and page >= total) or not items or len(items) < 100:
            return
    raise ConnectionError("This store has more data than one update can process. Contact support to extend its coverage.")


def _woo_data(db, body, connection_id, start, end):
    products, orders, returns, refunds = [], [], [], []
    for item in _woo_pages(body, "products"):
        products.append({"id": _id(connection_id, item["id"]), "upstream_id": str(item["id"]), "name": item["name"],
                         "description": re.sub(r"<[^>]*>", " ", item.get("description", "")).strip(),
                         "listing_claim": re.sub(r"<[^>]*>", " ", item.get("short_description") or item.get("description", "")).strip(),
                         "image": (item.get("images") or [{}])[0].get("src"), "variant": "Multiple variants" if item.get("variations") else "Default",
                         "sku": item.get("sku", ""), "price": float(item["price"]) if item.get("price") else None,
                         "source": "woocommerce", "connector_id": connection_id})
    for order in _woo_pages(body, "orders", {"after": start, "before": end, "dates_are_gmt": True, "status": "any"}):
        order_id = _id(connection_id, order["id"])
        items = [{"id": _id(connection_id, line["id"]), "product_id": _id(connection_id, line["product_id"]),
                  "variant_id": _id(connection_id, line.get("variation_id") or line["product_id"]), "quantity": line["quantity"],
                  "net_total": line.get("total"), "currency": order["currency"]} for line in order.get("line_items", [])]
        orders.append({"id": order_id, "upstream_id": str(order["id"]), "created_at": _utc_date(order["date_created_gmt"]),
                       "status": order["status"], "source": "woocommerce", "connector_id": connection_id, "items": items})
        if order.get("refunds"):
            for refund in _woo_pages(body, f"orders/{order['id']}/refunds"):
                mapped_lines = []
                for line in refund.get("line_items", []):
                    original_id = next((str(meta["value"]) for meta in line.get("meta_data", []) if meta.get("key") == "_refunded_item_id"), None)
                    originals = [original for original in order.get("line_items", []) if
                                 (str(original["id"]) == original_id if original_id else
                                  original.get("product_id") == line.get("product_id") and original.get("variation_id", 0) == line.get("variation_id", 0))]
                    original = originals[0] if len(originals) == 1 else None
                    quantity = abs(line.get("quantity", 0))
                    mapped_lines.append({"product_id": _id(connection_id, line["product_id"]),
                                         "variant_id": _id(connection_id, line.get("variation_id") or line["product_id"]),
                                         "order_item_id": _id(connection_id, original["id"]) if original else None,
                                         "quantity": quantity, "amount": line.get("total")})
                    # A recorded refund reason is useful feedback, but is never a received return.
                    if not original or not quantity or not refund.get("reason", "").strip():
                        continue
                    record_id = _id(connection_id, f"refund-feedback-{refund['id']}-{original['id']}")
                    existing = db.get("returns", record_id) or {}
                    normalized = ReturnRecord(id=record_id, product_id=_id(connection_id, line["product_id"]),
                                              variant_id=_id(connection_id, line.get("variation_id") or line["product_id"]), order_id=order_id,
                                              order_item_id=_id(connection_id, original["id"]), return_id=_id(connection_id, "refund-" + str(refund["id"])),
                                              refund_id=str(refund["id"]), reason=refund["reason"].strip()[:2000], returned_at=_utc_date(refund["date_created_gmt"]),
                                              source="woocommerce", status="closed", issue=existing.get("issue"), listing_snapshot_id=existing.get("listing_snapshot_id"))
                    returns.append({**normalized.model_dump(), "quantity": quantity, "connector_id": connection_id,
                                    "record_kind": "refund_feedback", "physical_return_confirmed": False,
                                    "date_basis": "Refund date; refund reason applies to this refund, not proof of a received return."})
                refunds.append({"id": _id(connection_id, "refund-" + str(refund["id"])), "refund_id": str(refund["id"]), "order_id": order_id,
                                "created_at": _utc_date(refund["date_created_gmt"]), "reason": refund.get("reason") or "No refund reason provided",
                                "amount": refund.get("amount"), "currency": order["currency"], "source": "woocommerce", "connector_id": connection_id,
                                "items": mapped_lines, "physical_return_confirmed": False})
    return products, orders, returns, refunds


def _utc_date(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=timezone.utc).isoformat()


def sync_connection(db, connection_id):
    record = db.get("store_connections", connection_id)
    if not record or record["status"] == "disconnected":
        raise ConnectionError("Reconnect this store before updating its data.")
    record = db.claim("store_connections", connection_id,
                      lambda item: item["status"] != "disconnected" and (not item.get("sync_lease") or time.time() - item["sync_lease"] >= 1800),
                      {"sync_lease": time.time()})
    if not record:
        raise ConnectionError("This store is already updating. Wait for it to finish before trying again.")
    try:
        body = _credentials(db, record)
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=60)
        start, end = (value.isoformat().replace("+00:00", "Z") for value in (start_time, end_time))
        products, orders, returns, refunds = (_shopify_data if body.platform == "shopify" else _woo_data)(db, body, connection_id, start, end)
        if len(products) + len(orders) + len(returns) + len(refunds) > 100_000:
            raise ConnectionError("This store has more data than one update can process. Contact support to extend its coverage.")
        # All pages must succeed before records are written. Repeat syncs update stable IDs.
        for product in products:
            previous = db.get("products", product["id"])
            if not previous or previous.get("description") != product["description"] or previous.get("image") != product["image"]:
                snapshot_id = "listing-" + hashlib.sha256((product["id"] + now()).encode()).hexdigest()[:24]
                db.put("snapshots", snapshot_id, {"id": snapshot_id, "product_id": product["id"], "description": product["description"], "image": product["image"], "captured_at": now()})
            db.put("products", product["id"], product)
        for collection, items in (("orders", orders), ("returns", returns), ("refunds", refunds)):
            for item in items:
                db.put(collection, item["id"], item)
        summary = {"id": connection_id, "source": body.platform, "connector_id": connection_id, "at": now(),
                   "products": len(products), "orders": len(orders), "returns": len(returns), "refunds": len(refunds),
                   "coverage": {"start": start, "end": end, "days": 60, "basis": "Orders created in the last 60 days"},
                   "date_basis": "Shopify return request date" if body.platform == "shopify" else "Refund date; refunds are separate from physical returns"}
        db.put("meta", "sync", {**summary, "id": "sync"})
        run_id = hashlib.sha256((connection_id + summary["at"]).encode()).hexdigest()[:24]
        db.create("sync_runs", run_id, {**summary, "id": run_id, "store_name": record["name"]})
        db.put("store_connections", connection_id, {**record, "status": "connected", "last_sync": summary, "last_error": None, "coverage": summary["coverage"], "sync_lease": None})
        return summary
    except ConnectionError as error:
        db.put("store_connections", connection_id, {**record, "status": "attention", "last_error": str(error), "sync_lease": None})
        raise
    except Exception:
        message = "The store update could not finish. Reconnect the store or try again shortly."
        db.put("store_connections", connection_id, {**record, "status": "attention", "last_error": message, "sync_lease": None})
        raise ConnectionError(message) from None
