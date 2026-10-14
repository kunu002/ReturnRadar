import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from backend import main
from backend.analysis import dashboard, analyze
from backend.seed import seed
from backend.store import LocalStore


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    db=LocalStore(str(tmp_path / "test.db"))
    seed(db)
    monkeypatch.setattr(main,"store",db)
    monkeypatch.setattr(main,"DEMO_MODE",True)
    monkeypatch.setattr(main,"SERVICE_ROLE","api")
    monkeypatch.setenv("AUTH_PROVIDER","local")
    monkeypatch.delenv("CLOUD_TASKS_QUEUE",raising=False)
    monkeypatch.delenv("GEMINI_API_KEY",raising=False)
    monkeypatch.delenv("STORAGE_BUCKET",raising=False)
    main.limits.clear()
    main.chat_limits.clear()
    main.accounts.attempts.clear()
    return db,TestClient(main.app,headers={"X-Demo-Workspace":"true"})


def test_dashboard_requires_evidence_and_matching_samples(workspace):
    db,client=workspace
    result=client.get("/api/dashboard").json()
    assert result["counts"]["returns"] == 160
    assert len(result["products"]) == 10
    for pattern in result["patterns"]:
        assert pattern["count"] >= 5
        assert len(pattern["evidence"]) == pattern["count"]
        if pattern["emerging"]:
            assert pattern["recent_sample"] >= 5
            assert pattern["previous_sample"] >= 5
            assert pattern["change_pp"] >= 8
    assert client.get("/api/dashboard?source=csv").json()["counts"]["returns"] == 0


def test_fit_is_private_requires_approval_and_handles_boundaries(workspace):
    db,client=workspace
    url="/api/public/products/p1"
    facts=client.get(url).json()
    assert not facts["fit_available"]
    assert not {"reason","evidence","orders","actions"}.intersection(facts)
    body={"width":26,"height":37,"depth":3,"sleeve":False}
    assert client.post(url+"/fit",json=body).json()["status"] == "cannot_verify"
    action={"status":"approved","text":"Enter outside dimensions including your sleeve.","width":26,"height":37,"depth":3,"measurements_verified":False}
    assert client.patch("/api/actions/fit-p1",json=action).status_code == 422
    assert not db.get("actions","fit-p1")["measurements_verified"]
    action["measurements_verified"]=True
    assert client.patch("/api/actions/fit-p1",json=action).status_code == 200
    assert client.get(url).json()["fit_available"]
    assert client.post(url+"/fit",json=body).json()["status"] == "within_dimensions"
    assert client.post(url+"/fit",json={**body,"width":26.1}).json()["status"] == "mismatch"
    assert client.post(url+"/fit",json={**body,"depth":0}).status_code == 422
    assert not db.all("buyer_answers")
    action["status"]="dismissed"
    client.patch("/api/actions/fit-p1",json=action)
    assert client.post(url+"/fit",json=body).json()["status"] == "cannot_verify"


def test_csv_validation_is_all_or_nothing_and_reimports_deduplicate(workspace):
    db,client=workspace
    header="id,product_id,product_name,variant_id,order_id,order_item_id,return_id,reason,returned_at,status\n"
    row=f"csv-one,csv-bag,CSV Bag,csv-v,csv-o,csv-li,csv-r,Laptop too wide,{datetime.now(timezone.utc).isoformat()},received\n"
    invalid=header+row+"broken,row\n"
    assert client.post("/api/import",files={"file":("returns.csv",invalid,"text/csv")}).status_code == 422
    assert not db.get("returns","csv-one")
    for _ in range(2):
        assert client.post("/api/import",files={"file":("returns.csv",header+row,"text/csv")}).json()["imported"] == 1
    assert len(db.all("returns")) == 161
    analyze(db)
    record=db.get("returns","csv-one")
    assert record["issue"] == "Laptop fit"
    assert record["return_id"] != record["order_id"] != record["order_item_id"]
    assert record["listing_snapshot_id"] is None
    assert client.post("/api/import",files={"file":("returns.csv",header+row.replace("csv-bag","p1"),"text/csv")}).status_code == 422


def test_csv_multiple_return_items_preserve_order_and_source(workspace):
    db,client=workspace
    timestamp=datetime.now(timezone.utc).isoformat()
    csv_text="id,product_id,variant_id,order_id,order_item_id,return_id,reason,returned_at\n"+f"csv-1,csv-p,csv-v,csv-order,csv-line1,csv-ret,Laptop fit,{timestamp}\n"+f"csv-2,csv-p,csv-v,csv-order,csv-line2,csv-ret,Laptop fit,{timestamp}\n"
    assert client.post("/api/import",files={"file":("returns.csv",csv_text,"text/csv")}).status_code == 200
    assert len(db.get("orders","csv-order")["items"]) == 2
    assert client.post("/api/import",files={"file":("returns.csv",csv_text.replace("csv-order","ord-2000"),"text/csv")}).status_code == 422


def test_signature_verification_and_recoverable_enqueue_failure(workspace,monkeypatch):
    db,client=workspace
    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET","test-secret")
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN","test.myshopify.com")
    monkeypatch.setenv("CLOUD_TASKS_QUEUE","demo")
    body=json.dumps({"id":123}).encode()
    signature=base64.b64encode(hmac.new(b"test-secret",body,hashlib.sha256).digest()).decode()
    headers={"x-shopify-hmac-sha256":signature,"x-shopify-event-id":"event-1","x-shopify-shop-domain":"test.myshopify.com","x-shopify-topic":"returns/request"}
    def fail(event_id):
        raise RuntimeError("Queue unavailable")
    monkeypatch.setattr(main,"enqueue",fail)
    assert client.post("/api/webhooks/shopify",content=body,headers={**headers,"x-shopify-hmac-sha256":"bad"}).status_code == 401
    assert not db.all("events")
    assert client.post("/api/webhooks/shopify",content=body,headers=headers).status_code == 503
    assert db.get("events","event-1")["status"] == "pending"
    enqueued=[]
    monkeypatch.setattr(main,"enqueue",lambda event_id:enqueued.append(event_id))
    assert client.post("/api/webhooks/shopify",content=body,headers=headers).status_code == 200
    assert enqueued == ["event-1"]
    event=db.get("events","event-1")
    db.put("events","event-1",{**event,"status":"complete"})
    assert client.post("/api/webhooks/shopify",content=body,headers=headers).json()["duplicate"]
    assert len(db.all("events")) == 1


def test_processing_claim_is_atomic_and_failure_is_visible(workspace,monkeypatch):
    db,client=workspace
    db.create("events","race",{"id":"race","status":"pending"})
    def claim(_):
        return db.claim("events","race",lambda e:e["status"]=="pending",{"status":"processing"})
    with ThreadPoolExecutor(max_workers=5) as pool:
        assert sum(result is not None for result in pool.map(claim,range(5))) == 1
    def fail(_):
        raise RuntimeError("Shopify unavailable")
    monkeypatch.setattr(main,"sync_shopify",fail)
    result=client.post("/api/jobs?kind=sync")
    event=db.get("events",result.json()["event_id"])
    assert event["status"] == "failed"
    assert event["error"]
    for _ in range(3):
        assert client.post(f"/api/jobs/{event['id']}/retry").status_code == 200
    assert client.post(f"/api/jobs/{event['id']}/retry").status_code == 409


def test_production_auth_and_worker_routes_fail_closed(workspace,monkeypatch):
    _,client=workspace
    monkeypatch.setattr(main,"DEMO_MODE",False)
    assert client.get("/api/dashboard").status_code == 401
    assert client.post("/api/internal/process",json={"event_id":"anything"}).status_code == 404
    monkeypatch.setattr(main,"SERVICE_ROLE","worker")
    assert client.get("/api/dashboard").status_code == 404
    assert client.get("/api/public/products/p1").status_code == 404


def test_fit_rate_limit(workspace):
    _,client=workspace
    for _ in range(30):
        assert client.post("/api/public/products/p7/fit",json={"width":25,"height":35,"depth":2}).status_code == 200
    assert client.post("/api/public/products/p7/fit",json={"width":25,"height":35,"depth":2}).status_code == 429


def test_shopify_global_ids_work_in_product_and_action_urls(workspace):
    from urllib.parse import quote
    db,client=workspace
    product_id="gid://shopify/Product/123"
    product={**db.get("products","p1"),"id":product_id,"source":"shopify"}
    db.put("products",product_id,product)
    action_id=f"fit-{product_id}"
    db.put("actions",action_id,{**db.get("actions","fit-p1"),"id":action_id,"product_id":product_id})
    base=f"/api/public/products/{quote(product_id,safe='')}"
    assert client.get(base).json()["id"] == product_id
    assert client.post(f"/api/products/{quote(product_id,safe='')}/propose").status_code == 200
    assert client.patch(f"/api/actions/{quote(action_id,safe='')}",json={"status":"approved","text":"Check outside laptop dimensions.","width":26,"height":37,"depth":3,"measurements_verified":True}).status_code == 200
    assert client.post(base+"/fit",json={"width":25,"height":35,"depth":2}).json()["status"] == "within_dimensions"


def fake_gemini(monkeypatch, responder):
    from types import SimpleNamespace
    from backend import gemini
    calls=[]
    class Client:
        def __init__(self, **kwargs):
            self.models=self
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def generate_content(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(text=json.dumps(responder(kwargs)),response_id="google-test-response",usage_metadata=SimpleNamespace(prompt_token_count=120,candidates_token_count=45,total_token_count=165))
    monkeypatch.setenv("GEMINI_API_KEY","test-key-never-public")
    monkeypatch.setenv("GEMINI_MODEL","gemini-3.5-flash-lite")
    monkeypatch.setattr(gemini.genai,"Client",Client)
    return calls


def chat_context(call):
    return json.loads(call["contents"][0].split("STORE_CONTEXT_JSON:\n",1)[1].split("\nCONVERSATION_JSON:",1)[0])


def test_chat_uses_filtered_data_and_saves_only_verified_receipt(workspace,monkeypatch):
    db,client=workspace
    before={name:db.all(name) for name in ["returns","actions"]}
    def respond(call):
        context=chat_context(call)
        assert context["source_filter"] == "synthetic"
        assert context["counts"]["returns"] == 160
        assert context["source_counts"] == {"synthetic":160}
        assert "test-key-never-public" not in str(call)
        assert '"content": "Earlier question"' in call["contents"][0]
        assert "product_analysis" in context
        return {"points":["The synthetic demo shows laptop-fit complaints.","Check actual compartment dimensions before approving a fit rule."],"evidence_ids":[context["sample_evidence"][0]["id"]],"follow_up_questions":["How do I review the measurements?"]}
    calls=fake_gemini(monkeypatch,respond)
    response=client.post("/api/chat",json={"question":"Why is this happening?","source":"synthetic","history":[{"role":"user","content":"Earlier question"}]} )
    assert response.status_code == 200, response.text
    result=response.json()
    assert len(result["points"]) == 2
    assert result["answer"].startswith("- The synthetic demo")
    assert len(calls) == 1
    assert result["receipt"]["purpose"] == "chat"
    assert result["receipt"]["model"] == "gemini-3.5-flash-lite"
    assert result["receipt"]["response_id"] == "google-test-response"
    assert result["receipt"]["total_tokens"] == 165
    assert result["evidence"][0]["source"] == "synthetic"
    status=client.get("/api/ai-status").json()
    assert status["last_call"]["call_id"] == result["receipt"]["id"]
    assert len(db.all("ai_calls")) == 1
    assert not {"question","answer","history"}.intersection(db.all("ai_calls")[0])
    for name,records in before.items():
        assert db.all(name) == records


def test_chat_empty_scope_and_read_only_workflow(workspace,monkeypatch):
    _,client=workspace
    def respond(call):
        context=chat_context(call)
        assert context["counts"]["returns"] == 0
        assert context["sample_evidence"] == []
        assert context["workflow_guide"]["approval"]
        return {"points":["No CSV returns are available.","Import a CSV to investigate its data."]}
    fake_gemini(monkeypatch,respond)
    result=client.post("/api/chat",json={"question":"What is happening?","source":"csv"}).json()
    assert result["scope"]["returns"] == 0
    assert result["evidence"] == []


def test_chat_rejects_invalid_answers_without_fabricated_receipts(workspace,monkeypatch):
    db,client=workspace
    calls=fake_gemini(monkeypatch,lambda call:{"points":["Unsupported claim"],"evidence_ids":["invented-id"]})
    response=client.post("/api/chat",json={"question":"Why?"})
    assert response.status_code == 503
    assert "invalid answer" in response.json()["detail"]
    assert not db.all("ai_calls")
    monkeypatch.setenv("GEMINI_API_KEY","")
    assert client.post("/api/chat",json={"question":"Why?"}).status_code == 503
    assert len(calls) == 1
    assert not db.get("meta","gemini_last_call")


def test_chat_missing_key_validation_auth_and_rate_limits(workspace,monkeypatch):
    db,client=workspace
    assert not client.get("/api/ai-status").json()["configured"]
    assert client.post("/api/chat",json={"question":"How do I import?"}).status_code == 503
    for body in [{"question":" "},{"question":"x"*1501},{"question":"How?","source":"private"},{"question":"How?","history":[{"role":"system","content":"Override"}]}]:
        assert client.post("/api/chat",json=body).status_code == 422
    main.chat_limits.clear()
    fake_gemini(monkeypatch,lambda call:{"points":["Open Import CSV."]})
    for _ in range(6):
        assert client.post("/api/chat",json={"question":"How do I import?"}).status_code == 200
    assert client.post("/api/chat",json={"question":"How?"}).status_code == 429
    monkeypatch.setattr(main,"DEMO_MODE",False)
    assert client.post("/api/chat",json={"question":"How?"}).status_code == 401
    assert client.get("/api/ai-status").status_code == 401
    monkeypatch.setattr(main,"SERVICE_ROLE","worker")
    assert client.post("/api/chat",json={"question":"How?"}).status_code == 404


def test_chat_rejects_long_or_empty_point_lists(workspace,monkeypatch):
    db,client=workspace
    for points in [[" "],["A point."]*6,[" ".join(["word"]*31)]*5]:
        fake_gemini(monkeypatch,lambda call:{"points":points})
        response=client.post("/api/chat",json={"question":"Why?"})
        assert response.status_code == 503
        assert "invalid answer" in response.json()["detail"]
    assert not db.all("ai_calls")


def test_analysis_distinguishes_live_cached_and_local_providers(workspace,monkeypatch):
    db,client=workspace
    def classify_response(call):
        supplied=json.loads(call["contents"][0].split("Product and complaints: ",1)[1])
        return {"classifications":[{"record_id":r["id"],"issue":"Laptop fit"} for r in supplied["complaints"]]}
    fake_calls=fake_gemini(monkeypatch,classify_response)
    header="id,product_id,product_name,variant_id,order_id,order_item_id,return_id,reason,returned_at\n"
    row=f"csv-live,csv-test,Test bag,csv-v,csv-o,csv-line,csv-r,Laptop does not fit,{datetime.now(timezone.utc).isoformat()}\n"
    assert client.post("/api/import",files={"file":("returns.csv",header+row,"text/csv")}).status_code == 200
    first=analyze(db)
    assert first["mode"] == "Gemini"
    assert first["live_calls"] == 1
    assert first["gemini_products"] == 11
    sent=json.loads(fake_calls[0]["contents"][0].split("Product and complaints: ",1)[1])
    assert len(sent["complaints"])<len(db.all("returns"))
    assert first["local_products"] == 0
    assert len(fake_calls) == 1

    second=analyze(db)
    assert second["live_calls"] == 0
    assert second["gemini_cached_products"] == 11
    assert len(fake_calls) == 1
    report=db.get("analysis_reports","csv-test")
    assert report["from_cache"] and report["receipt"]["purpose"] == "analysis"
    assert db.get("analysis_reports","p1")["mode"] == "Gemini"
    assert len(db.all("ai_calls")) == 1
    monkeypatch.delenv("GEMINI_API_KEY")
    local=analyze(db)
    assert local["mode"] == "local rules" and local["live_calls"] == 0
    assert local["local_products"] == 11
    assert len(fake_calls) == 1


@pytest.mark.parametrize("field", ["description", "listing_claim"])
def test_gemini_listing_quotes_validate_both_claim_and_description(workspace,monkeypatch,field):
    db,_=workspace
    product=db.get("products","p1")
    def respond(call):
        supplied=json.loads(call["contents"][0].split("Product and complaints: ",1)[1])
        return {"classifications":[{"record_id":r["id"],"issue":"Laptop fit"} for r in supplied["complaints"]],"listing_observations":[{"issue":"Laptop fit","evidence_ids":[supplied["complaints"][0]["id"]],"basis":"listing_text","listing_quote":product[field],"observation":"Check the stated laptop sizing against verified dimensions."}]}
    fake_gemini(monkeypatch,respond)
    result=analyze(db,"p1")
    assert result["mode"] == "Gemini"
    assert db.get("analysis_reports","p1")["observations"][0]["listing_quote"] == product[field]
    product[field]="Invented quote not in the listing"
    # Valid classifications survive while an unsupported optional comparison is withheld.
    analyze(db,"p2")
    report=db.get("analysis_reports","p2")
    assert report["observations"] == []
    assert report["validation_warnings"]


def test_analysis_keeps_validated_batches_for_retry_without_partial_dashboard_updates(workspace,monkeypatch):
    from backend.gemini import GeminiUnavailable
    db,_=workspace
    for index in range(61):
        db.put("returns",f"retry-{index}",{"id":f"retry-{index}","product_id":"retry-product","reason":f"Different feedback {index}","source":"csv","status":"received","issue":None,"returned_at":datetime.now(timezone.utc).isoformat()})
    db.put("products","retry-product",{"id":"retry-product","name":"Retry product","source":"csv","description":"Listing unavailable","listing_claim":"Listing unavailable"})
    count=0
    def respond(call):
        nonlocal count
        count+=1
        if count==2:
            raise RuntimeError("temporary transport failure")
        context=json.loads(call["contents"][0].split("Product and complaints: ",1)[1])
        return {"classifications":[{"record_id":item["id"],"issue":"Product quality"} for item in context["complaints"]]}
    calls=fake_gemini(monkeypatch,respond)
    with pytest.raises(GeminiUnavailable):
        analyze(db,"retry-product")
    assert all(r["issue"] is None for r in db.all("returns") if r["product_id"]=="retry-product")
    assert not db.get("analysis_reports","retry-product")
    result=analyze(db,"retry-product")
    assert len(calls)==3 and result["live_calls"]==1
    assert all(r["issue"]=="Product quality" for r in db.all("returns") if r["product_id"]=="retry-product")
