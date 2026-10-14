"""Accounts, opaque sessions and server-side workspace permissions."""
import hashlib
import hmac
import os
import re
import secrets
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Literal
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .store import WorkspaceStore, now

ROLES = {
    "owner": ["read", "chat", "analyse", "import", "propose", "approve", "members"],
    "admin": ["read", "chat", "analyse", "import", "propose", "approve", "members"],
    "analyst": ["read", "chat", "analyse", "import", "propose"],
    "viewer": ["read"],
}
SESSION_COOKIE = "rr_session"
attempts = defaultdict(deque)


def provider():
    return os.getenv("AUTH_PROVIDER", "firebase" if os.getenv("K_SERVICE") else "local")


class AccountInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=15)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        value=value.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Enter a valid email address.")
        return value


class Registration(AccountInput):
    name: str = Field(min_length=1, max_length=100)
    workspace_name: str = Field(default="", max_length=100)
    invite_token: str = Field(default="", max_length=200)
    workspace_mode: Literal["empty", "sample"] = "empty"

    @field_validator("name")
    @classmethod
    def valid_name(cls, value):
        if not value.strip():
            raise ValueError("Enter your name.")
        return value.strip()


class WorkspaceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="", max_length=100)
    mode: Literal["empty", "sample"] = "empty"


class InviteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)
    role: str = Field(pattern="^(admin|analyst|viewer)$")
    _email = field_validator("email")(AccountInput.valid_email.__func__)


class MemberChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str = Field(pattern="^(admin|analyst|viewer)$")
    active: bool = True


class JoinInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=20, max_length=200)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    value = hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=32768,r=8,p=3,maxmem=64*1024*1024).hex()
    return salt+":"+value


def auth_guard(request):
    key=request.client.host if request.client else "unknown"
    hits=attempts[key]
    current=time.monotonic()
    while hits and current-hits[0]>60:
        hits.popleft()
    if len(hits)>=20:
        raise HTTPException(429,"Too many account requests. Try again in a minute.")
    hits.append(current)


def local_only():
    if provider() != "local":
        raise HTTPException(409,"Use the configured Google sign-in.")


def issue_session(root, uid, response):
    token=secrets.token_urlsafe(48)
    root.create("auth_sessions",digest(token),{"id":digest(token),"uid":uid,"expires":time.time()+8*3600,"revoked":False})
    response.set_cookie(SESSION_COOKIE,token,httponly=True,secure=bool(os.getenv("K_SERVICE")) or os.getenv("AUTH_COOKIE_SECURE")=="true",samesite="strict",max_age=8*3600,path="/")


def identity(root, request):
    if provider()=="local":
        cookie=request.cookies.get(SESSION_COOKIE)
        session=root.get("auth_sessions",digest(cookie)) if cookie else None
        if not session or session["revoked"] or session["expires"]<=time.time():
            raise HTTPException(401,"Sign in to your merchant account.")
        user=root.get("auth_users",session["uid"])
        if not user:
            raise HTTPException(401,"Account unavailable.")
        return user
    authorization=request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        raise HTTPException(401,"Sign in to your merchant account.")
    import firebase_admin
    from firebase_admin import auth
    try:
        if not firebase_admin._apps:
            firebase_admin.initialize_app()
        token=auth.verify_id_token(authorization[7:],check_revoked=True)
    except Exception:
        raise HTTPException(401,"Your session expired. Sign in again.") from None
    if not token.get("email_verified") or not token.get("email"):
        raise HTTPException(403,"Use a verified Google account.")
    uid="firebase-"+token["uid"]
    user=root.get("auth_users",uid)
    if not user:
        user={"id":uid,"email":token["email"].lower(),"name":token.get("name") or token["email"],"created_at":now()}
        root.create("auth_users",uid,user)
    return user


@dataclass
class Access:
    uid: str
    email: str
    name: str
    workspace_id: str
    role: str
    db: object

    def require(self, permission):
        if permission not in ROLES[self.role]:
            raise HTTPException(403,"Your role does not allow this action.")
        return self


def access(root, user, workspace_id):
    db=WorkspaceStore(root,workspace_id)
    membership=db.get("members",user["id"])
    workspace=root.get("workspaces",workspace_id)
    if not workspace or not membership or not membership["active"]:
        raise HTTPException(403,"You do not have access to this workspace.")
    if workspace.get("sample_data") and not workspace.get("sample_ready"):
        raise HTTPException(409,"Your sample store is still being prepared. Choose Explore demo again to finish setup.")
    return Access(user["id"],user["email"],user["name"],workspace_id,membership["role"],db)


def create_workspace(root, user, name="", mode="empty"):
    if mode not in {"empty", "sample"}:
        raise HTTPException(422,"Choose how to start your workspace.")
    if mode == "empty" and not name.strip():
        raise HTTPException(422,"Enter your merchant workspace name.")
    # Each signed-in account has separate merchant and sample namespaces. Names
    # never confer membership, and selecting a sample never touches a merchant's
    # existing workspace or invitation.
    sample=mode == "sample"
    workspace_id=("sample-" if sample else "merchant-")+digest(user["id"])[:32]
    value={"id":workspace_id,"name":name.strip() or "My sample store","owner":user["id"],"created_at":now(),"sample_data":sample}
    if sample:
        value["sample_ready"]=False
    root.create("workspaces",workspace_id,value)
    workspace=root.get("workspaces",workspace_id)
    if not workspace or workspace.get("owner") != user["id"] or bool(workspace.get("sample_data")) != sample:
        raise HTTPException(403,"You do not have access to this workspace.")
    db=WorkspaceStore(root,workspace_id)
    db.create("members",user["id"],{"id":user["id"],"email":user["email"],"name":user["name"],"role":"owner","active":True,"joined_at":now()})
    member=db.get("members",user["id"])
    if not member or not member.get("active") or member.get("role") != "owner":
        raise HTTPException(403,"You do not have access to this workspace.")
    if sample:
        from .workspace_samples import FIXTURE_ID, seed_workspace_samples
        manifest=db.get("meta",FIXTURE_ID)
        if not manifest or manifest.get("status") != "completed":
            try:
                seed_workspace_samples(root,workspace_id,user["id"],apply=True)
            except Exception:
                # A concurrent request may have completed the same create-only
                # seed. Failed partial seeds keep their original dates and can
                # resume without replacing decisions or changing the default.
                manifest=db.get("meta",FIXTURE_ID)
                if not manifest or manifest.get("status") != "completed":
                    raise HTTPException(503,"We couldn't prepare your sample store. Please try Explore demo again.") from None
        manifest=db.get("meta",FIXTURE_ID)
        if not manifest or manifest.get("status") != "completed" or manifest.get("sample_fixture") != FIXTURE_ID or not manifest.get("synthetic"):
            raise HTTPException(503,"We couldn't prepare your sample store. Please try Explore demo again.")
        ready=root.claim("workspaces",workspace_id,lambda record:record.get("owner") == user["id"] and record.get("sample_data"),{"sample_ready":True})
        if not ready:
            raise HTTPException(403,"You do not have access to this workspace.")
    # Publish only a completed sample. Fetch current account fields so retries
    # do not restore stale profile values from an earlier identity lookup.
    root.put("auth_users",user["id"],{**(root.get("auth_users",user["id"]) or user),"default_workspace":workspace_id})
    return workspace_id


def valid_invite(root, token, email):
    invite=root.get("workspace_invites",digest(token))
    if not invite:
        raise HTTPException(403,"This invitation link is no longer valid. Ask the workspace owner for a new link.")
    if invite.get("used"):
        raise HTTPException(403,"This invitation has already been accepted. Sign in to the workspace or ask its owner for a new link.")
    if invite.get("revoked"):
        raise HTTPException(403,"This invitation has been cancelled. Ask the workspace owner for a new link.")
    if invite["expires"]<=time.time():
        raise HTTPException(403,"This invitation has expired. Ask the workspace owner for a new link.")
    if invite["email"]!=email.strip().lower():
        raise HTTPException(403,"This invitation was sent to a different email address. Sign out and use the invited email.")
    if not root.get("workspaces",invite["workspace_id"]):
        raise HTTPException(403,"This workspace is no longer available. Ask the person who invited you for help.")
    creator=WorkspaceStore(root,invite["workspace_id"]).get("members",invite["created_by"])
    if not creator or not creator["active"] or "members" not in ROLES.get(creator["role"],[]) or (invite["role"]=="admin" and creator["role"]!="owner"):
        raise HTTPException(403,"The person who invited you can no longer add this teammate. Ask the workspace owner for a new link.")
    return invite


def join_workspace(root, user, token):
    invite=valid_invite(root,token,user["email"])
    db=WorkspaceStore(root,invite["workspace_id"])
    existing=db.get("members",user["id"])
    if existing and existing["active"]:
        raise HTTPException(409,"You are already a member of this workspace.")
    claimed=root.claim("workspace_invites",invite["id"],lambda i:not i.get("used") and not i.get("revoked") and i["expires"]>time.time(),{"used":True,"accepted_by":user["id"]})
    if not claimed:
        raise HTTPException(409,"This invitation was already used or revoked.")
    db.put("members",user["id"],{"id":user["id"],"email":user["email"],"name":user["name"],"role":invite["role"],"active":True,"joined_at":now()})
    root.put("auth_users",user["id"],{**user,"default_workspace":invite["workspace_id"]})
    return invite["workspace_id"]


def session_info(root, current):
    workspace={"id":"demo","name":"Everyday Goods"} if current.workspace_id=="demo" else root.get("workspaces",current.workspace_id)
    return {"user":{"id":current.uid,"name":current.name,"email":current.email},"workspace":{"id":workspace["id"],"name":workspace["name"],"sample_data":bool(workspace.get("sample_data")) or current.workspace_id=="demo"},"role":current.role,"permissions":ROLES[current.role],"demo":current.workspace_id=="demo"}


def audit(current, action, target):
    key=uuid.uuid4().hex
    current.db.create("audit",key,{"id":key,"actor":current.uid,"action":action,"target":target,"at":now()})
