"""Opt-in samples belong to one account and never replace merchant data."""
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from backend import accounts, main, workspace_samples
from backend.store import WorkspaceStore
from backend.workspace_samples import COLLECTIONS, FIXTURE_ID, PREFIX
from test_accounts import PASSWORD, invite, register, upload
from test_workflows import workspace


def sample(client, name=""):
    result=client.post("/api/auth/workspace",json={"mode":"sample","name":name})
    assert result.status_code==200,result.text
    return result.json()


def test_empty_accounts_have_no_automatic_samples_and_names_do_not_join(workspace):
    root,_=workspace
    first,a=register("first@example.com","The same store")
    second,b=register("second@example.com","The same store")
    assert a["workspace"]["id"] != b["workspace"]["id"]
    assert a["workspace"]["name"] == b["workspace"]["name"]
    assert not a["workspace"]["sample_data"] and not b["workspace"]["sample_data"]
    for client,session in ((first,a),(second,b)):
        scoped=WorkspaceStore(root,session["workspace"]["id"])
        assert all(not scoped.all(collection) for collection in COLLECTIONS)
        assert client.get("/api/dashboard").json()["counts"]["returns"]==0
        assert client.get("/api/auth/workspaces").json()==[{**session["workspace"],"role":"owner"}]
    assert second.get("/api/dashboard",headers={"X-Workspace-ID":a["workspace"]["id"]}).status_code==403


def test_local_registration_can_explicitly_choose_private_varied_samples(workspace):
    root,_=workspace
    client=TestClient(main.app)
    response=client.post("/api/auth/register",json={"name":"Judge","email":"judge@example.com","password":PASSWORD,"workspace_mode":"sample"})
    assert response.status_code==200,response.text
    session=response.json()
    assert session["workspace"]["name"]=="My sample store"
    assert session["workspace"]["id"].startswith("sample-")
    assert session["workspace"]["sample_data"] and not session["demo"]
    scoped=WorkspaceStore(root,session["workspace"]["id"])
    assert {collection:len(scoped.all(collection)) for collection in COLLECTIONS}=={
        "products":18,"snapshots":10,"orders":1336,"returns":256,"actions":30,"review_cycles":3}
    assert len({product["category"] for product in scoped.all("products")})==9
    assert all(action["status"]=="draft" for action in scoped.all("actions"))
    assert all(cycle["status"]=="planned" and not cycle["completed_at"] for cycle in scoped.all("review_cycles"))
    assert not scoped.all("ai_calls") and not scoped.all("analysis_reports")
    assert scoped.get("meta","analysis") is None
    assert client.get("/api/dashboard").json()["counts"]["returns"]==256
    assert client.get("/api/auth/session").json()["workspace"]==session["workspace"]


def test_each_account_has_its_own_sample_data_and_decisions(workspace):
    root,_=workspace
    first,_=register("first@example.com")
    second,_=register("second@example.com")
    a=sample(first,"Shared sample name")
    b=sample(second,"Shared sample name")
    assert a["workspace"]["id"] != b["workspace"]["id"]
    scoped_a=WorkspaceStore(root,a["workspace"]["id"])
    scoped_b=WorkspaceStore(root,b["workspace"]["id"])
    action_id=PREFIX+"buyer-guide-demo-sneakers"
    changed=first.patch("/api/actions/"+action_id,json={"status":"dismissed","text":"My own assessment."})
    assert changed.status_code==200,changed.text
    assert scoped_a.get("actions",action_id)["status"]=="dismissed"
    assert scoped_b.get("actions",action_id)["status"]=="draft"
    scoped_a.put("meta","gemini_last_call",{"id":"gemini_last_call","model":"first-account-receipt"})
    assert second.get("/api/ai-status").json()["last_call"] is None
    assert second.get("/api/dashboard",headers={"X-Workspace-ID":a["workspace"]["id"]}).status_code==403
    assert all(item["id"]!=a["workspace"]["id"] for item in second.get("/api/auth/workspaces").json())


def test_start_my_workspace_leaves_sample_and_preserves_both(workspace):
    root,_=workspace
    client,merchant=register("owner@example.com","My actual store")
    upload(client)
    business=WorkspaceStore(root,merchant["workspace"]["id"])
    business.put("connector_credentials","kept",{"id":"kept","private":"encrypted-placeholder"})
    before={collection:deepcopy(business.all(collection)) for collection in (*COLLECTIONS,"members","connector_credentials")}
    demo=sample(client)
    assert demo["workspace"]["id"]!=merchant["workspace"]["id"]
    returned=client.post("/api/auth/workspace",json={"name":"Attempt to rename","mode":"empty"})
    assert returned.status_code==200,returned.text
    assert returned.json()["workspace"]==merchant["workspace"]
    assert {collection:business.all(collection) for collection in before}==before
    assert client.get("/api/dashboard").json()["counts"]["returns"]==1
    assert len(WorkspaceStore(root,demo["workspace"]["id"]).all("returns"))==256
    assert len(client.get("/api/auth/workspaces").json())==2


def test_resume_completed_samples_does_not_reseed_or_replace_edits(workspace,monkeypatch):
    root,_=workspace
    client,_=register("owner@example.com")
    first=sample(client)
    scoped=WorkspaceStore(root,first["workspace"]["id"])
    action=scoped.get("actions",PREFIX+"buyer-guide-demo-sneakers")
    action.update(status="dismissed",text="My decision remains.")
    scoped.put("actions",action["id"],action)
    upload(client)
    before={collection:scoped.all(collection) for collection in COLLECTIONS}
    def unexpected(*args,**kwargs):
        raise AssertionError("Completed samples must not be seeded again.")
    monkeypatch.setattr(workspace_samples,"seed_workspace_samples",unexpected)
    second=sample(client,"Rename attempt")
    assert second["workspace"]==first["workspace"]
    assert {collection:scoped.all(collection) for collection in COLLECTIONS}==before


def test_invitations_join_only_the_merchant_workspace_even_if_sample_selected(workspace):
    root,_=workspace
    owner,merchant=register("owner@example.com")
    token=invite(owner,"judge@example.com","viewer")
    invited=TestClient(main.app)
    response=invited.post("/api/auth/register",json={"name":"Judge","email":"judge@example.com","password":PASSWORD,"workspace_mode":"sample","invite_token":token})
    assert response.status_code==200,response.text
    joined=response.json()
    assert joined["workspace"]==merchant["workspace"] and joined["role"]=="viewer"
    business=WorkspaceStore(root,merchant["workspace"]["id"])
    assert not business.all("products")
    assert business.get("meta",FIXTURE_ID) is None
    assert len(root.all("workspaces"))==1
    # Opting into a separate private sample later grants no role in the merchant.
    private=sample(invited)
    assert private["role"]=="owner" and private["workspace"]["sample_data"]
    assert invited.get("/api/auth/session",headers={"X-Workspace-ID":merchant["workspace"]["id"]}).json()["role"]=="viewer"


def test_partial_sample_seed_keeps_existing_default_and_resumes(workspace,monkeypatch):
    root,_=workspace
    client,merchant=register("owner@example.com")
    original=workspace_samples._create_missing
    calls=[]
    def interrupted(db,collection,values):
        calls.append(collection)
        if collection=="orders":
            raise RuntimeError("Interrupted write")
        return original(db,collection,values)
    monkeypatch.setattr(workspace_samples,"_create_missing",interrupted)
    failed=client.post("/api/auth/workspace",json={"mode":"sample"})
    assert failed.status_code==503 and "Explore demo again" in failed.json()["detail"]
    user=root.get("auth_users",merchant["user"]["id"])
    assert user["default_workspace"]==merchant["workspace"]["id"]
    pending_id="sample-"+accounts.digest(user["id"])[:32]
    pending=WorkspaceStore(root,pending_id)
    manifest=pending.get("meta",FIXTURE_ID)
    assert manifest["status"]=="pending" and len(pending.all("products"))==18
    assert client.get("/api/dashboard",headers={"X-Workspace-ID":pending_id}).status_code==409
    assert len(client.get("/api/auth/workspaces").json())==1
    monkeypatch.setattr(workspace_samples,"_create_missing",original)
    resumed=sample(client)
    assert resumed["workspace"]["id"]==pending_id
    assert pending.get("meta",FIXTURE_ID)["at"]==manifest["at"]
    assert pending.get("meta",FIXTURE_ID)["status"]=="completed"
    assert len(pending.all("orders"))==1336 and len(pending.all("returns"))==256
    assert root.get("auth_users",user["id"])["default_workspace"]==pending_id


def test_local_sample_registration_failure_keeps_recoverable_login(workspace,monkeypatch):
    root,_=workspace
    original=workspace_samples.seed_workspace_samples
    monkeypatch.setattr(workspace_samples,"seed_workspace_samples",lambda *a,**kw: (_ for _ in ()).throw(RuntimeError("Interrupted")))
    client=TestClient(main.app)
    result=client.post("/api/auth/register",json={"name":"Judge","email":"judge@example.com","password":PASSWORD,"workspace_mode":"sample"})
    assert result.status_code==503,result.text
    assert client.cookies.get(accounts.SESSION_COOKIE)
    assert client.get("/api/auth/session").json()["needs_workspace"]
    assert "default_workspace" not in root.get("auth_users","local-"+accounts.digest("judge@example.com"))
    monkeypatch.setattr(workspace_samples,"seed_workspace_samples",original)
    assert sample(client)["workspace"]["sample_data"]


def test_firebase_onboarding_is_explicit_private_and_empty_is_separate(workspace,monkeypatch):
    import firebase_admin
    from firebase_admin import auth
    root,_=workspace
    monkeypatch.setenv("AUTH_PROVIDER","firebase")
    monkeypatch.setattr(main,"DEMO_MODE",False)
    monkeypatch.setattr(firebase_admin,"_apps",{"test":object()})
    tokens={"first":{"uid":"judge-one","email":"one@example.com","name":"Judge One","email_verified":True},
            "second":{"uid":"judge-two","email":"two@example.com","name":"Judge Two","email_verified":True}}
    monkeypatch.setattr(auth,"verify_id_token",lambda value,check_revoked:tokens[value])
    first=TestClient(main.app,headers={"Authorization":"Bearer first"})
    second=TestClient(main.app,headers={"Authorization":"Bearer second"})
    assert first.get("/api/auth/session").json()["needs_workspace"]
    assert not root.all("workspaces")
    assert not first.get("/api/config").json()["demo"]
    a=sample(first)
    b=sample(second)
    assert a["workspace"]["id"]!=b["workspace"]["id"]
    assert second.get("/api/dashboard",headers={"X-Workspace-ID":a["workspace"]["id"]}).status_code==403
    empty=first.post("/api/auth/workspace",json={"name":"My real store","mode":"empty"})
    assert empty.status_code==200 and not empty.json()["workspace"]["sample_data"]
    assert first.get("/api/dashboard").json()["counts"]["returns"]==0
    assert second.get("/api/dashboard").json()["counts"]["returns"]==256


@pytest.mark.parametrize("payload",[{"mode":"sample","workspace_id":"foreign"},{"mode":"sample","owner_id":"foreign"},{"mode":"unknown"},{"mode":"empty"}])
def test_workspace_selection_cannot_accept_foreign_ids_or_invalid_choices(workspace,payload):
    client,_=register("owner@example.com")
    assert client.post("/api/auth/workspace",json=payload).status_code==422


def test_sample_creation_requires_verified_identity_and_cannot_restore_revoked_roles(workspace):
    root,_=workspace
    anonymous=TestClient(main.app)
    assert anonymous.post("/api/auth/workspace",json={"mode":"sample"}).status_code==401
    client,_=register("owner@example.com")
    session=sample(client)
    scoped=WorkspaceStore(root,session["workspace"]["id"])
    member=scoped.get("members",session["user"]["id"])
    scoped.put("members",member["id"],{**member,"active":False})
    assert client.post("/api/auth/workspace",json={"mode":"sample"}).status_code==403
    assert not scoped.get("members",member["id"])["active"]
