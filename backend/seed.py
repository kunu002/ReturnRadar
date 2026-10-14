from datetime import datetime, timedelta, timezone
import random
from .store import now

NAMES = ["Everyday Backpack", "Weekender Duffle", "Metro Sling", "Classic Daypack", "Trail Explorer", "Everyday Tote", "Commuter Pro", "Mini Crossbody", "Carryall Duffle", "Rolltop Backpack"]
COLORS = ["Olive", "Sand", "Black", "Navy", "Forest", "Cream", "Charcoal", "Rust", "Black", "Slate"]


def seed(store):
    if store.get("meta", "initialized"):
        return
    rng = random.Random(42)
    today = datetime.now(timezone.utc).replace(microsecond=0)
    for index, name in enumerate(NAMES):
        key = f"p{index+1}"
        product = {"id": key, "name": name, "variant": COLORS[index], "sku": f"RR-{100+index}", "price": [2499, 3299, 1499, 2199, 4499, 1799, 3799, 1299, 2999, 3499][index], "description": "A daily carry with water-resistant fabric, thoughtful pockets, and a padded laptop compartment." if index in [0,3,6,9] else "Thoughtful organization and durable fabric for your daily essentials.", "listing_claim": "Fits laptops up to 15.6 inches" if index == 0 else "Made for everyday carry", "source": "synthetic", "image": "/backpack.jpg" if index in [0,3,4,6,9] else None}
        store.put("products", key, product)
        store.put("snapshots", f"seed-{key}", {"id": f"seed-{key}", "product_id": key, "captured_at": now(), "description": product["description"], "listing_claim": product["listing_claim"], "source": "synthetic"})
    complaints = {"Laptop fit": ["My 15.6-inch laptop doesn't fit with its sleeve on.", "Laptop compartment is narrower than I expected.", "Listing said 15.6 inch but my laptop is too wide."], "Zipper quality": ["The main zipper gets stuck near the top.", "Zipper broke after a week of use.", "Zip is difficult to close when the bag is full."], "Color mismatch": ["The olive shade looks darker than the photos.", "Color is different from the product image."], "Strap comfort": ["Shoulder straps dig in with a heavy load.", "Strap padding feels thin for long commutes."], "Other": ["Changed my mind about this purchase.", "Ordered the wrong style."]}
    for i in range(160):
        age = rng.randrange(0, 28)
        product_index = rng.choices(range(10), weights=[29, 16, 12, 11, 8, 6, 5, 5, 4, 4])[0]
        key = f"p{product_index+1}"
        issue = ("Laptop fit" if age < 14 else "Other") if product_index == 0 and i % 4 != 0 else "Zipper quality" if product_index == 1 else "Color mismatch" if product_index == 2 else "Strap comfort" if product_index == 3 else rng.choice(list(complaints))
        record = {"id": f"demo-{i+1:04}", "return_id": f"ret-{i+1:04}", "order_id": f"ord-{2000+i}", "order_item_id": f"item-{i}", "refund_id": None, "product_id": key, "variant_id": f"v{product_index+1}", "reason": rng.choice(complaints[issue]), "returned_at": (today-timedelta(days=age)).isoformat(), "status": "received", "source": "synthetic", "issue": issue, "listing_snapshot_id": None}
        store.put("returns", record["id"], record)
        store.put("orders", record["order_id"], {"id": record["order_id"], "source": "synthetic", "fulfilled_at": (today-timedelta(days=age+10)).isoformat(), "items": [{"id": record["order_item_id"], "product_id": key, "variant_id": record["variant_id"], "quantity": 1}]})
    actions = [
        {"id":"fit-p1", "product_id":"p1", "kind":"fit_check", "title":"Help buyers check their laptop fit", "text":"Check your laptop's width, height and depth, including any sleeve, against the compartment measurements.", "status":"draft", "width":26.0,"height":37.0,"depth":3.0,"measurements_verified":False,"issue":"Laptop fit"},
        {"id":"listing-p1", "product_id":"p1", "kind":"listing", "title":"Make laptop sizing more specific", "text":"Screen size alone does not confirm fit. Check your laptop dimensions, including its sleeve, using our Purchase Fit Check.","status":"draft","issue":"Laptop fit"},
        {"id":"inspect-p2","product_id":"p2","kind":"inspection","title":"Inspect the main zipper","text":"Inspect the main zipper on available stock and review the reported failure points before deciding on a listing change.","status":"draft","issue":"Zipper quality"},
        {"id":"fit-p7", "product_id":"p7", "kind":"fit_check", "title":"Commuter laptop check", "text":"Compare your laptop with the verified compartment measurements.", "status":"approved", "width":27.0,"height":39.0,"depth":3.5,"measurements_verified":True,"issue":"Laptop fit", "approved_at":now(), "source":"synthetic"}
    ]
    for action in actions:
        action["source"] = "synthetic"
        store.put("actions", action["id"], action)
    store.put("meta", "initialized", {"id":"initialized","at":now()})
