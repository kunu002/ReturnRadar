import {useEffect,useState} from 'react';
import * as Tabs from '@radix-ui/react-tabs';
import {BookOpen,CheckCheck,ChevronDown,ClipboardCheck,ExternalLink,FileText,PackageCheck,Ruler,RotateCcw,ScanLine,Search,ShieldCheck,ShoppingBag,Sparkles,Wrench,X} from 'lucide-react';
import type {Action,Dashboard} from './types';
import './actions.css';

const actionTypes:Record<string,{label:string;icon:typeof ShieldCheck;tone:string}>={
 fit_check:{label:'Purchase Fit Check',icon:ShieldCheck,tone:'green'},
 sizing_guide:{label:'Sizing guide',icon:Ruler,tone:'green'},
 compatibility_check:{label:'Compatibility guide',icon:Wrench,tone:'blue'},
 care_guide:{label:'Use & care guide',icon:BookOpen,tone:'blue'},
 packaging_check:{label:'Packaging check',icon:PackageCheck,tone:'amber'},
 quality_check:{label:'Quality check',icon:ClipboardCheck,tone:'amber'},
 listing:{label:'Listing suggestion',icon:FileText,tone:'blue'},
 inspection:{label:'Stock inspection',icon:ScanLine,tone:'amber'},
};
const buyerGuides:Record<string,{label:string;icon:typeof ShieldCheck;tone:string}>={
 sizing:{label:'Size guide',icon:Ruler,tone:'green'},
 footwear:{label:'Shoe fit guide',icon:Ruler,tone:'green'},
 compatibility:{label:'Compatibility & setup',icon:Wrench,tone:'blue'},
 care:{label:'Use & care guide',icon:BookOpen,tone:'blue'},
 placement:{label:'Dimensions & delivery',icon:Ruler,tone:'green'},
 beauty:{label:'Packaging & care',icon:PackageCheck,tone:'blue'},
 materials:{label:'Dimensions & materials',icon:Ruler,tone:'green'},
 included:{label:"What's included",icon:PackageCheck,tone:'amber'},
 carry:{label:'Capacity & carry',icon:ShoppingBag,tone:'green'},
 general:{label:'Before you buy',icon:CheckCheck,tone:'green'},
};
function actionTypeKey(action:Action){return action.kind==='buyer_guide'?`buyer_guide:${action.guide_type||'general'}`:action.kind;}
function actionType(key:string){return key.startsWith('buyer_guide:')?buyerGuides[key.slice('buyer_guide:'.length)]||buyerGuides.general:actionTypes[key]||{label:'Product improvement',icon:Sparkles,tone:'green'};}
const sourceLabels:Record<string,string>={synthetic:'Sample feedback',csv:'Imported feedback',shopify:'Shopify feedback',woocommerce:'WooCommerce feedback'};
const statuses=['draft','approved','dismissed'] as const;

export default function Actions({data,onEdit,workspace,selectedSource='all'}:{data:Dashboard;onEdit:(action:Action)=>void;workspace:string;selectedSource?:string}){
 const [query,setQuery]=useState('');
 const [product,setProduct]=useState('all');
 const [issue,setIssue]=useState('all');
 const [kind,setKind]=useState('all');
 const [status,setStatus]=useState('draft');
 const clearFilters=()=>{setQuery('');setProduct('all');setIssue('all');setKind('all');};
 useEffect(()=>{clearFilters();setStatus('draft');},[workspace,selectedSource]);
 const productById=new Map(data.products.map(item=>[item.id,item]));
 const availableProducts=[...new Set(data.actions.map(action=>action.product_id))].map(id=>({id,name:productById.get(id)?.name||'Archived product'})).sort((a,b)=>a.name.localeCompare(b.name));
 const availableIssues=[...new Set(data.actions.map(action=>action.issue).filter(Boolean))].sort();
 const availableKinds=[...new Set(data.actions.map(actionTypeKey))].sort((a,b)=>actionType(a).label.localeCompare(actionType(b).label));
 const search=query.trim().toLocaleLowerCase();
 const matches=data.actions.filter(action=>{
  const item=productById.get(action.product_id);
  const searchable=[item?.name,item?.variant,item?.sku,action.title,action.text,action.issue,actionType(actionTypeKey(action)).label].join(' ').toLocaleLowerCase();
  return (product==='all'||action.product_id===product)&&(issue==='all'||action.issue===issue)&&(kind==='all'||actionTypeKey(action)===kind)&&(!search||searchable.includes(search));
 });
 const filtered=query.trim()!==''||product!=='all'||issue!=='all'||kind!=='all';
 const activeActions=matches.filter(action=>action.status===status);
 const totalForStatus=data.actions.filter(action=>action.status===status).length;

 return <div className="action-center">
  <section className="card action-filters" aria-label="Action filters">
   <div className="action-filter-heading"><div><h2>Find an action</h2><p>Focus on the products and improvements you want to review.</p></div>{filtered&&<button className="text-button" onClick={clearFilters}><X size={14}/>Reset filters</button>}</div>
   <div className="action-filter-controls">
    <label className="search-box action-search"><Search size={17}/><input aria-label="Search actions" placeholder="Search products or actions…" value={query} onChange={event=>setQuery(event.target.value)}/></label>
    <label className="action-select"><span>Product</span><div><select aria-label="Action product" value={product} onChange={event=>setProduct(event.target.value)}><option value="all">All products</option>{availableProducts.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select><ChevronDown size={14}/></div></label>
    <label className="action-select"><span>Issue</span><div><select aria-label="Action issue" value={issue} onChange={event=>setIssue(event.target.value)}><option value="all">All issues</option>{availableIssues.map(value=><option key={value} value={value}>{value}</option>)}</select><ChevronDown size={14}/></div></label>
    <label className="action-select"><span>Action type</span><div><select aria-label="Action type" value={kind} onChange={event=>setKind(event.target.value)}><option value="all">All action types</option>{availableKinds.map(value=><option key={value} value={value}>{actionType(value).label}</option>)}</select><ChevronDown size={14}/></div></label>
   </div>
  </section>
  <Tabs.Root value={status} onValueChange={setStatus}>
   <Tabs.List className="tabs-list" aria-label="Action status"><Tabs.Trigger value="draft">Needs review<span className="badge">{matches.filter(action=>action.status==='draft').length}</span></Tabs.Trigger><Tabs.Trigger value="approved">Approved</Tabs.Trigger><Tabs.Trigger value="dismissed">Dismissed</Tabs.Trigger></Tabs.List>
   <p className="action-result-count" role="status">Showing {activeActions.length} of {totalForStatus} {totalForStatus===1?'action':'actions'}{selectedSource!=='all'?` · ${selectedSource==='synthetic'?'Sample data':selectedSource==='csv'?'CSV imports':selectedSource==='shopify'?'Shopify':selectedSource==='woocommerce'?'WooCommerce':'Selected source'}`:''}</p>
   {statuses.map(value=><Tabs.Content value={value} key={value}>
    <div className="action-list">{matches.filter(action=>action.status===value).map(action=>{
     const type=actionType(actionTypeKey(action));
     const verifiedFit=action.kind==='fit_check'&&action.measurements_verified&&[action.width,action.height,action.depth].every(dimension=>typeof dimension==='number'&&dimension>0);
     const verifiedGuide=action.kind==='buyer_guide'&&action.guidance_verified;
     const previewAvailable=value==='approved'&&(verifiedFit||verifiedGuide);
     return <section className="card action-card" key={action.id}>
      <div className={`action-type-icon ${type.tone}`}><type.icon size={22}/></div>
      <div className="action-body"><div className="action-labels"><span className="badge">{type.label}</span><span className="action-product-name">{productById.get(action.product_id)?.name||'Archived product'}</span></div><h2>{action.title}</h2><p>{action.text}</p><div className="action-meta"><span><RotateCcw size={13}/>{action.issue}</span>{action.source&&<span>{sourceLabels[action.source]||'Store feedback'}</span>}{action.kind==='buyer_guide'&&!!action.checklist?.length&&<span>{action.checklist.length} buyer {action.checklist.length===1?'check':'checks'}</span>}{value==='approved'&&action.kind!=='fit_check'&&action.kind!=='buyer_guide'&&<span><CheckCheck size={13}/>Approved for your team · apply the change in your store</span>}{value==='approved'&&action.kind==='fit_check'&&!previewAvailable&&<span>Verify measurements before sharing a fit check</span>}{value==='approved'&&action.kind==='buyer_guide'&&<span>{previewAvailable?'Buyer guide ready to share':'Verify product guidance before sharing a buyer guide'}</span>}</div></div>
      <div className="action-buttons"><button className={`button ${value==='draft'?'primary':''}`} onClick={()=>onEdit(action)}>{value==='draft'?'Review action':'View decision'}</button>{previewAvailable&&<a className="text-button" href={`?product=${encodeURIComponent(action.product_id)}&workspace=${encodeURIComponent(workspace)}${action.kind==='buyer_guide'?'&check=guide':''}`} target="_blank" rel="noreferrer">Preview<ExternalLink size={14}/></a>}</div>
     </section>;
    })}</div>
    {!matches.some(action=>action.status===value)&&<div className="empty"><ScanLine size={32}/><h3>{filtered?'No actions match these filters':value==='draft'?'All caught up':'No actions here yet'}</h3><p>{filtered?'Try a broader search or reset your filters to see more actions.':value==='draft'?'Propose actions from a product investigation when you are ready.':'Review a suggested action to save your decision here.'}</p>{filtered&&<button className="button" onClick={clearFilters}>Reset filters</button>}</div>}
   </Tabs.Content>)}
  </Tabs.Root>
 </div>;
}
