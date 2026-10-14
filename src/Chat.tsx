import {useEffect,useRef,useState,type FormEvent} from 'react';
import {MessageCircle,Sparkles,Send,Plus,ShieldCheck,FileText,ChevronDown,CalendarDays,LoaderCircle,RefreshCw,Info,ScanLine,BookOpen,Lightbulb,TriangleAlert} from 'lucide-react';
import {api} from './api';
import {functionalError} from './functional-errors';
import type {AIStatus,AnalysisRun,ChatResponse,Dashboard} from './types';

type Message={id:number;role:'user'|'assistant';content:string;response?:ChatResponse};
const starterQuestions=[
 {icon:ScanLine,label:'Know what',text:'What should I focus on in my return data?'},
 {icon:Lightbulb,label:'Know why',text:'Why are customers returning my highest-risk products?'},
 {icon:BookOpen,label:'Know how',text:'What should I change to prevent repeat returns?'},
 {icon:Sparkles,label:'Review progress',text:'How can I check whether my changes are reducing returns?'},
];
const sampleQuestions=[
 {icon:ScanLine,label:'Choose a priority',text:'Which sample products should I focus on first to reduce return requests?'},
 {icon:Lightbulb,label:'Understand the evidence',text:'Why are customers returning the Everyday Running Shoes, and what does their feedback suggest?'},
 {icon:BookOpen,label:'Choose an improvement',text:'What checks would help prevent missing-piece returns for the Wooden Puzzle Set?'},
 {icon:Sparkles,label:'Plan a review',text:'How should I review a sizing-guide change and measure whether it reduces returns?'},
];
const sourceLabels:Record<string,string>={all:'All sources',synthetic:'Sample data',csv:'File imports',shopify:'Shopify',woocommerce:'WooCommerce'};
const timestamp=(value:string)=>new Date(value).toLocaleString('en-IN',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'});

export function AnalysisStatus({analysis}:{analysis:AnalysisRun|null}){
 if(!analysis)return <div className="analysis-status"><Info size={17}/><div><strong>Ready for your first analysis</strong><span>Analyse your returns to find recurring issues and practical next steps.</span></div></div>;
 if(analysis.records===0)return <div className="analysis-status"><Info size={17}/><div><strong>No returns to analyse yet</strong><span>Import return records to discover where you can improve.</span></div></div>;
 return <div className="analysis-status"><Sparkles size={18}/><div><strong>Latest analysis complete</strong><span>{analysis.records!==undefined?`${analysis.records} return records reviewed. `:''}Review the findings and choose a change to test.</span></div><span className="analysis-time">{timestamp(analysis.at)}</span></div>;
}

export default function Chat({data,refreshKey,onNavigate,sampleData=false}:{data:Dashboard;refreshKey:number;sampleData?:boolean;onNavigate:(view:'actions'|'products'|'integrations'|'reviews')=>void}){
 const [status,setStatus]=useState<AIStatus|null>(null);
 const [messages,setMessages]=useState<Message[]>([]);
 const [question,setQuestion]=useState('');
 const [days,setDays]=useState(28);
 const [source,setSource]=useState('all');
 const [busy,setBusy]=useState(false);
 const [error,setError]=useState('');
 const [retryQuestion,setRetryQuestion]=useState('');
 const end=useRef<HTMLDivElement>(null);
 const input=useRef<HTMLTextAreaElement>(null);
 const sequence=useRef(0);
 const mounted=useRef(true);
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
 const loadStatus=()=>api<AIStatus>('/ai-status').then(result=>{setStatus(result);setError('');}).catch(e=>setError(functionalError(e,'Questions are temporarily unavailable. Please try again shortly.')));
 useEffect(()=>{void loadStatus();},[refreshKey]);
 useEffect(()=>{if(messages.length||busy)end.current?.scrollIntoView({block:'nearest',behavior:'smooth'});},[messages,busy]);
 async function ask(raw:string){
  const text=raw.trim();if(!text||busy)return;
  setError('');setBusy(true);setRetryQuestion(text);setQuestion('');
  const previous=messages.at(-1)?.role==='user'&&messages.at(-1)?.content===text?messages.slice(0,-1):messages;
  setMessages([...previous,{id:++sequence.current,role:'user',content:text}]);
  const history=previous.slice(-8).map(m=>({role:m.role,content:m.content.slice(0,4000)}));
  try{
   const response=await api<ChatResponse>('/chat',{method:'POST',body:JSON.stringify({question:text,days,source,history})});
   if(!mounted.current)return;
   setMessages(current=>[...current,{id:++sequence.current,role:'assistant',content:response.answer,response}]);
   setRetryQuestion('');void loadStatus();
  }catch(e){if(mounted.current)setError(functionalError(e,'Radar couldn’t answer that question right now. Please try again.'));}finally{if(mounted.current)setBusy(false);}
 }
 function submit(event:FormEvent){event.preventDefault();void ask(question);}
 return <div className="ask-radar-layout">
  <section className="card chat-workspace">
   <div className="chat-top"><div className="chat-title"><div className="chat-logo"><MessageCircle size={22}/></div><div><h2>Ask Radar</h2><p>Clear answers. Practical next steps.</p></div></div><button className="button small" disabled={busy||messages.length===0} onClick={()=>{setMessages([]);setError('');setRetryQuestion('');input.current?.focus();}}><Plus size={15}/>New chat</button></div>
   <div className="chat-context"><span>Ask about</span><label className="select-wrap"><CalendarDays size={14}/><select aria-label="Chat date range" value={days} disabled={busy} onChange={e=>setDays(Number(e.target.value))}><option value={28}>Last 28 days</option><option value={14}>Last 14 days</option><option value={7}>Last 7 days</option><option value={90}>Last 90 days</option></select><ChevronDown size={13}/></label><label className="select-wrap"><select aria-label="Chat data source" disabled={busy} value={source} onChange={e=>setSource(e.target.value)}>{Object.entries(sourceLabels).map(([value,label])=><option key={value} value={value}>{label}</option>)}</select><ChevronDown size={13}/></label></div>
   <div className="chat-messages" role="log" aria-label="Ask Radar conversation" aria-live="polite">
    {!messages.length&&<div className="chat-welcome"><span className="chat-welcome-icon"><Sparkles size={30}/></span><div className="eyebrow">YOUR DATA. A LITTLE MORE UNDERSTANDING.</div><h2>Know what. Know why.<br/>Know what to do next.</h2><p>{sampleData?'Try a question about your sample products, then follow the evidence to a practical improvement.':'Ask about your return patterns, the evidence behind them, or how to take the next step.'}</p><div className="chat-starters">{(sampleData?sampleQuestions:starterQuestions).map(starter=><button key={starter.label} disabled={busy||!status?.configured} onClick={()=>void ask(starter.text)}><starter.icon size={18}/><div><span>{starter.label}</span><strong>{starter.text}</strong></div></button>)}</div></div>}
    {messages.map(message=><article className={`chat-message ${message.role}`} key={message.id}><div className="chat-message-avatar">{message.role==='assistant'?<RadarIcon/>:'You'}</div><div className="chat-message-body"><span className="chat-speaker">{message.role==='assistant'?'Radar':'You'}</span><div className="chat-answer-text">{message.response?.points?.length?<ul className="chat-answer-points">{message.response.points.map((point,index)=><li key={index}>{point}</li>)}</ul>:message.content}</div>{message.response&&<><div className="chat-scope">Based on {message.response.scope.returns} return records · {sourceLabels[message.response.scope.source]||'Imported records'} · {message.response.scope.days} days{message.response.scope.source_counts.synthetic>0&&<span>Includes sample records for demonstration</span>}</div>{message.response.evidence.length>0&&<details className="chat-evidence"><summary><FileText size={14}/>Supporting return feedback ({message.response.evidence.length})<ChevronDown size={14}/></summary>{message.response.evidence.map(item=><div key={item.id}><strong>{item.product_name}<span>{sourceLabels[item.source]||'Imported records'}</span></strong><p>“{item.reason}”</p></div>)}</details>}{message.response.follow_up_questions.length>0&&<div className="chat-followups">{message.response.follow_up_questions.map(followup=><button key={followup} disabled={busy} onClick={()=>void ask(followup)}>{followup}</button>)}</div>}</>}</div></article>)}
    {busy&&<div className="chat-thinking" role="status"><span className="chat-message-avatar"><RadarIcon/></span><span><LoaderCircle size={16} className="spin"/>Checking your selected returns…</span></div>}
    {!status?.configured&&status&&!error&&<div className="chat-unavailable"><Info size={17}/><p>Questions are temporarily unavailable. You can still review your product findings and suggested actions.</p></div>}
    {error&&<div className="chat-error" role="alert"><TriangleAlert size={18}/><div><strong>Couldn’t complete that request</strong><p>{error}</p><button className="text-button" onClick={()=>retryQuestion?void ask(retryQuestion):void loadStatus()} disabled={busy}><RefreshCw size={14}/>{retryQuestion?'Retry question':'Try again'}</button></div></div>}
    <div ref={end}/>
   </div>
   <form className="chat-composer" onSubmit={submit}><div className="chat-input-row"><textarea ref={input} aria-label="Ask a question" rows={2} maxLength={1500} placeholder="Ask about your returns, a signal, or how to use ReturnRadar…" value={question} disabled={busy} onChange={e=>setQuestion(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();if(question.trim())void ask(question);}}}/><button className="chat-send" aria-label="Send question" type="submit" disabled={busy||!question.trim()||!status?.configured}>{busy?<LoaderCircle size={19} className="spin"/>:<Send size={19}/>}</button></div><div className="chat-composer-footer"><span><ShieldCheck size={13}/>Answers explain your data. You approve every change.</span><span>{question.length}/1500</span></div></form>
  </section>
  <aside className="chat-side"><section className="card chat-side-card"><div className="chat-side-heading"><Sparkles size={19}/><h3>Your return insights</h3></div><AnalysisStatus analysis={status?.analysis||data.analysis}/><p>Answers use your latest findings and the returns selected above. Customer feedback points to possible causes; verify them before making a change.</p><button className="text-button" onClick={()=>void loadStatus()}><RefreshCw size={14}/>Refresh insights</button></section><section className="card chat-side-card"><h3>Turn findings into progress</h3><ol className="chat-next-steps"><li>Choose one product issue.</li><li>Review the customer feedback.</li><li>Make a focused improvement.</li><li>Review new returns to see what changed.</li></ol><p>Compare returned units with sold units before claiming a lower return rate.</p><div className="chat-side-links"><button onClick={()=>onNavigate('products')}><ScanLine size={15}/>Review product findings</button><button onClick={()=>onNavigate('reviews')}><RefreshCw size={15}/>Plan a product review</button><button onClick={()=>onNavigate('actions')}><ShieldCheck size={15}/>Review suggested actions</button><button onClick={()=>onNavigate('integrations')}><Info size={15}/>Bring in new return records</button></div></section><div className="chat-private-note"><ShieldCheck size={17}/><p>Questions explain your data. Your team reviews and approves every change.</p></div></aside>
 </div>;
}

function RadarIcon(){return <MessageCircle size={18}/>;}
