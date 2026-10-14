"""Provision an explicit local UI test account without changing another workspace."""
from . import accounts
from .seed import seed
from .store import WorkspaceStore, now


def create_demo_account(root, email, password, name="UI Demo Merchant"):
    body=accounts.Registration(email=email,password=password,name="Demo reviewer",workspace_name=name)
    uid="local-"+accounts.digest(body.email)
    user=root.get("auth_users",uid)
    created=False
    if user and not user.get("ui_test_account"):
        raise ValueError("This email already belongs to another account. Choose a different test email.")
    if not user:
        user={"id":uid,"email":body.email,"name":body.name,"password_hash":accounts.password_hash(body.password),"created_at":now(),"ui_test_account":True}
        if not root.create("auth_users",uid,user):
            raise ValueError("Account was created concurrently. Run again to reuse it.")
        created=True
    workspace_id=user.get("default_workspace") or accounts.create_workspace(root,user,name)
    db=WorkspaceStore(root,workspace_id)
    member=db.get("members",uid)
    if not member or member["role"]!="owner" or not member["active"]:
        raise ValueError("The test account does not have active owner access. No membership was changed.")
    seed(db)
    db.put("meta","ui_test_data",{"id":"ui_test_data","synthetic":True,"at":now(),"message":"Labelled synthetic records for evaluating the UI; not merchant sales."})
    return {"email":body.email,"workspace_id":workspace_id,"created":created,"products":len(db.all("products")),"returns":len(db.all("returns"))}
