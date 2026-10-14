import {useEffect,useState} from 'react';
import {ArrowLeft,CheckCircle2,ChevronRight,ClipboardCheck,Info,Radar,ShoppingBag} from 'lucide-react';
import type {PublicProduct} from './types';
import './buyer-guide.css';

const guideNames:Record<string,string>={
 sizing:'Size guide',footwear:'Shoe fit guide',compatibility:'Compatibility & setup',
 care:'Use & care guide',placement:'Placement & delivery guide',materials:'Dimensions & materials',
 beauty:'Packaging & care',included:'What’s included',carry:'Capacity & carrying guide',general:'Before you buy',
};

export default function BuyerGuide({product}:{product:PublicProduct}){
 const guide=product.purchase_check;
 const [checked,setChecked]=useState<Set<number>>(new Set());
 const [reviewed,setReviewed]=useState(false);
 useEffect(()=>{setChecked(new Set());setReviewed(false);},[product.id,guide]);
 const complete=!!guide&&guide.checklist.length>0&&guide.checklist.every((_,index)=>checked.has(index));
 function toggle(index:number){
  setChecked(previous=>{const next=new Set(previous);if(next.has(index))next.delete(index);else next.add(index);return next;});
  setReviewed(false);
 }
 return <div className="buyer-guide-page">
  <header className="buyer-guide-header"><a href="/" className="buyer-guide-brand"><Radar size={29}/>returnradar.</a><span>Confident choices start with clear details.</span><a className="button small" href="/"><ArrowLeft size={15}/>Merchant workspace</a></header>
  <div className="buyer-guide-breadcrumb"><span>Before you buy</span><ChevronRight size={14}/><span>{product.name}</span></div>
  <main className="buyer-guide-layout">
   <section className="buyer-guide-product" aria-label="Product details">
    <div className="buyer-guide-image">{product.image?<img src={product.image} alt={product.name}/>:<div><ShoppingBag size={60} strokeWidth={1.25}/><span>Product image not available</span></div>}</div>
    <div className="buyer-guide-product-copy">{product.demo&&<span className="badge">Sample product</span>}{product.category&&<span className="buyer-guide-category">{product.category}</span>}<h1>{product.name}</h1><p className="buyer-guide-variant">{product.variant}</p><p>{product.description}</p>{product.price!==null&&(product.currency||product.demo)&&<strong className="buyer-guide-price">{new Intl.NumberFormat('en-IN',{style:'currency',currency:product.currency||'INR',maximumFractionDigits:0}).format(product.price)}</strong>}</div>
   </section>
   <section className="buyer-guide-card" aria-labelledby="buyer-guide-title">
    <div className="buyer-guide-heading"><span className="buyer-guide-icon"><ClipboardCheck size={25}/></span><div><span className="buyer-guide-eyebrow">{guide?guideNames[guide.guide_type]||'Before you buy':'PRODUCT GUIDANCE'}</span><h2 id="buyer-guide-title">{guide?.title||'Buyer guidance is being prepared'}</h2></div></div>
    {guide?<>
     <p className="buyer-guide-instructions">{guide.instructions}</p>
     <div className="buyer-guide-verification"><CheckCircle2 size={16}/><span>Product details reviewed by the merchant</span></div>
     <form onSubmit={event=>{event.preventDefault();setReviewed(true);}}>
      <fieldset className="buyer-guide-checklist"><legend>Check the details that matter to you</legend>{guide.checklist.map((item,index)=><label key={index} className={checked.has(index)?'checked':''}><input type="checkbox" checked={checked.has(index)} onChange={()=>toggle(index)}/><span>{item}</span></label>)}</fieldset>
      <button type="submit" className="button primary buyer-guide-submit"><ClipboardCheck size={17}/>Review my checks</button>
     </form>
     {reviewed&&<div className={`buyer-guide-result ${complete?'complete':'incomplete'}`} role="status">{complete?<CheckCircle2 size={22}/>:<Info size={22}/>}<div><h3>{complete?'Checks reviewed':'Some checks still need attention'}</h3><p>{complete?'You have reviewed each item in the merchant’s checklist. Confirm any remaining questions with the store before choosing this product.':'Review the unchecked details and ask the store about anything you cannot confirm.'}</p></div></div>}
     <p className="buyer-guide-note">This checklist helps you review product details. It does not guarantee suitability or fit.</p>
     <p className="buyer-guide-privacy">Your selections stay on this page.</p>
    </>:<div className="buyer-guide-pending"><Info size={23}/><div><p>The merchant has not published a verified buyer guide for this product yet.</p><p>Check the product description and ask the store about sizing, features or included items before buying.</p></div></div>}
   </section>
  </main>
  <footer className="buyer-guide-footer"><span><Radar size={18}/>Buyer guidance by ReturnRadar</span><span>Fewer returns. Happier customers.</span></footer>
 </div>;
}
