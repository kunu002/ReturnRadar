import {useEffect,useState,type FormEvent} from 'react';
import {Radar,ShieldCheck,Users,LogOut,Copy,Mail,LockKeyhole,ArrowRight,LoaderCircle,Sparkles,ShoppingBag} from 'lucide-react';
import {api,APIError,authConfig,login,leaveDemo,exploreDemo,logout,setWorkspace,pendingInvitation,clearInvitation} from './api';
import {functionalError} from './functional-errors';
import type {Member,Role,Session,WorkspaceSummary} from './types';

type WorkspaceMode='sample'|'empty';
const roleDescriptions:Record<Role,string>={owner:'Manage the workspace, team, data and approvals.',admin:'Manage team members, data and approvals.',analyst:'Import records, run analysis, ask Radar and propose actions.',viewer:'Read dashboards, returns, evidence and saved decisions.'};

function WorkspaceChoices({mode,onChange,disabled=false}:{mode:WorkspaceMode;onChange:(mode:WorkspaceMode)=>void;disabled?:boolean}){
 return <fieldset className="workspace-choices" disabled={disabled}>
  <legend>How would you like to begin?</legend>
  <label className={`workspace-choice ${mode==='sample'?'selected':''}`}>
   <input type="radio" name="workspace-mode" value="sample" checked={mode==='sample'} onChange={()=>onChange('sample')}/>
   <Sparkles size={22} aria-hidden="true"/>
   <span><strong>Explore demo</strong><small>Your own sample store with products, sales, returns, suggested improvements and review cycles.</small></span>
  </label>
  <label className={`workspace-choice ${mode==='empty'?'selected':''}`}>
   <input type="radio" name="workspace-mode" value="empty" checked={mode==='empty'} onChange={()=>onChange('empty')}/>
   <ShoppingBag size={22} aria-hidden="true"/>
   <span><strong>Start my workspace</strong><small>Start empty, then connect your store or import your records.</small></span>
  </label>
  <p className="workspace-choice-note"><ShieldCheck size={16} aria-hidden="true"/>Both choices create a separate workspace for you. Matching names never grant access to another merchant.</p>
 </fieldset>;
}

export function LoginPage({onSignedIn,onDemo,error:initialError}:{onSignedIn:()=>void;onDemo:()=>void;error:string}){
 const invite=pendingInvitation();
 const [create,setCreate]=useState(!!invite);
 const [mode,setMode]=useState<WorkspaceMode>('sample');
 const [email,setEmail]=useState('');const [password,setPassword]=useState('');const [name,setName]=useState('');const [workspace,setWorkspaceName]=useState('');
 const [busy,setBusy]=useState(false);const [error,setError]=useState(initialError?functionalError(initialError):'');
 async function submit(e:FormEvent){
  e.preventDefault();setBusy(true);setError('');leaveDemo();
  try{
   const result=await api<Session>(create?'/auth/register':'/auth/login',{method:'POST',body:JSON.stringify(create?{email,password,name,workspace_name:workspace,workspace_mode:invite?'empty':mode,invite_token:invite}:{email,password})});
   if(create){setWorkspace(result.workspace.id);if(invite)clearInvitation();}
   onSignedIn();
  }catch(e){
   if(create&&!invite&&e instanceof APIError&&e.status===503){
    try{const current=await api<{needs_workspace?:boolean}>('/auth/session');if(current.needs_workspace){sessionStorage.setItem('rr_workspace_setup_retry',mode);onSignedIn();return;}}catch{/* Keep the original account error when no signed-in session exists. */}
   }
   setError(functionalError(e,'Sign-in is temporarily unavailable. Please try again shortly.'));
  }finally{setBusy(false);}
 }
 return <main className="account-page">
  <section className="account-intro">
   <a className="account-brand" href="/"><Radar size={29}/>returnradar.</a>
   <span className="eyebrow">A WORKSPACE FOR EVERY MERCHANT</span>
   <h1>Your store.<br/>Your team.<br/>A clearer picture.</h1>
   <p>Learn what drives returns, choose product improvements, and review whether the next purchase gets better.</p>
   <div className="account-benefit"><ShieldCheck size={21}/><span>Private merchant data<br/><small>Every workspace has its own records and permissions.</small></span></div>
  </section>
  <section className="account-form-card">
   <span className="account-form-icon"><LockKeyhole size={25}/></span>
   <h2>{invite?'Join your merchant team':create?'Choose your starting point':'Welcome back'}</h2>
   <p>{invite?'Use the email address your invitation was sent to.':create?'Explore a sample store or bring your own business data.':'Sign in to your merchant workspace.'}</p>
   {authConfig.provider==='local'?<>
    <div className="account-tabs"><button aria-pressed={!create} onClick={()=>{setCreate(false);setError('');}}>Sign in</button><button aria-pressed={create} onClick={()=>{setCreate(true);setError('');}}>Create account</button></div>
    <form onSubmit={submit}>
     {create&&!invite&&<WorkspaceChoices mode={mode} onChange={setMode} disabled={busy}/>}
     {create&&<label className="field-label">Your name<input value={name} maxLength={100} onChange={e=>setName(e.target.value)} autoComplete="name" required/></label>}
     {create&&!invite&&<label className="field-label">{mode==='sample'?'Sample workspace name (optional)':'Merchant workspace name'}<input value={workspace} maxLength={100} onChange={e=>setWorkspaceName(e.target.value)} placeholder={mode==='sample'?'My sample store':'e.g. Olive Carry'} required={mode==='empty'}/></label>}
     <label className="field-label">Email address<input type="email" value={email} onChange={e=>setEmail(e.target.value)} autoComplete="email" required/></label>
     <label className="field-label">Password<input type="password" value={password} onChange={e=>setPassword(e.target.value)} minLength={8} maxLength={15} autoComplete={create?'new-password':'current-password'} required/></label>
     {create&&<small>Use 8–15 characters. New accounts own their workspace; invited teammates receive the assigned role.</small>}
     <button className="button primary" disabled={busy}>{busy?<LoaderCircle size={17} className="spin"/>:<ArrowRight size={17}/>} {invite&&create?'Create account & join':create?mode==='sample'?'Create account & explore demo':'Create workspace':'Sign in'}</button>
    </form>
   </>:<button className="button primary" onClick={()=>{leaveDemo();void login().catch(e=>setError(functionalError(e)));}}>Continue with Google</button>}
   {error&&<div className="error-banner" role="alert">{error}</div>}
   {authConfig.demo&&<div className="account-demo"><span>Want to look around without signing in?</span><button className="text-button" onClick={()=>{exploreDemo();onDemo();}}>Explore sample workspace<ArrowRight size={15}/></button><small>Shared sample data. Sign in to create your own separate sample store.</small></div>}
  </section>
 </main>;
}

export function WorkspaceSetup({onReady}:{onReady:()=>void}){
 const [name,setName]=useState('');const [mode,setMode]=useState<WorkspaceMode>(sessionStorage.getItem('rr_workspace_setup_retry')==='empty'?'empty':'sample');const [error,setError]=useState('');const [busy,setBusy]=useState(false);
 const [recovering]=useState(!!sessionStorage.getItem('rr_workspace_setup_retry'));
 const [identity,setIdentity]=useState<{name:string;email:string}|null>(null);
 const [identityLoading,setIdentityLoading]=useState(true);
 const invite=pendingInvitation();
 useEffect(()=>{let active=true;void api<{user:{name:string;email:string}}>('/auth/identity').then(result=>{if(active)setIdentity(result.user);}).catch(e=>{if(active)setError(functionalError(e));}).finally(()=>{if(active)setIdentityLoading(false);});return()=>{active=false;};},[]);
 async function submit(e:FormEvent){
  e.preventDefault();setBusy(true);setError('');
  try{const s=await api<Session>(invite?'/auth/join':'/auth/workspace',{method:'POST',body:JSON.stringify(invite?{token:invite}:{name,mode})});setWorkspace(s.workspace.id);sessionStorage.removeItem('rr_workspace_setup_retry');clearInvitation();onReady();}catch(e){setError(functionalError(e));}finally{setBusy(false);}
 }
 async function switchAccount(){setBusy(true);setError('');try{await logout();location.reload();}catch(e){setError(functionalError(e));setBusy(false);}}
 return <div className={`sign-in ${invite?'':'workspace-onboarding'}`}>
  <Radar size={35}/>
  <h1>{invite?'Accept your invitation':'Choose your starting point'}</h1>
  {identity&&<p>Signed in as <strong>{identity.email}</strong>.</p>}
  {invite?<p>Use the exact email address your teammate invited. If you opened this link while signed in to another account, switch accounts below.</p>:<p>See how product insights become improvements, or start with your own store.</p>}
  {recovering&&!invite&&<p className="workspace-recovery-note" role="status">Your account is ready. Finish choosing your workspace below.</p>}
  <form onSubmit={submit}>
   {!invite&&<>
    <WorkspaceChoices mode={mode} onChange={setMode} disabled={busy}/>
    <label className="field-label">{mode==='sample'?'Sample workspace name (optional)':'Merchant workspace name'}<input value={name} maxLength={100} onChange={e=>setName(e.target.value)} placeholder={mode==='sample'?'My sample store':'e.g. Everyday Goods'} required={mode==='empty'}/></label>
    {mode==='sample'&&<p className="workspace-sample-note">Sample records are clearly labelled. Your changes stay in your own copy, and you can start a separate merchant workspace anytime.</p>}
   </>}
   <button className="button primary" disabled={busy||identityLoading||!identity}>{busy?<LoaderCircle size={17} className="spin"/>:<ArrowRight size={17}/>} {invite?'Join workspace':mode==='sample'?'Explore demo':'Start my workspace'}</button>
  </form>
  {error&&<p role="alert">{error}</p>}
  <button className="text-button" disabled={busy} onClick={()=>void switchAccount()}>{invite?'Use a different account':'Sign out'}</button>
  {invite&&<small>Invitation links expire after 48 hours. Ask your workspace owner for a new link if this one has expired.</small>}
 </div>;
}

export function TeamSettings({session,onLogout,onWorkspaceChanged}:{session:Session;onLogout:()=>void;onWorkspaceChanged:()=>void}){
 const [members,setMembers]=useState<Member[]>([]);const [workspaces,setWorkspaces]=useState<WorkspaceSummary[]>([]);const [email,setEmail]=useState('');const [role,setRole]=useState<Role>('analyst');const [inviteLink,setInviteLink]=useState('');const [error,setError]=useState('');const [busy,setBusy]=useState(false);
 const [createMerchant,setCreateMerchant]=useState(false);const [merchantName,setMerchantName]=useState('');
 const manage=session.permissions.includes('members')&&!session.demo;
 const sample=!!session.workspace.sample_data;
 const merchantWorkspace=workspaces.find(w=>w.role==='owner'&&!w.sample_data);
 const load=async()=>{try{if(manage)setMembers(await api<Member[]>('/workspace/members'));if(!session.demo)setWorkspaces(await api<WorkspaceSummary[]>('/auth/workspaces'));}catch(e){setError(functionalError(e));}};
 useEffect(()=>{setCreateMerchant(false);setMerchantName('');setInviteLink('');void load();},[session.workspace.id,manage]);
 function switchWorkspace(id:string){setWorkspace(id);onWorkspaceChanged();}
 async function openWorkspace(mode:WorkspaceMode,name=''){
  setBusy(true);setError('');
  try{const next=await api<Session>('/auth/workspace',{method:'POST',body:JSON.stringify({mode,name})});switchWorkspace(next.workspace.id);}catch(e){setError(functionalError(e));}finally{setBusy(false);}
 }
 function startMerchant(){if(merchantWorkspace)switchWorkspace(merchantWorkspace.id);else setCreateMerchant(true);}
 async function invite(e:FormEvent){e.preventDefault();setBusy(true);setError('');try{const r=await api<{token:string}>('/workspace/invites',{method:'POST',body:JSON.stringify({email,role})});setInviteLink(`${location.origin}/#invite=${encodeURIComponent(r.token)}`);setEmail('');}catch(e){setError(functionalError(e));}finally{setBusy(false);}}
 async function update(m:Member,nextRole:Role,active:boolean){setBusy(true);setError('');try{await api(`/workspace/members/${encodeURIComponent(m.id)}`,{method:'PATCH',body:JSON.stringify({role:nextRole,active})});await load();}catch(e){setError(functionalError(e));}finally{setBusy(false);}}
 return <div className="team-settings">
  <section className="card team-account"><div><div className="eyebrow">YOUR ACCESS</div><h2>{session.workspace.name}</h2><p>{session.user.name} · {session.user.email}</p><span className="badge green">{session.role}</span>{sample&&<span className="badge workspace-sample-badge">Sample data</span>}<p>{roleDescriptions[session.role]}</p><p>Workspace names are labels. Teammates join through invitations, even when another workspace has the same name.</p>{session.demo&&<p>This is a shared sample workspace. Create an account for private merchant data.</p>}</div><button className="button" onClick={()=>void logout().then(onLogout).catch(e=>setError(functionalError(e)))}><LogOut size={16}/>{session.demo?'Leave demo':'Sign out'}</button></section>
  {workspaces.length>1&&<section className="card team-account"><label className="field-label">Switch workspace<select value={session.workspace.id} disabled={busy} onChange={e=>switchWorkspace(e.target.value)}>{workspaces.map(w=><option key={w.id} value={w.id}>{w.name}{w.sample_data?' · sample data':''} · {w.role}</option>)}</select></label></section>}
  {!session.demo&&<section className="card workspace-explore">
   <div className="workspace-explore-heading">{sample?<ShoppingBag size={24}/>:<Sparkles size={24}/>}<div><h2>{sample?'Ready for your own store?':'Explore what ReturnRadar can do'}</h2><p>{sample?'Keep this sample store for exploring. Your business records belong in a separate merchant workspace.':'Try a separate sample store with several product categories, return patterns, suggested improvements and review cycles.'}</p></div></div>
   {sample?<>
    <p className="workspace-explore-note">{merchantWorkspace?`Your merchant workspace “${merchantWorkspace.name}” is ready to reopen.`:'Create an empty workspace, then connect your store or import your records.'}</p>
    {createMerchant?<form className="workspace-merchant-form" onSubmit={e=>{e.preventDefault();void openWorkspace('empty',merchantName);}}>
     <label className="field-label">Merchant workspace name<input value={merchantName} maxLength={100} required onChange={e=>setMerchantName(e.target.value)} placeholder="e.g. Everyday Goods" autoFocus/></label>
     <div><button className="button primary" disabled={busy}>{busy?<LoaderCircle className="spin" size={16}/>:<ArrowRight size={16}/>}Create workspace</button><button type="button" className="text-button" disabled={busy} onClick={()=>setCreateMerchant(false)}>Cancel</button></div>
    </form>:<button className="button primary" disabled={busy} onClick={startMerchant}><ArrowRight size={16}/>{merchantWorkspace?'Open my workspace':'Start my workspace'}</button>}
   </>:<><p className="workspace-explore-note">Your current workspace and all its data stay here. Each person gets their own sample copy.</p><button className="button primary" disabled={busy} onClick={()=>void openWorkspace('sample')}>{busy?<LoaderCircle className="spin" size={16}/>:<Sparkles size={16}/>}Explore demo</button></>}
  </section>}
  <section className="card role-guide"><h2><ShieldCheck size={21}/>Who can do what?</h2><div className="role-grid">{Object.entries(roleDescriptions).map(([name,description])=><article key={name}><strong>{name}</strong><p>{description}</p></article>)}</div></section>
  {manage&&<>
   <section className="card team-invite"><h2><Users size={21}/>Invite a teammate</h2><p>Choose their access. Share the invitation link directly; it expires in 48 hours and works only for this email.</p><form onSubmit={invite}><label className="field-label">Teammate email<input type="email" required value={email} onChange={e=>setEmail(e.target.value)}/></label><label className="field-label">Role<select aria-label="Teammate role" value={role} onChange={e=>setRole(e.target.value as Role)}>{(session.role==='owner'?['admin','analyst','viewer']:['analyst','viewer']).map(r=><option key={r} value={r}>{r}</option>)}</select></label><button className="button primary" disabled={busy}><Mail size={16}/>Create invitation</button></form>{inviteLink&&<div className="invite-result"><label className="field-label">Invitation link<input readOnly value={inviteLink}/></label><button className="button small" onClick={()=>void navigator.clipboard.writeText(inviteLink).catch(()=>setError('Select the invitation link and copy it manually.'))}><Copy size={15}/>Copy link</button><small>No email has been sent. Share this link with your teammate.</small></div>}</section>
   <section className="card team-members"><h2>Workspace members</h2><div className="table-scroll"><table><thead><tr><th>MEMBER</th><th>ACCESS</th><th>STATUS</th><th/></tr></thead><tbody>{members.map(m=><tr key={m.id}><td><strong>{m.name}</strong><span className="cell-subtext">{m.email}</span></td><td>{m.role==='owner'||m.id===session.user.id||session.role==='admin'&&m.role==='admin'?<span className="badge">{m.role}</span>:<select aria-label={`Role for ${m.email}`} value={m.role} disabled={busy} onChange={e=>void update(m,e.target.value as Role,m.active)}>{(session.role==='owner'?['admin','analyst','viewer']:['analyst','viewer']).map(r=><option key={r} value={r}>{r}</option>)}</select>}</td><td>{m.active?'Active':'Removed'}</td><td>{m.role!=='owner'&&m.id!==session.user.id&&!(session.role==='admin'&&m.role==='admin')&&<button className="button small" disabled={busy} onClick={()=>void update(m,m.role,!m.active)}>{m.active?'Remove access':'Restore access'}</button>}</td></tr>)}</tbody></table></div></section>
  </>}
  {error&&<div className="error-banner" role="alert">{error}</div>}
 </div>;
}
