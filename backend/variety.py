"""Additional explicitly synthetic categories, preserving existing review decisions."""
from datetime import datetime,timedelta,timezone
from .store import now


CATALOG=[
 ("demo-shirt","Everyday Cotton Shirt","Clothing","Blue · Medium",1299,"Regular-fit cotton shirt with a detailed size chart.","Size & fit",["The shirt is too tight around the shoulders.","The size chart did not match the shirt I received.","The sleeves are too small for the labelled size."],"Check the size chart against garment measurements; add chest and shoulder measurements."),
 ("demo-sneakers","Everyday Running Shoes","Footwear","White · UK 8",2999,"Lightweight running shoes with cushioned soles and standard sizing.","Size & fit",["These shoes are too small for my usual size.","The front is too tight for my feet.","I need clearer sizing guidance for wide feet."],"Check the fit across sizes; add foot-length guidance and a note about toe-box width."),
 ("demo-headphones","Wireless Headphones","Electronics","Graphite",3499,"Wireless over-ear headphones supplied with a charging cable.","Product quality",["The battery stopped working after two days.","The headphones do not charge consistently.","The wireless connection drops during calls."],"Inspect charging and pairing on a sample of stock; record results before updating product guidance."),
 ("demo-mug","Travel Coffee Mug","Kitchen","Steel · 450 ml",899,"A stainless-steel travel mug with a removable lid.","Packaging",["The lid leaked during my commute.","The mug was leaking when tilted in my bag.","The packaging arrived wet because the seal was loose."],"Check lid seals and include clear closing instructions; verify any leak-resistance claim."),
 ("demo-lamp","Ceramic Table Lamp","Home","Ivory",2199,"Ceramic table lamp supplied with a fitted protective carton.","Damaged on arrival",["The ceramic base arrived cracked.","The shade arrived damaged inside the carton.","The lamp arrived broken despite the outer box looking fine."],"Review protective packaging and drop-test a sample before changing the shipping process."),
 ("demo-serum","Hydrating Face Serum","Beauty","30 ml",799,"A bottled cosmetic serum supplied with a sealed dropper. No clinical benefits are claimed.","Packaging",["The serum leaked inside the delivery box.","The bottle seal was broken on arrival.","The packaging was wet around the dropper cap."],"Inspect cap torque, seals and delivery packaging; avoid making unverified product-benefit claims."),
 ("demo-yoga","Everyday Yoga Mat","Fitness","Sage · 6 mm",1499,"A yoga mat with dimensions and care instructions shown in the product details.","Description mismatch",["The material felt different from the product description.","The mat thickness did not match the description.","The product specification needs clearer material details."],"Measure mat thickness and length, verify material details, and update the description with checked facts."),
 ("demo-toy","Wooden Puzzle Set","Toys","Multicolour",999,"A wooden puzzle set with a listed count of included pieces.","Missing parts",["The puzzle was missing parts when the box arrived.","A missing part prevented completing the puzzle.","The set was missing two puzzle pieces."],"Introduce a piece-count packing check and photograph a verified complete set for the listing."),
]


def seed_variety(db):
    if db.get("meta","variety_fixtures"):
        return
    today=datetime.now(timezone.utc).replace(microsecond=0)
    for product_id,name,category,variant,price,description,issue,reasons,plan in CATALOG:
        db.create("products",product_id,{"id":product_id,"name":name,"category":category,"variant":variant,"sku":"SAMPLE-"+product_id[5:].upper(),"price":price,"description":description,"listing_claim":description,"image":None,"source":"synthetic"})
        evidence=[]
        for index in range(12):
            key=f"sample-{product_id}-{index+1}"
            age=index*2+1
            returned_at=(today-timedelta(days=age)).isoformat()
            order_id=f"sample-order-{product_id}-{index+1}";item_id=f"sample-item-{product_id}-{index+1}"
            db.create("returns",key,{"id":key,"return_id":"return-"+key,"product_id":product_id,"variant_id":product_id+"-variant","order_id":order_id,"order_item_id":item_id,"refund_id":None,"reason":reasons[index%len(reasons)],"returned_at":returned_at,"status":"received","source":"synthetic","quantity":1,"issue":issue,"listing_snapshot_id":None})
            db.create("orders",order_id,{"id":order_id,"created_at":(today-timedelta(days=age+5)).isoformat(),"source":"synthetic","status":"completed","items":[{"id":item_id,"product_id":product_id,"variant_id":product_id+"-variant","quantity":1}]})
            evidence.append(key)
        db.create("actions","sample-action-"+product_id,{"id":"sample-action-"+product_id,"product_id":product_id,"kind":"inspection" if category in ["Electronics","Toys"] else "listing","title":"Reduce "+issue.lower()+" complaints","text":plan,"status":"draft","issue":issue,"source":"synthetic","evidence_ids":evidence})
    db.put("meta","variety_fixtures",{"id":"variety_fixtures","at":now(),"synthetic":True})


def seed_sales_sample(db):
    if db.get("meta","sample_sales_fixture"):
        return
    today=datetime.now(timezone.utc).replace(microsecond=0)
    for product in db.all("products"):
        if product["source"]!="synthetic":
            continue
        for index in range(60):
            key=f"sample-sale-{product['id']}-{index+1}"
            db.create("orders",key,{"id":key,"created_at":(today-timedelta(days=index%28+1)).isoformat(),"source":"synthetic","status":"completed","items":[{"id":key+"-item","product_id":product["id"],"variant_id":product["id"]+"-variant","quantity":1,"net_total":str(product.get("price") or 0),"currency":"INR"}]})
    db.put("meta","sample_sales_fixture",{"id":"sample_sales_fixture","at":now(),"synthetic":True})
