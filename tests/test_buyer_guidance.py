import json
import pytest
from backend.product_actions import buyer_guide,product_category,fit_supported
from backend.variety import seed_variety
from test_workflows import workspace


@pytest.mark.parametrize("name,category,expected",[
 ("Cotton shirt","Clothing","sizing"),("Running shoes","Footwear","footwear"),
 ("Wireless headphones","Electronics","compatibility"),("Travel mug","Kitchen","care"),
 ("Table lamp","Home","placement"),("Face serum","Beauty","beauty"),
 ("Yoga mat","Fitness","materials"),("Puzzle set","Toys","included"),
])
def test_guidance_matches_product_without_inventing_laptop_fit(name,category,expected):
    product={"name":name,"category":category}
    guide=buyer_guide(product)
    assert guide["guide_type"]==expected
    assert not guide["guidance_verified"]
    assert len(guide["checklist"])>=2
    assert "laptop" not in json.dumps(guide).lower()
    assert not fit_supported(product)


def test_unknown_and_conflicting_names_do_not_override_explicit_product_metadata():
    assert product_category({"name":"Oak Dresser","category":"Home"})=="Home"
    assert product_category({"name":"Travel mug"})=="Kitchen"
    assert buyer_guide({"name":"Unspecified product"})["guide_type"]=="general"
    assert not fit_supported({"name":"Unspecified product"})


def test_bag_category_and_crossbody_names_receive_carry_guidance():
    assert product_category({"name":"Mini Crossbody","category":"Bags"})=="Bags"
    assert buyer_guide({"name":"Mini Crossbody","category":"Bags"})["guide_type"]=="carry"
    assert product_category({"name":"Structured crossbody"})=="Bags"


def test_shoe_guide_requires_verified_approval_and_keeps_feedback_private(workspace):
    db,client=workspace
    seed_variety(db)
    assert client.post("/api/products/demo-sneakers/propose").status_code==200
    guide=db.get("actions","buyer-guide-demo-sneakers")
    assert guide["kind"]=="buyer_guide" and guide["guide_type"]=="footwear"
    assert not any(a["kind"]=="fit_check" for a in db.all("actions") if a["product_id"]=="demo-sneakers")
    public="/api/public/products/demo-sneakers"
    assert client.get(public).json()["purchase_check"] is None
    body={"status":"approved","text":"Compare foot length and width with our verified sizing guide.","checklist":["Review the seller’s size chart.","Confirm the selected shoe size."]}
    assert client.patch("/api/actions/"+guide["id"],json=body).status_code==422
    body["guidance_verified"]=True
    assert client.patch("/api/actions/"+guide["id"],json=body).status_code==200
    result=client.get(public).json()
    assert not result["fit_available"] and not result["fit_check_supported"]
    assert result["purchase_check"]["guide_type"]=="footwear"
    assert result["purchase_check"]["checklist"]==body["checklist"]
    assert not {"returns","orders","reason","evidence_ids","reviewed_by"}.intersection(result["purchase_check"])
    assert "These shoes are too small" not in json.dumps(result)
    # Preparing suggestions again preserves the merchant's approved edits.
    client.post("/api/products/demo-sneakers/propose")
    assert db.get("actions",guide["id"])["text"]==body["text"]
    assert db.get("actions",guide["id"])["status"]=="approved"
    assert client.patch("/api/actions/"+guide["id"],json={"status":"dismissed","text":body["text"]}).status_code==200
    assert client.get(public).json()["purchase_check"] is None


def test_unverified_or_incomplete_guides_are_not_counted_as_live(workspace):
    db,client=workspace
    product={**db.get("products","p1"),"id":"guide-product","name":"Cotton shirt","category":"Clothing","source":"shopify"}
    db.put("products",product["id"],product)
    action={"id":"guide-action","product_id":product["id"],"kind":"buyer_guide","guide_type":"sizing","status":"approved","text":"Check size information.","title":"Size guide","source":"shopify","issue":"Size & fit","checklist":["Check the chest measurement.","Check the selected size."]}
    db.put("actions",action["id"],action)
    assert client.get("/api/dashboard?source=shopify").json()["counts"]["fit_ready"]==0
    db.put("actions",action["id"],{**action,"guidance_verified":True})
    assert client.get("/api/dashboard?source=shopify").json()["counts"]["fit_ready"]==1
    assert client.get("/api/dashboard?source=synthetic").json()["counts"]["fit_ready"]==1
