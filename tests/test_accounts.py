import json
import time
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from backend import main, accounts
from backend.store import WorkspaceStore
from test_workflows import workspace

PASSWORD="ReturnRadar42!"


def register(email,name="Olive Carry",token=""):
    client=TestClient(main.app)
    result=client.post("/api/auth/register",json={"name":"Merchant","email":email,"password":PASSWORD,"workspace_name":name,"invite_token":token})
    assert result.status_code==200,result.text
    return client,result.json()


def upload(client,reason="Laptop too wide"):
    date=datetime.now(timezone.utc).isoformat()
    data="id,product_id,product_name,variant_id,order_id,order_item_id,return_id,reason,returned_at\n"+f"csv-1,csv-bag,Merchant bag,csv-v,csv-o,csv-item,csv-return,{reason},{date}\n"
    response=client.post("/api/import",files={"file":("returns.csv",data,"text/csv")})
    assert response.status_code==200,response.text


def invite(owner,email,role):
    result=owner.post("/api/workspace/invites",json={"email":email,"role":role})
    assert result.status_code==200,result.text
    return result.json()["token"]


def test_accounts_passwords_sessions_and_logout(workspace):
    db,_=workspace
    client,session=register("OWNER@Example.com")
    assert session["role"]=="owner" and not session["demo"]
    assert "password" not in json.dumps(session)
    assert client.get("/api/dashboard").json()["counts"]["returns"]==0
    assert not client.get("/api/dashboard").json()["products"]
    cookie=client.cookies.get(accounts.SESSION_COOKIE)
    saved=db.all("auth_sessions")[0]
    assert cookie not in json.dumps(saved)
    assert db.all("auth_users")[0]["password_hash"]!=PASSWORD
    assert "HttpOnly" in client.post("/api/auth/login",json={"email":"owner@example.com","password":PASSWORD}).headers["set-cookie"]
    assert client.post("/api/auth/login",json={"email":"owner@example.com","password":"wrong-pass"}).status_code==401
    assert client.get("/api/auth/session").json()["user"]["email"]=="owner@example.com"
    assert client.post("/api/auth/logout").status_code==200
    assert client.get("/api/dashboard").status_code==401
    client.cookies.set(accounts.SESSION_COOKIE,cookie)
    # The older independent session expires server-side as well.
    db.put("auth_sessions",saved["id"],{**saved,"expires":time.time()-1})
    assert client.get("/api/dashboard").status_code==401


def test_merchant_data_and_record_ids_are_isolated(workspace):
    db,_=workspace
    first,a=register("one@example.com","Merchant One")
    second,b=register("two@example.com","Merchant Two")
    upload(first,"Laptop too wide")
    assert second.get("/api/dashboard").json()["counts"]["returns"]==0
    upload(second,"Zipper stuck")
    assert first.get("/api/dashboard").json()["returns"][0]["reason"]=="Laptop too wide"
    assert second.get("/api/dashboard").json()["returns"][0]["reason"]=="Zipper stuck"
    assert second.get("/api/dashboard",headers={"X-Workspace-ID":a["workspace"]["id"]}).status_code==403
    assert first.get("/api/dashboard",headers={"X-Workspace-ID":"demo","X-Demo-Workspace":"true"}).status_code==403
    assert first.get("/api/dashboard",headers={"X-Demo-Workspace":"true"}).json()["counts"]["returns"]==1
    db_a=WorkspaceStore(db,a["workspace"]["id"])
    db_a.put("actions","foreign-action",{"id":"foreign-action","kind":"inspection","product_id":"csv-bag","status":"draft","text":"Review","issue":"Laptop fit"})
    assert second.patch("/api/actions/foreign-action",json={"status":"dismissed","text":"Changed"}).status_code==404
    db_a.put("meta","gemini_last_call",{"id":"gemini_last_call","model":"secret-first-receipt"})
    assert second.get("/api/ai-status").json()["last_call"] is None
    assert len(db.all("returns"))==160  # Existing demo was not migrated into merchant accounts.


@pytest.mark.parametrize("role",["admin","analyst","viewer"])
def test_roles_are_enforced_by_api_and_revocation_is_immediate(workspace,role):
    db,_=workspace
    owner,session=register("owner@example.com")
    code=invite(owner,"teammate@example.com",role)
    member,joined=register("teammate@example.com",token=code)
    assert joined["workspace"]==session["workspace"] and joined["role"]==role
    assert member.get("/api/dashboard").status_code==200
    assert member.get("/api/workspace/members").status_code==(200 if role=="admin" else 403)
    scoped=WorkspaceStore(db,session["workspace"]["id"])
    scoped.put("actions","decision",{"id":"decision","kind":"inspection","product_id":"bag","status":"draft","text":"Review","issue":"Laptop fit"})
    changed=member.patch("/api/actions/decision",json={"status":"dismissed","text":"Inspect before changing"})
    assert changed.status_code==(200 if role=="admin" else 403)
    analysis=member.post("/api/jobs?kind=analysis")
    assert analysis.status_code==(403 if role=="viewer" else 200)
    if role=="viewer":
        assert member.post("/api/chat",json={"question":"What changed?"}).status_code==403
        assert member.post("/api/import",files={"file":("x.csv","x","text/csv")}).status_code==403
        assert member.post("/api/products/bag/propose").status_code==403
    uid=joined["user"]["id"]
    assert owner.patch(f"/api/workspace/members/{uid}",json={"role":"viewer","active":False}).status_code==200
    assert member.get("/api/dashboard").status_code==403
    assert member.get("/api/ai-status").status_code==403


def test_invites_expire_bind_email_and_cannot_escalate(workspace):
    db,_=workspace
    owner,s=register("owner@example.com")
    code=invite(owner,"reader@example.com","viewer")
    wrong,_=register("wrong@example.com")
    assert wrong.post("/api/auth/join",json={"token":code}).status_code==403
    invited,j=register("reader@example.com",token=code)
    assert invited.post("/api/auth/join",json={"token":code}).status_code==403
    assert invited.post("/api/workspace/invites",json={"email":"other@example.com","role":"admin"}).status_code==403
    assert owner.post("/api/workspace/invites",json={"email":"other@example.com","role":"owner"}).status_code==422
    assert owner.patch(f"/api/workspace/members/{s['user']['id']}",json={"role":"viewer","active":False}).status_code==403
    admin,admin_s=register("admin@example.com",token=invite(owner,"admin@example.com","admin"))
    assert admin.post("/api/workspace/invites",json={"email":"boss@example.com","role":"admin"}).status_code==403
    pending=invite(admin,"late@example.com","analyst")
    owner.patch(f"/api/workspace/members/{admin_s['user']['id']}",json={"role":"viewer","active":False})
    assert wrong.post("/api/auth/join",json={"token":pending}).status_code==403
    expiring=invite(owner,"expired@example.com","viewer")
    record=db.get("workspace_invites",accounts.digest(expiring))
    db.put("workspace_invites",record["id"],{**record,"expires":time.time()-1})
    client=TestClient(main.app)
    assert client.post("/api/auth/register",json={"name":"Late","email":"expired@example.com","password":PASSWORD,"invite_token":expiring}).status_code==403
    assert db.get("auth_users","local-"+accounts.digest("expired@example.com")) is None


def test_jobs_and_workers_keep_workspace_context(workspace,monkeypatch):
    db,_=workspace
    owner,s=register("owner@example.com")
    other,_=register("other@example.com")
    upload(owner)
    result=owner.post("/api/jobs?kind=analysis")
    assert result.status_code==200
    job=owner.get("/api/jobs").json()[0]
    assert job["status"]=="complete" and job["result"]["records"]==1
    assert other.get("/api/jobs").json()==[]
    assert other.post(f"/api/jobs/{job['id']}/retry").status_code==404
    assert other.post("/api/jobs?product_id=csv-bag").status_code==404
    captured=[]
    monkeypatch.setenv("CLOUD_TASKS_QUEUE","test")
    monkeypatch.setattr(main,"enqueue",lambda event,workspace:captured.append((event,workspace)))
    queued=owner.post("/api/jobs?kind=analysis").json()
    assert captured==[(queued["event_id"],s["workspace"]["id"])]
    monkeypatch.setattr(main,"SERVICE_ROLE","worker")
    monkeypatch.setenv("K_SERVICE","worker")
    worker=TestClient(main.app)
    assert worker.post("/api/internal/process",json={"event_id":queued["event_id"]}).status_code==404
    assert worker.post("/api/internal/process",json={"event_id":queued["event_id"],"workspace_id":"wrong"}).status_code==404
    assert worker.post("/api/internal/process",json={"event_id":queued["event_id"],"workspace_id":s["workspace"]["id"]}).status_code==200


def test_csv_only_workspaces_and_public_fit_are_scoped(workspace,monkeypatch):
    db,_=workspace
    owner,s=register("owner@example.com")
    other,b=register("other@example.com")
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN","private.myshopify.com")
    monkeypatch.setenv("SHOPIFY_ACCESS_TOKEN","private-token")
    integration=owner.get("/api/integrations").json()
    assert integration["csv_only"] and not integration["shopify"]["configured"]
    assert "private" not in json.dumps(integration)
    assert owner.post("/api/jobs?kind=sync").status_code==409
    upload(owner)
    scoped=WorkspaceStore(db,s["workspace"]["id"])
    scoped.put("actions","fit",{"id":"fit","product_id":"csv-bag","kind":"fit_check","status":"approved","measurements_verified":True,"width":26,"height":37,"depth":3,"text":"Compare your outside dimensions."})
    public=TestClient(main.app)
    path="/api/public/products/csv-bag?workspace="+s["workspace"]["id"]
    data=public.get(path).json()
    assert data["fit_available"] and not {"returns","orders","reason","members"}.intersection(data)
    assert public.get("/api/public/products/csv-bag?workspace="+b["workspace"]["id"]).status_code==404
    assert public.post("/api/public/products/csv-bag/fit?workspace="+s["workspace"]["id"],json={"width":25,"height":36,"depth":2}).json()["status"]=="within_dimensions"


def test_demo_is_explicit_production_fails_closed_and_origin_is_checked(workspace,monkeypatch):
    _,demo=workspace
    anonymous=TestClient(main.app)
    assert anonymous.get("/api/dashboard").status_code==401
    assert demo.get("/api/dashboard").status_code==200
    assert anonymous.post("/api/auth/register",headers={"Origin":"https://evil.example"},json={"name":"Bad","email":"bad@example.com","password":PASSWORD,"workspace_name":"Bad"}).status_code==403
    monkeypatch.setattr(main,"DEMO_MODE",False)
    assert demo.get("/api/dashboard").status_code==401
    assert anonymous.get("/api/public/products/p1").status_code==404
    assert anonymous.post("/api/webhooks/shopify").status_code==404
    monkeypatch.setenv("AUTH_PROVIDER","firebase")
    assert anonymous.get("/api/dashboard").status_code==401
    assert anonymous.post("/api/auth/register",json={"name":"Bad","email":"bad@example.com","password":PASSWORD,"workspace_name":"Bad"}).status_code==409


def test_firebase_identity_does_not_supply_workspace_roles(workspace,monkeypatch):
    import firebase_admin
    from firebase_admin import auth
    db,_=workspace
    monkeypatch.setenv("AUTH_PROVIDER","firebase")
    monkeypatch.setattr(main,"DEMO_MODE",False)
    monkeypatch.setattr(firebase_admin,"_apps",{"test":object()})
    token={"uid":"google-owner","email":"google@example.com","name":"Google Merchant","email_verified":True,"role":"owner"}
    calls=[]
    def verify(value,check_revoked=False):
        calls.append((value,check_revoked))
        return token
    monkeypatch.setattr(auth,"verify_id_token",verify)
    client=TestClient(main.app,headers={"Authorization":"Bearer verified-token"})
    assert client.get("/api/auth/session").json()["needs_workspace"]
    result=client.post("/api/auth/workspace",json={"name":"Google Store"})
    assert result.status_code==200,result.text
    info=result.json()
    scoped=WorkspaceStore(db,info["workspace"]["id"])
    member=scoped.get("members",info["user"]["id"])
    scoped.put("members",member["id"],{**member,"role":"viewer"})
    assert client.get("/api/auth/session").json()["role"]=="viewer"
    assert client.get("/api/dashboard").status_code==200
    assert client.post("/api/jobs?kind=analysis").status_code==403
    assert all(revoked for _,revoked in calls)
    token["email_verified"]=False
    assert client.get("/api/dashboard").status_code==403


@pytest.mark.parametrize("length,expected",[(7,422),(8,200),(15,200),(16,422)])
def test_password_range_applies_to_signup_and_login(workspace,length,expected):
    client=TestClient(main.app)
    password="R"*length
    body={"name":"Merchant","email":"range@example.com","password":password,"workspace_name":"Range Store"}
    response=client.post("/api/auth/register",json=body)
    assert response.status_code==expected,response.text
    response=client.post("/api/auth/login",json={"email":body["email"],"password":password})
    assert response.status_code==expected,response.text
