"""Product-specific guidance uses merchant-approved facts, never invented fit rules."""
import re


def product_category(product):
    values=" ".join(str(product.get(field) or "") for field in ("category","product_type","categories"))
    name=product.get("name","")
    texts=[values.lower(),name.lower()]
    rules=[("Footwear",r"footwear|shoes?|sneakers?|trainers?|boots?|sandals?"),("Clothing",r"clothing|shirts?|tees?|dresses?|trousers?|jackets?|hoodies?|apparel|jeans"),("Electronics",r"electronics?|headphones?|earbuds?|chargers?|speakers?|wireless devices?"),("Kitchen",r"kitchen|mugs?|cups?|tumblers?|bottles?|drinkware|cookware"),("Beauty",r"beauty|cosmetic|serum|skincare|moisturizer"),("Fitness",r"fitness|yoga|exercise|workout"),("Toys",r"toy|puzzle|playset"),("Home",r"home|lamp|furniture|decor|table|chair"),("Bags",r"bags?|backpacks?|daypacks?|duffles?|duffels?|slings?|luggage|totes?|crossbody|laptop sleeves?")]
    for text in texts:
        for category,pattern in rules:
            if re.search(r"\b(?:"+pattern+r")\b",text):
                return category
    if "padded laptop compartment" in (product.get("description") or "").lower():
        return "Bags"
    return "Other"


GUIDES={
 "Clothing":("sizing","Choose the right clothing size","Before choosing a size, compare your measurements with the seller’s size information. Ask about any missing garment measurement.",["Compare your measurements with the seller’s size guide.","Check the selected size and preferred fit.","Ask the seller about any missing garment measurement before buying."]),
 "Footwear":("footwear","Choose a suitable shoe size","Compare foot length and width with the seller’s shoe-size information. Ask about unclear fit details before choosing a size.",["Compare foot length and width with the seller’s shoe-size guide.","Check the selected size and any fit notes supplied for this shoe.","Ask the seller if the sizing information is incomplete."]),
 "Electronics":("compatibility","Check compatibility and setup","Review the published device requirements, included accessories and manufacturer-supported setup instructions before choosing this product.",["Compare the published device requirements with your equipment.","Check which cables or accessories are included.","Review the manufacturer-supported setup instructions and ask about unknown requirements."]),
 "Kitchen":("care","Understand use and care","Review the supplied closing, cleaning and care instructions. Ask the seller to clarify any use limits that are not provided.",["Review the supplied use and closing instructions.","Check the manufacturer’s cleaning and care guidance.","Ask the seller to clarify any missing use limits or included parts."]),
 "Home":("placement","Check dimensions and delivery","Compare the published dimensions with your space, review the included items, and check the seller’s arrival-inspection guidance.",["Compare published product dimensions with the intended space.","Review the seller’s list of included items.","Check the seller’s instructions for inspecting the delivery and reporting damage."]),
 "Beauty":("beauty","Review packaging and care","Read the manufacturer’s supplied use and storage information and the seller’s packaging guidance. Ask about missing details before buying.",["Read the manufacturer’s supplied use and storage instructions.","Review the seller’s guidance on intact packaging and seals.","Ask the seller about missing product details rather than assuming a benefit."]),
 "Fitness":("materials","Check dimensions and materials","Review the published dimensions, material details and manufacturer-supported use and care instructions before buying.",["Compare the published dimensions with your intended use.","Review the verified material and included-item details.","Check the supplied use and care instructions."]),
 "Toys":("included","Check what is included","Review the seller’s verified list of included pieces and the manufacturer’s supplied instructions. Ask about missing information before buying.",["Review the seller’s verified list of included pieces.","Check the instructions supplied by the manufacturer.","Ask the seller to clarify any missing information before buying."]),
 "Bags":("carry","Choose your carrying setup","Compare your contents with published usable dimensions, carrying features and included items. Screen size alone does not establish laptop fit.",["Compare your intended contents with the published usable dimensions.","Check the carrying features and included items.","Ask the seller to clarify measurements that are not provided."]),
 "Other":("general","Review the product before buying","Review the published product features and included items. Ask the seller to clarify details that matter to your purchase.",["Compare the published features with what you need.","Check the seller’s list of included items.","Ask the seller to clarify important details that are missing."]),
}


def buyer_guide(product):
    kind,title,text,checklist=GUIDES[product_category(product)]
    return {"kind":"buyer_guide","guide_type":kind,"title":title,"text":text,"checklist":checklist,"guidance_verified":False}


def fit_supported(product,actions=()):
    category=product_category(product)
    return category=="Bags" or category=="Other" and any(action.get("kind")=="fit_check" for action in actions)


def public_check_ready(action,product=None):
    if action.get("status")!="approved":
        return False
    if action.get("kind")=="buyer_guide":
        checks=action.get("checklist") or []
        return bool(action.get("guidance_verified") and 2<=len(checks)<=8 and all(isinstance(item,str) and item.strip() for item in checks))
    if action.get("kind")=="fit_check":
        return bool(action.get("measurements_verified") and all(isinstance(action.get(field),(int,float)) and not isinstance(action.get(field),bool) and action[field]>0 for field in ("width","height","depth")) and (not product or len(product.get("variants",[]))<=1 and fit_supported(product,[action])))
    return False
