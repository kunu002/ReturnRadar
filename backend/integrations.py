import base64
import hashlib
import hmac
import json
import os
import re
import uuid
import httpx
from .models import ReturnRecord
from .store import now
from .runtime import TASK_DEADLINE_SECONDS


def verify_signature(body, signature, secret):
    expected = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
    return bool(signature) and hmac.compare_digest(expected, signature)


def shopify_config():
    domain = os.getenv("SHOPIFY_STORE_DOMAIN", "")
    token = os.getenv("SHOPIFY_ACCESS_TOKEN", "")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9-]*\.myshopify\.com", domain) or not token:
        raise ValueError("Configure SHOPIFY_STORE_DOMAIN and SHOPIFY_ACCESS_TOKEN on the server first.")
    return domain, token


def graphql(query, variables=None):
    domain, token = shopify_config()
    version = os.getenv("SHOPIFY_API_VERSION", "2026-07")
    response = httpx.post(f"https://{domain}/admin/api/{version}/graphql.json", headers={"X-Shopify-Access-Token":token}, json={"query":query,"variables":variables or {}}, timeout=30)
    response.raise_for_status()
    payload = response.json()
    if payload.get("errors"):
        # Do not expose raw upstream responses or credentials to the browser.
        raise ValueError("Shopify rejected the query. Check API scopes and server logs.")
    return payload["data"]


PRODUCT_QUERY = """query Products($cursor:String){ products(first:50,after:$cursor){nodes{id title description featuredImage{url} variants(first:100){nodes{id title sku} pageInfo{hasNextPage}}} pageInfo{hasNextPage endCursor}}}"""
ORDER_QUERY = """query Orders($cursor:String){orders(first:25,after:$cursor,query:"status:any",sortKey:CREATED_AT,reverse:true){nodes{id createdAt displayFulfillmentStatus lineItems(first:100){nodes{id quantity variant{id product{id}}} pageInfo{hasNextPage}} returns(first:100){nodes{id status returnLineItems(first:100){nodes{... on ReturnLineItem{id quantity returnReason returnReasonNote fulfillmentLineItem{lineItem{id variant{id product{id}}}}}} pageInfo{hasNextPage}}} pageInfo{hasNextPage}}} pageInfo{hasNextPage endCursor}}}"""


def sync_shopify(store):
    shopify_config()
    products=[]
    cursor=None
    while True:
        connection=graphql(PRODUCT_QUERY,{"cursor":cursor})["products"]
        for p in connection["nodes"]:
            if p["variants"]["pageInfo"]["hasNextPage"]:
                raise ValueError("A product has over 100 variants. Extend variant pagination before syncing this store.")
            product={"id":p["id"],"name":p["title"],"description":p["description"],"listing_claim":p["description"],"image":(p.get("featuredImage") or {}).get("url"),"variant":"Multiple variants","sku":p["variants"]["nodes"][0].get("sku","") if p["variants"]["nodes"] else "","price":None,"source":"shopify","variants":p["variants"]["nodes"]}
            products.append(product)
        if not connection["pageInfo"]["hasNextPage"]:
            break
        cursor=connection["pageInfo"]["endCursor"]
    orders=[]
    returns=[]
    cursor=None
    while True:
        connection=graphql(ORDER_QUERY,{"cursor":cursor})["orders"]
        for order in connection["nodes"]:
            if order["lineItems"]["pageInfo"]["hasNextPage"] or order["returns"]["pageInfo"]["hasNextPage"]:
                raise ValueError("An order exceeds the demo page limit. Extend nested pagination before syncing.")
            orders.append({"id":order["id"],"created_at":order["createdAt"],"fulfillment_status":order["displayFulfillmentStatus"],"source":"shopify","items":[{"id":li["id"],"quantity":li["quantity"],"variant_id":(li.get("variant") or {}).get("id"),"product_id":((li.get("variant") or {}).get("product") or {}).get("id")} for li in order["lineItems"]["nodes"]]})
            for ret in order["returns"]["nodes"]:
                if ret["returnLineItems"]["pageInfo"]["hasNextPage"]:
                    raise ValueError("A return exceeds 100 items. Extend nested pagination before syncing.")
                for item in ret["returnLineItems"]["nodes"]:
                    li=(item.get("fulfillmentLineItem") or {}).get("lineItem") or {}
                    variant=li.get("variant") or {}
                    if not variant.get("product"):
                        continue
                    existing=store.get("returns",item["id"])
                    # CLOSED describes processing, not proof that an item was physically received.
                    normalized=ReturnRecord(id=item["id"],return_id=ret["id"],product_id=variant["product"]["id"],variant_id=variant["id"],order_id=order["id"],order_item_id=li["id"],reason=item.get("returnReasonNote") or item.get("returnReason") or "Unspecified",returned_at=existing["returned_at"] if existing else now(),status=ret["status"].lower(),source="shopify",issue=(existing or {}).get("issue"),listing_snapshot_id=(existing or {}).get("listing_snapshot_id"))
                    returns.append(normalized.model_dump())
        if not connection["pageInfo"]["hasNextPage"]:
            break
        cursor=connection["pageInfo"]["endCursor"]
    for p in products:
        previous=store.get("products",p["id"])
        if not previous or previous.get("description") != p["description"] or previous.get("image") != p["image"]:
            snapshot_id="listing-"+hashlib.sha256((p["id"]+now()).encode()).hexdigest()[:24]
            store.put("snapshots",snapshot_id,{"id":snapshot_id,"product_id":p["id"],"description":p["description"],"image":p["image"],"captured_at":now()})
        store.put("products",p["id"],p)
    for o in orders:
        store.put("orders",o["id"],o)
    for r in returns:
        store.put("returns",r["id"],r)
    summary={"id":"sync","at":now(),"products":len(products),"orders":len(orders),"returns":len(returns),"source":"shopify","date_basis":"First observed by ReturnRadar; Shopify return query does not provide historical receipt dates."}
    store.put("meta","sync",summary)
    return summary


def enqueue(event_id,workspace_id=None):
    from google.cloud import tasks_v2
    project=os.environ["GOOGLE_CLOUD_PROJECT"]
    region=os.getenv("CLOUD_TASKS_LOCATION","asia-south1")
    queue=os.environ["CLOUD_TASKS_QUEUE"]
    worker=os.environ["WORKER_URL"].rstrip("/")
    client=tasks_v2.CloudTasksClient()
    parent=client.queue_path(project,region,queue)
    task={"name":f"{parent}/tasks/rr-{uuid.uuid4().hex}","dispatch_deadline":{"seconds":TASK_DEADLINE_SECONDS},"http_request":{"http_method":tasks_v2.HttpMethod.POST,"url":worker+"/api/internal/process","headers":{"Content-Type":"application/json"},"body":json.dumps({"event_id":event_id,"workspace_id":workspace_id}).encode(),"oidc_token":{"service_account_email":os.environ["TASKS_SERVICE_ACCOUNT"],"audience":worker}}}
    # Keep queue failures inside Firebase Hosting's proxy budget. The saved
    # pending event remains retryable instead of falling back to an API process.
    client.create_task(parent=parent,task=task,retry=None,timeout=15)
