import {useEffect,useRef} from 'react';
import type {Dashboard,Pattern} from './types';
type Tool={name:string;title:string;description:string;inputSchema:object;annotations:{readOnlyHint:boolean;untrustedContentHint:boolean};execute:(input:unknown)=>unknown|Promise<unknown>};
type Context={registerTool:(tool:Tool,options:{signal:AbortSignal})=>void|Promise<void>};

export function useWebTools(data:Dashboard|null,open:(p:Pattern)=>void){
 const latest=useRef({data,open});
 latest.current={data,open};
 useEffect(()=>{
  const context=(document as Document & {modelContext?:Context}).modelContext;
  if(!context?.registerTool)return;
  const lifecycle=new AbortController();
  const tools:Tool[]=[{
   name:'read_return_summary',title:'Read return summary',description:'Read the current filtered complaint counts and recurring pattern IDs. Does not change records.',
   inputSchema:{type:'object',properties:{},additionalProperties:false},annotations:{readOnlyHint:true,untrustedContentHint:false},
   execute(input){
    if(!input||typeof input!=='object'||Object.keys(input).length)throw new Error('Expected an empty object.');
    if(!latest.current.data)throw new Error('Workspace is not loaded.');
    const d=latest.current.data;
    return {days:d.days,counts:d.counts,patterns:d.patterns.map(p=>({id:p.id,count:p.count,sample_size:p.sample_size,emerging:p.emerging}))};
   }
  },{
   name:'start_return_investigation',title:'Open return investigation',description:'Open the evidence panel for a pattern in the current view. This stages an investigation and does not approve any action.',
   inputSchema:{type:'object',properties:{pattern_id:{type:'string'}},required:['pattern_id'],additionalProperties:false},annotations:{readOnlyHint:false,untrustedContentHint:true},
   async execute(input){
    if(!input||typeof input!=='object'||Object.keys(input).some(k=>k!=='pattern_id')||typeof (input as {pattern_id?:unknown}).pattern_id!=='string')throw new Error('Expected a pattern_id string.');
    const pattern=latest.current.data?.patterns.find(p=>p.id===(input as {pattern_id:string}).pattern_id);
    if(!pattern)throw new Error('Pattern is not in the current view.');
    latest.current.open(pattern);
    await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    return {opened:true,pattern_id:pattern.id,evidence_count:pattern.count};
   }
  }];
  for(const tool of tools){
   try{void Promise.resolve(context.registerTool(tool,{signal:lifecycle.signal})).catch(()=>{});}catch{/* Unsupported experimental implementation. */}
  }
  return ()=>lifecycle.abort();
 },[]);
}
