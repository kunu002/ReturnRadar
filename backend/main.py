import csv
import io
import json
import logging
import os
import hmac
import secrets
import time
import uuid
from collections import defaultdict, deque
from threading import BoundedSemaphore
from datetime import datetime, timezone
from pathlib import Path
from fastapi import FastAPI, BackgroundTasks, Depends, HTTPException, Request, Response, UploadFile, File, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import JSONResponse
from .store import store, now, WorkspaceStore
from . import accounts
from .seed import seed
from .models import Approval, FitInput, ReturnRecord, ChatRequest, ProcessTask
from .analysis import dashboard, analyze
from .integrations import verify_signature, sync_shopify, enqueue
from .gemini import GeminiUnavailable, configured_model
from .chat import answer_question
from .connections import connection_summary
from .connectors import ConnectorInput, ConnectionError, verify_connection, save_connection, disconnect_connection, sync_connection, public_connections
from .reviews import CycleCreate, CycleUpdate, create_cycle, update_cycle, review_summary
from .product_actions import buyer_guide, product_category, fit_supported, public_check_ready
from .runtime import EVENT_LEASE_SECONDS, hosted

DEMO_MODE = os.getenv("DEMO_MODE", "false" if os.getenv("K_SERVICE") else "true").lower() == "true"
SERVICE_ROLE = os.getenv("SERVICE_ROLE", "api")
logger = logging.getLogger("returnradar")
app = FastAPI(title="ReturnRadar", version="0.1.0", docs_url=None if hosted() or SERVICE_ROLE == "worker" else "/docs", redoc_url=None if hosted() or SERVICE_ROLE == "worker" else "/redoc", openapi_url=None if hosted() or SERVICE_ROLE == "worker" else "/openapi.json")
origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["GET","POST","PATCH"], allow_headers=["Authorization","Content-Type","X-Workspace-ID","X-Demo-Workspace"])


@app.exception_handler(RequestValidationError)
async def connection_validation_error(request:Request,exc:RequestValidationError):
    if request.url.path not in ["/api/connections","/api/connections/test"]:
        return await request_validation_exception_handler(request,exc)
    issues=exc.errors()
    first=issues[0] if issues else {}
    field=str(first.get("loc",["body"])[-1])
    # Model-level connection errors are fixed application messages; never echo submitted inputs.
    safe_messages={
        "Enter a valid store address.","Enter your store's myshopify.com address.",
        "Enter your store's myshopify.com address, not the app settings page.",
        "Use your store's HTTPS address without a login, query, or port.",
        "Enter a public WooCommerce store address.",
        "Enter the store home address, including its installation folder if needed.",
        "Enter the store access token.","Enter the WooCommerce consumer key and consumer secret.",
        "Enter your Shopify app Client ID and Client secret.",
        "Connection details contain invalid characters.",
    }
    message=str(first.get("msg",""))
    if message.startswith("Value error, "):
        message=message[len("Value error, "):]
    if message not in safe_messages:
        message={"url":"Enter a valid store address.","platform":"Choose a store platform.","access_token":"Check the store access token and try again.","client_id":"Check your Shopify app Client ID.","client_secret":"Check your Shopify app Client secret.","consumer_key":"Check the WooCommerce consumer key.","consumer_secret":"Check the WooCommerce consumer secret."}.get(field,"Check your store address and connection details, then try again.")
    logger.info("Store connection details need correction: field=%s",field)
    return JSONResponse(status_code=422,content={"detail":message})


@app.middleware("http")
async def check_origin(request, call_next):
    # Worker IAM is configured separately from the public API. Also keep its
    # application surface restricted to probes and task processing.
    if SERVICE_ROLE == "worker" and (request.method, request.url.path) not in {("GET", "/api/health"), ("GET", "/api/ready"), ("POST", "/api/internal/process")}:
        return JSONResponse(status_code=404, content={"detail":"Not Found"})
    if SERVICE_ROLE != "worker" and request.url.path == "/api/internal/process":
        return JSONResponse(status_code=404, content={"detail":"Not Found"})
    if request.method in ["POST", "PATCH", "DELETE"] and request.headers.get("origin") and request.headers["origin"] not in origins:
        return JSONResponse(status_code=403,content={"detail":"Request origin is not allowed."})
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        # Firebase Hosting must never cache another merchant's response.
        response.headers["Cache-Control"] = "private, no-store"
    return response
if DEMO_MODE:
    seed(store)
limits = defaultdict(deque)
chat_limits = defaultdict(deque)
chat_slot = BoundedSemaphore(1)


def seller(request: Request):
    if SERVICE_ROLE != "api":
        raise HTTPException(404)
    if DEMO_MODE and request.headers.get("x-demo-workspace")=="true" and not request.cookies.get(accounts.SESSION_COOKIE) and not request.headers.get("authorization"):
        return accounts.Access("local-demo","demo@localhost","Demo merchant","demo","owner",store)
    user=accounts.identity(store,request)
    workspace_id=request.headers.get("x-workspace-id") or user.get("default_workspace")
    if not workspace_id:
        raise HTTPException(409,"Create a workspace or accept an invitation first.")
    return accounts.access(store,user,workspace_id)


def permission(name):
    def authorized(current=Depends(seller)):
        return current.require(name)
    return authorized


def account_user(request: Request):
    if SERVICE_ROLE != "api":
        raise HTTPException(404)
    return accounts.identity(store,request)


def public_guard(request):
    if SERVICE_ROLE != "api":
        raise HTTPException(404)
    key = request.client.host if request.client else "unknown"
    timestamp=time.monotonic()
    hits=limits[key]
    while hits and timestamp-hits[0] > 60:
        hits.popleft()
    if len(hits) >= 30:
        raise HTTPException(429,"Too many fit checks. Try again in a minute.")
    hits.append(timestamp)
    if len(limits) > 5000:
        for old in list(limits):
            if not limits[old] or timestamp-limits[old][-1] > 60:
                del limits[old]


@app.get("/api/health")
def health():
    return {"status":"ok","demo":DEMO_MODE}


@app.get("/api/ready")
def readiness():
    try:
        store.get("meta", "readiness")
    except Exception:
        logger.exception("Storage readiness check failed")
        raise HTTPException(503, "Service is temporarily unavailable.") from None
    return {"status":"ready"}


@app.get("/api/config")
def config():
    if SERVICE_ROLE != "api":
        raise HTTPException(404)
    return {"demo":DEMO_MODE,"auth_provider":accounts.provider(),"firebase":{"apiKey":os.getenv("FIREBASE_WEB_API_KEY",""),"authDomain":os.getenv("FIREBASE_AUTH_DOMAIN",""),"projectId":os.getenv("GOOGLE_CLOUD_PROJECT","")}}


@app.post("/api/auth/register")
def register(body:accounts.Registration,request:Request,response:Response):
    if SERVICE_ROLE != "api":
        raise HTTPException(404)
    accounts.local_only()
    accounts.auth_guard(request)
    if body.invite_token:
        accounts.valid_invite(store,body.invite_token,body.email)
    elif body.workspace_mode=="empty" and not body.workspace_name.strip():
        raise HTTPException(422,"Enter your merchant workspace name.")
    uid="local-"+accounts.digest(body.email)
    user={"id":uid,"name":body.name.strip(),"email":body.email,"password_hash":accounts.password_hash(body.password),"created_at":now()}
    if not store.create("auth_users",uid,user):
        raise HTTPException(409,"This email already has an account. Sign in instead.")
    try:
        # An invitation always joins its merchant workspace; it must not add
        # synthetic fixtures to the business that invited this account.
        workspace_id=accounts.join_workspace(store,user,body.invite_token) if body.invite_token else accounts.create_workspace(store,user,body.workspace_name,body.workspace_mode)
    except HTTPException as exc:
        if body.invite_token or body.workspace_mode!="sample" or exc.status_code!=503:
            raise
        # Keep a newly registered user signed in even if fixture provisioning
        # was interrupted, so Explore demo can safely resume the pending seed.
        pending=JSONResponse(status_code=503,content={"detail":exc.detail})
        accounts.issue_session(store,uid,pending)
        return pending
    accounts.issue_session(store,uid,response)
    return accounts.session_info(store,accounts.access(store,user,workspace_id))


@app.post("/api/auth/login")
def sign_in(body:accounts.AccountInput,request:Request,response:Response):
    if SERVICE_ROLE != "api":
        raise HTTPException(404)
    accounts.local_only()
    accounts.auth_guard(request)
    user=store.get("auth_users","local-"+accounts.digest(body.email))
    saved=(user or {}).get("password_hash", "00"*16+":"+"00"*64)
    candidate=accounts.password_hash(body.password,saved.split(":",1)[0])
    if not user or not hmac.compare_digest(candidate,saved):
        raise HTTPException(401,"Email or password is incorrect.")
    accounts.issue_session(store,user["id"],response)
    return {"signed_in":True}


@app.post("/api/auth/logout")
def sign_out(request:Request,response:Response):
    if SERVICE_ROLE != "api":
        raise HTTPException(404)
    cookie=request.cookies.get(accounts.SESSION_COOKIE)
    if cookie:
        record=store.get("auth_sessions",accounts.digest(cookie))
        if record:
            store.put("auth_sessions",record["id"],{**record,"revoked":True})
    response.delete_cookie(accounts.SESSION_COOKIE,path="/")
    return {"signed_out":True}


@app.get("/api/auth/session")
def current_session(request:Request):
    if SERVICE_ROLE != "api":
        raise HTTPException(404)
    if request.headers.get("x-demo-workspace")=="true" and DEMO_MODE and not request.cookies.get(accounts.SESSION_COOKIE) and not request.headers.get("authorization"):
        return accounts.session_info(store,seller(request))
    user=accounts.identity(store,request)
    if not user.get("default_workspace") and not request.headers.get("x-workspace-id"):
        return {"needs_workspace":True,"user":{"name":user["name"],"email":user["email"]}}
    return accounts.session_info(store,seller(request))


@app.get("/api/auth/identity")
def signed_in_identity(user=Depends(account_user)):
    return {"user":{"id":user["id"],"name":user["name"],"email":user["email"]}}


@app.post("/api/auth/workspace")
def new_workspace(body:accounts.WorkspaceInput,user=Depends(account_user)):
    workspace_id=accounts.create_workspace(store,user,body.name,body.mode)
    return accounts.session_info(store,accounts.access(store,user,workspace_id))


@app.post("/api/auth/join")
def join(body:accounts.JoinInput,user=Depends(account_user)):
    workspace_id=accounts.join_workspace(store,user,body.token)
    return accounts.session_info(store,accounts.access(store,user,workspace_id))


@app.get("/api/auth/workspaces")
def my_workspaces(user=Depends(account_user)):
    result=[]
    for workspace in store.all("workspaces"):
        member=WorkspaceStore(store,workspace["id"]).get("members",user["id"])
        if member and member["active"] and (not workspace.get("sample_data") or workspace.get("sample_ready")):
            result.append({"id":workspace["id"],"name":workspace["name"],"role":member["role"],"sample_data":bool(workspace.get("sample_data"))})
    return result


@app.get("/api/workspace/members")
def members(current=Depends(permission("members"))):
    if current.workspace_id=="demo":
        return []
    return current.db.all("members")


@app.post("/api/workspace/invites")
def invite_member(body:accounts.InviteInput,current=Depends(permission("members"))):
    if current.workspace_id=="demo":
        raise HTTPException(409,"Sign in to manage a merchant team.")
    if current.role=="admin" and body.role=="admin":
        raise HTTPException(403,"Only the owner can invite an Admin.")
    token=secrets.token_urlsafe(32)
    key=accounts.digest(token)
    store.create("workspace_invites",key,{"id":key,"workspace_id":current.workspace_id,"email":body.email,"role":body.role,"created_by":current.uid,"expires":time.time()+48*3600,"used":False,"revoked":False})
    accounts.audit(current,"invite",body.email)
    return {"token":token,"email":body.email,"role":body.role,"expires_in_hours":48}


@app.patch("/api/workspace/members/{member_id}")
def change_member(member_id:str,body:accounts.MemberChange,current=Depends(permission("members"))):
    member=current.db.get("members",member_id)
    if not member:
        raise HTTPException(404,"Member not found.")
    if member["role"]=="owner" or member_id==current.uid:
        raise HTTPException(403,"The owner's access and your own role cannot be changed here.")
    if current.role=="admin" and (body.role=="admin" or member["role"]=="admin"):
        raise HTTPException(403,"Only the owner can manage Admin access.")
    member.update(body.model_dump())
    current.db.put("members",member_id,member)
    accounts.audit(current,"member_access",member_id)
    return member


@app.get("/api/workspace/audit")
def audit_log(current=Depends(permission("members"))):
    return sorted(current.db.all("audit"),key=lambda a:a["at"],reverse=True)[:50]


@app.get("/api/dashboard")
def get_dashboard(days:int=Query(28,ge=7,le=90),source:str=Query("all",pattern="^(all|synthetic|csv|shopify|woocommerce)$"),current=Depends(permission("read"))):
    return dashboard(current.db,days,source)


@app.get("/api/connections")
def connections(current=Depends(permission("read"))):
    summary=connection_summary(current.db)
    summary["stores"]=public_connections(current.db)
    summary["connectors"]=[{"id":"csv","name":"Return CSV","status":"available","method":"manual_import"},{"id":"shopify","name":"Shopify","status":"available","method":"store_connection"},{"id":"woocommerce","name":"WooCommerce","status":"available","method":"store_connection"}]
    imported_orders=[o for o in current.db.all("orders") if o.get("connector_id")]
    summary["coverage"]["sales"]={"status":"available" if imported_orders else "not_imported","orders":len(imported_orders)}
    summary["coverage"]["refunds"]={"records":len(current.db.all("refunds"))}
    return summary


def connector_admin(current):
    current.require("members")
    if current.workspace_id=="demo":
        raise HTTPException(409,"Sign in to your own workspace to connect a store.")


@app.post("/api/connections/test")
def test_connection(body:ConnectorInput,current=Depends(seller)):
    connector_admin(current)
    try:
        return verify_connection(body)
    except ConnectionError as exc:
        raise HTTPException(422,str(exc))


@app.post("/api/connections")
def connect_store(body:ConnectorInput,current=Depends(seller)):
    connector_admin(current)
    try:
        result=save_connection(current.db,body)
    except ConnectionError as exc:
        raise HTTPException(422,str(exc))
    accounts.audit(current,"connect_store",result["id"])
    return result


@app.post("/api/connections/{connection_id}/disconnect")
def disconnect_store(connection_id:str,current=Depends(seller)):
    connector_admin(current)
    try:
        result=disconnect_connection(current.db,connection_id)
    except ConnectionError as exc:
        raise HTTPException(404,str(exc))
    accounts.audit(current,"disconnect_store",connection_id)
    return result


@app.post("/api/connections/{connection_id}/sync")
def update_store(connection_id:str,background:BackgroundTasks,current=Depends(permission("import"))):
    connection=current.db.get("store_connections",connection_id)
    if not connection or connection.get("status")=="disconnected":
        raise HTTPException(404,"Connect this store before updating its data.")
    if any(e.get("connector_id")==connection_id and (e["status"] in ["pending","queued"] or e["status"]=="processing" and time.time()-e.get("lease_at",0)<1800) for e in current.db.all("events")):
        raise HTTPException(409,"This store is already being updated. Please wait for the result.")
    event_id=uuid.uuid4().hex
    current.db.create("events",event_id,{"id":event_id,"kind":"connector_sync","connector_id":connection_id,"status":"pending","created_at":now(),"created_by":current.uid,"workspace_id":current.workspace_id})
    dispatch(event_id,background,current.workspace_id)
    return {"event_id":event_id,"status":"pending"}


@app.get("/api/reviews")
def product_reviews(current=Depends(permission("read"))):
    return review_summary(current.db)


@app.post("/api/reviews")
def start_review(body:CycleCreate,current=Depends(permission("propose"))):
    return create_cycle(current.db,body,current.uid)


@app.patch("/api/reviews/{review_id}")
def review_decision(review_id:str,body:CycleUpdate,current=Depends(permission("approve"))):
    return update_cycle(current.db,review_id,body,current.uid)


@app.post("/api/chat")
def chat(body:ChatRequest, current=Depends(permission("chat"))):
    uid=current.workspace_id+":"+current.uid
    if not body.question.strip():
        raise HTTPException(422,"Enter a question about your returns or how to use ReturnRadar.")
    timestamp=time.monotonic()
    hits=chat_limits[uid]
    while hits and timestamp-hits[0]>60:
        hits.popleft()
    if len(hits)>=6:
        raise HTTPException(429,"Ask Radar allows six questions a minute. Please wait a moment.")
    if not chat_slot.acquire(blocking=False):
        raise HTTPException(429,"Ask Radar is answering another question. Try again shortly.")
    try:
        hits.append(timestamp)
        return answer_question(current.db,body,current.role)
    except GeminiUnavailable as exc:
        raise HTTPException(503,str(exc))
    finally:
        chat_slot.release()


@app.get("/api/ai-status")
def ai_status(current=Depends(permission("read"))):
    store=current.db
    return {"configured":bool(os.getenv("GEMINI_API_KEY")),"model":configured_model(),"last_call":store.get("meta","gemini_last_call"),"analysis":store.get("meta","analysis")}


@app.patch("/api/actions/{action_id:path}")
def update_action(action_id:str, approval:Approval, current=Depends(permission("approve"))):
    store=current.db
    uid=current.uid
    action=store.get("actions",action_id)
    if not action:
        raise HTTPException(404,"Action not found.")
    if approval.status == "approved" and action["kind"] == "fit_check":
        product=store.get("products",action["product_id"])
        if product and not fit_supported(product,[action]):
            raise HTTPException(422,"Use a product-specific buyer guide for this product.")
        if product and len(product.get("variants",[]))>1:
            raise HTTPException(422,"The demo fit check requires a single product variant. Use a single-variant development-store product for this MVP.")
        if not approval.measurements_verified or any(v is None for v in [approval.width,approval.height,approval.depth]):
            raise HTTPException(422,"Verify all three compartment measurements before publishing the fit check.")
    if approval.status=="approved" and action["kind"]=="buyer_guide":
        checklist=approval.checklist or action.get("checklist") or []
        if not approval.guidance_verified or not 2<=len(checklist)<=8 or not all(isinstance(item,str) and 3<=len(item.strip())<=250 for item in checklist):
            raise HTTPException(422,"Verify the product facts and buyer checklist before publishing this guide.")
    action.update(approval.model_dump(exclude_unset=True))
    action.update({"reviewed_by":uid,"reviewed_at":now()})
    store.put("actions",action_id,action)
    accounts.audit(current,"action_decision",action_id)
    return action


@app.post("/api/products/{product_id:path}/propose")
def propose(product_id:str,current=Depends(permission("propose"))):
    store=current.db
    product=store.get("products",product_id)
    if not product:
        raise HTTPException(404,"Product not found.")
    patterns=[p for p in dashboard(store)["patterns"] if p["product_id"] == product_id]
    guide=buyer_guide(product)
    guide_key=f"buyer-guide-{product_id}"
    store.create("actions",guide_key,{"id":guide_key,"product_id":product_id,"status":"draft","issue":patterns[0]["issue"] if patterns else "Product expectations","source":product["source"],**guide})
    for pattern in patterns:
        issue=pattern["issue"]
        plans={
            "Laptop fit":("fit_check","Help buyers check laptop fit","Compare laptop dimensions, including any sleeve, with merchant-verified compartment measurements."),
            "Size & fit":("listing","Make sizing easier to choose","Check measurements across sizes and update the size guide with verified fit information. Review the next customer feedback before claiming improvement."),
            "Product quality":("inspection","Check product performance","Inspect a sample of affected stock against the advertised features. Record what fails and plan one verified improvement."),
            "Damaged on arrival":("inspection","Improve protection in transit","Review packaging protection and test it with sample shipments. Record the change and follow up on damage complaints."),
            "Missing parts":("inspection","Check every included part","Add a packing checklist for the included pieces, verify it on a sample, and review new missing-part complaints."),
            "Packaging":("inspection","Review packaging and seals","Inspect seals, closures and protective packaging. Choose one change and follow up on similar complaints."),
            "Description mismatch":("listing","Clarify product expectations","Compare the advertised details with verified product measurements and materials. Update only facts you have checked."),
            "Wrong item":("inspection","Check product picking and packing","Compare the ordered variant with packing labels, introduce a checked pick list, and review repeat mistakes."),
            "Color mismatch":("listing","Check colour expectations","Review product photos under consistent lighting and clarify verified colour details before updating the listing."),
            "Zipper quality":("inspection","Inspect zipper performance","Check zipper movement on a sample of affected stock, record failure points, and follow up after the chosen change."),
            "Strap comfort":("listing","Clarify comfort and carrying guidance","Inspect strap construction and review carrying guidance against verified product facts. Follow up on comfort feedback.")}
        kind,title,text=plans.get(issue,("inspection","Review repeated feedback",f"Review the {pattern['count']} linked complaints, choose one change to test, and track its results in Product Reviews."))
        if kind=="fit_check" and not fit_supported(product):
            kind,title,text="listing","Clarify the product requirements","Review the product details and clarify verified requirements; use the product-specific buyer guide rather than assuming a laptop dimension check applies."
        key=f"proposal-{product_id}-{issue}"
        store.create("actions",key,{"id":key,"product_id":product_id,"kind":kind,"title":title,"text":text,"status":"draft","issue":issue,"measurements_verified":False,"width":None,"height":None,"depth":None,"source":product["source"],"evidence_ids":[r["id"] for r in pattern["evidence"]]})
    return {"proposed":len(patterns)+1}


def buyer_store(workspace):
    if workspace=="demo":
        if not DEMO_MODE:
            raise HTTPException(404,"Product not found.")
        return store
    if not store.get("workspaces",workspace):
        raise HTTPException(404,"Product not found.")
    return WorkspaceStore(store,workspace)


@app.get("/api/public/products/{product_id:path}")
def public_product(product_id:str,workspace:str=Query("demo",max_length=100)):
    if SERVICE_ROLE != "api":
        raise HTTPException(404)
    db=buyer_store(workspace)
    p=db.get("products",product_id)
    if not p:
        raise HTTPException(404,"Product not found.")
    actions=[a for a in db.all("actions") if a["product_id"]==product_id]
    rule=next((a for a in actions if a["kind"]=="fit_check" and public_check_ready(a,p)),None)
    guide=next((a for a in actions if a["kind"]=="buyer_guide" and public_check_ready(a,p)),None)
    return {"id":p["id"],"name":p["name"],"description":p["description"],"image":p.get("image"),"price":p.get("price"),"variant":p.get("variant"),"category":product_category(p),"currency":p.get("currency") or ("INR" if p["source"]=="synthetic" else None),"demo":p["source"] == "synthetic","fit_check_supported":fit_supported(p,actions),"fit_available":bool(rule),"compartment":{k:rule[k] for k in ["width","height","depth"]} if rule else None,"question":rule["text"] if rule else None,"purchase_check":{"kind":"buyer_guide","guide_type":guide.get("guide_type","general"),"title":guide["title"],"instructions":guide["text"],"checklist":guide["checklist"]} if guide else None}


@app.post("/api/public/products/{product_id:path}/fit")
def fit(product_id:str, body:FitInput, request:Request,workspace:str=Query("demo",max_length=100)):
    public_guard(request)
    p=public_product(product_id,workspace)
    if not p["fit_available"]:
        return {"status":"cannot_verify","title":"We can't verify fit yet","message":"Verified compartment measurements are not available for this product."}
    # Inputs always include sleeve. Avoid inventing an assumed sleeve thickness.
    dimensions=p["compartment"]
    failed=[name for name in ["width","height","depth"] if getattr(body,name) > dimensions[name]]
    if failed:
        return {"status":"mismatch","title":"This laptop is likely too large","message":f"Your {' and '.join(failed)} exceeds the verified compartment measurement. Consider another bag.","compartment":dimensions}
    return {"status":"within_dimensions","title":"Your measurements are within range","message":"The dimensions are within the verified compartment measurements. Openings, shape and padding can affect physical fit; this check cannot guarantee it.","compartment":dimensions}


@app.post("/api/import")
async def import_csv(file:UploadFile=File(...),platform:str=Query("generic",pattern="^(generic|shopify|woocommerce|other)$"),current=Depends(permission("import"))):
    store=current.db
    content=await file.read(2_000_001)
    if len(content) > 2_000_000:
        raise HTTPException(413,"CSV must be smaller than 2 MB.")
    try:
        text=content.decode("utf-8-sig")
        reader=csv.DictReader(io.StringIO(text))
        allowed=set(ReturnRecord.model_fields) | {"product_name","variant_name"}
        if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)) or set(reader.fieldnames)-allowed:
            raise ValueError("CSV has duplicate or unsupported columns. Use the provided template.")
        records=[]
        products={}
        seen=set()
        for line,row in enumerate(reader,2):
            if None in row:
                raise ValueError(f"Line {line}: the row has more fields than the header.")
            if len(records) >= 1000:
                raise ValueError("Use at most 1,000 return items per import.")
            clean={k:v for k,v in row.items() if k in ReturnRecord.model_fields and v not in [None,""]}
            clean["source"]="csv"
            clean["issue"]=None
            clean["listing_snapshot_id"]=None
            record=ReturnRecord.model_validate(clean)
            date=datetime.fromisoformat(record.returned_at.replace("Z","+00:00"))
            if not date.tzinfo or date > datetime.now(timezone.utc):
                raise ValueError(f"Line {line}: use a past or current ISO timestamp with a timezone.")
            if record.id in seen:
                raise ValueError(f"Line {line}: duplicate return-item id.")
            existing=store.get("returns",record.id)
            if existing and existing["source"] != "csv":
                raise ValueError(f"Line {line}: id belongs to another data source.")
            product=store.get("products",record.product_id)
            if product and product["source"] != "csv":
                raise ValueError(f"Line {line}: product id belongs to another data source. Use unique CSV product IDs.")
            existing_order=store.get("orders",record.order_id)
            if existing_order and existing_order["source"] != "csv":
                raise ValueError(f"Line {line}: order id belongs to another data source. Use unique CSV order IDs.")
            seen.add(record.id)
            records.append({**record.model_dump(),"source_platform":platform})
            products[record.product_id]={"id":record.product_id,"name":row.get("product_name") or record.product_id,"variant":row.get("variant_name") or record.variant_id,"sku":record.variant_id,"description":"Listing not imported","listing_claim":"Listing unavailable","image":None,"price":None,"source":"csv"}
        if not records:
            raise ValueError("The CSV does not contain any return items.")
    except (ValueError,TypeError,UnicodeError) as exc:
        raise HTTPException(422,str(exc))
    # Validate the entire file before performing any writes.
    for product in products.values():
        store.put("products",product["id"],product)
    for record in records:
        store.put("returns",record["id"],record)
        order=store.get("orders",record["order_id"]) or {"id":record["order_id"],"source":"csv","items":[]}
        items={item["id"]:item for item in order["items"]}
        items[record["order_item_id"]]={"id":record["order_item_id"],"variant_id":record["variant_id"],"product_id":record["product_id"]}
        store.put("orders",record["order_id"],{**order,"items":list(items.values())})
    if os.getenv("STORAGE_BUCKET"):
        from google.cloud import storage
        storage.Client().bucket(os.environ["STORAGE_BUCKET"]).blob(f"imports/{current.workspace_id}/{uuid.uuid4().hex}.csv").upload_from_string(content,content_type="text/csv")
    store.put("meta","sync",{"id":"sync","at":now(),"source":"csv","returns":len(records),"products":len(products)})
    import_id=uuid.uuid4().hex
    store.create("import_runs",import_id,{"id":import_id,"platform":platform,"method":"csv","records":len(records),"products":len(products),"at":now(),"imported_by":current.uid})
    accounts.audit(current,"csv_import",str(len(records)))
    return {"imported":len(records),"products":len(products)}


def process_event(event_id,workspace_id=None):
    db=WorkspaceStore(store,workspace_id) if workspace_id else store
    claimed=db.claim("events",event_id,lambda e:e["status"] in ["pending","queued","failed"] or (e["status"] == "processing" and time.time()-e.get("lease_at",0) > EVENT_LEASE_SECONDS),{"status":"processing","lease_at":time.time()})
    if not claimed:
        event=db.get("events",event_id)
        if event and event["status"] == "processing":
            raise RuntimeError("Event is already processing; retry later.")
        return
    try:
        if claimed["kind"]=="connector_sync":
            result=sync_connection(db,claimed["connector_id"])
        elif claimed["kind"] in ["sync","webhook"]:
            if workspace_id:
                raise RuntimeError("Merchant workspaces use CSV imports.")
            sync_shopify(db)
            result=analyze(db,claimed.get("product_id"))
        else:
            result=analyze(db,claimed.get("product_id"))
        db.put("events",event_id,{**claimed,"status":"complete","finished_at":now(),"error":None,"result":result})
    except Exception as exc:
        logger.exception("Processing event failed: %s",event_id)
        message=str(exc) if isinstance(exc,(GeminiUnavailable,ConnectionError)) else "This task could not be completed. Please try again."
        db.put("events",event_id,{**claimed,"status":"failed","error":message,"provider_status":getattr(exc,"code",None),"finished_at":now()})
        raise


def process_local(event_id,workspace_id=None):
    try:
        process_event(event_id,workspace_id)
    except Exception:
        pass


def dispatch(event_id,background,workspace_id=None):
    if os.getenv("CLOUD_TASKS_QUEUE"):
        try:
            enqueue(event_id,workspace_id) if workspace_id else enqueue(event_id)
        except Exception:
            logger.exception("Enqueue failed")
            raise HTTPException(503,"Task delivery failed. The saved event can be retried.")
    else:
        if os.getenv("K_SERVICE"):
            raise HTTPException(503,"Configure Cloud Tasks before accepting background work.")
        background.add_task(process_local,event_id,workspace_id)


@app.post("/api/jobs")
def start_job(background:BackgroundTasks,kind:str=Query("analysis",pattern="^(sync|analysis)$"),product_id:str|None=None,current=Depends(permission("analyse"))):
    db=current.db
    if kind=="sync" and current.workspace_id!="demo":
        raise HTTPException(409,"This workspace uses CSV imports. Import your exported return records instead.")
    if product_id and not db.get("products",product_id):
        raise HTTPException(404,"Product not found.")
    event_id=uuid.uuid4().hex
    db.create("events",event_id,{"id":event_id,"kind":kind,"product_id":product_id,"status":"pending","created_at":now(),"created_by":current.uid,"workspace_id":current.workspace_id})
    accounts.audit(current,"start_"+kind,event_id)
    dispatch(event_id,background,None if current.workspace_id=="demo" else current.workspace_id)
    return {"event_id":event_id,"status":"pending"}


@app.get("/api/jobs")
def jobs(current=Depends(permission("read"))):
    return sorted(current.db.all("events"),key=lambda e:e["created_at"],reverse=True)[:50]


@app.post("/api/jobs/{event_id}/retry")
def retry(event_id:str,background:BackgroundTasks,current=Depends(permission("analyse"))):
    db=current.db
    event=db.get("events",event_id)
    if not event:
        raise HTTPException(404,"Task not found.")
    if event["status"] not in ["failed","pending"]:
        raise HTTPException(409,"Only pending or failed tasks can be retried.")
    if event.get("attempts",0)>=3:
        raise HTTPException(409,"Retry limit reached. Resolve the cause, then start a new task.")
    event.update({"status":"pending","attempts":event.get("attempts",0)+1})
    db.put("events",event_id,event)
    dispatch(event_id,background,None if current.workspace_id=="demo" else current.workspace_id)
    return {"status":"pending"}


@app.get("/api/integrations")
def integrations(current=Depends(permission("read"))):
    demo=current.workspace_id=="demo"
    return {"shopify":{"configured":demo and bool(os.getenv("SHOPIFY_STORE_DOMAIN") and os.getenv("SHOPIFY_ACCESS_TOKEN")),"domain":os.getenv("SHOPIFY_STORE_DOMAIN","") if demo else "","last_sync":current.db.get("meta","sync")},"gemini":{"configured":bool(os.getenv("GEMINI_API_KEY")),"model":configured_model(),"last_call":current.db.get("meta","gemini_last_call")},"storage":os.getenv("STORAGE_BACKEND","sqlite"),"queue":bool(os.getenv("CLOUD_TASKS_QUEUE")),"demo":demo,"csv_only":not demo}


@app.post("/api/webhooks/shopify")
async def webhook(request:Request,background:BackgroundTasks):
    if SERVICE_ROLE != "api" or not DEMO_MODE:
        raise HTTPException(404)
    body=await request.body()
    if len(body)>2_000_000:
        raise HTTPException(413)
    secret=os.getenv("SHOPIFY_WEBHOOK_SECRET")
    if not secret or not verify_signature(body,request.headers.get("x-shopify-hmac-sha256",""),secret):
        raise HTTPException(401,"Invalid webhook signature.")
    if request.headers.get("x-shopify-shop-domain") != os.getenv("SHOPIFY_STORE_DOMAIN"):
        raise HTTPException(403,"Unexpected Shopify store.")
    topic=request.headers.get("x-shopify-topic","")
    if topic not in ["returns/request","returns/approve","returns/update","returns/process","returns/close","returns/cancel","returns/reopen","returns/decline","refunds/create"]:
        raise HTTPException(400,"Unsupported webhook topic.")
    event_id=request.headers.get("x-shopify-event-id") or request.headers.get("x-shopify-webhook-id")
    if not event_id or len(event_id)>180:
        raise HTTPException(400,"Missing event ID.")
    try:
        json.loads(body)
    except ValueError:
        raise HTTPException(400,"Invalid webhook JSON.")
    store.create("events",event_id,{"id":event_id,"kind":"webhook","topic":topic,"status":"pending","created_at":now()})
    event=store.get("events",event_id)
    if event["status"] in ["complete","processing"]:
        return {"accepted":True,"duplicate":True}
    dispatch(event_id,background)
    return {"accepted":True}


@app.post("/api/internal/process")
def internal_process(payload:ProcessTask):
    # Cloud Run IAM authenticates Cloud Tasks OIDC before this private service is reached.
    if SERVICE_ROLE != "worker" or not os.getenv("K_SERVICE"):
        raise HTTPException(404)
    workspace_id=payload.workspace_id
    if not isinstance(workspace_id,str) or not store.get("workspaces",workspace_id):
        raise HTTPException(404,"Unknown workspace.")
    db=WorkspaceStore(store,workspace_id)
    if not db.get("events",payload.event_id):
        raise HTTPException(404,"Unknown event.")
    try:
        process_event(payload.event_id,workspace_id)
    except Exception:
        raise HTTPException(503,"Processing failed; retry delivery.")
    return {"processed":True}


if Path("dist").exists() and SERVICE_ROLE == "api":
    app.mount("/",StaticFiles(directory="dist",html=True),name="frontend")
