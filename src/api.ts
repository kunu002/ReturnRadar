import { initializeApp, getApps } from 'firebase/app';
import { getAuth, GoogleAuthProvider, signInWithPopup, onAuthStateChanged, signOut, type User, type Auth } from 'firebase/auth';

const base=import.meta.env?.VITE_API_URL || '';
let user:User|null=null;
let signIn: (()=>Promise<unknown>)|null=null;
let firebaseAuth:Auth|null=null;
export let authConfig={provider:'local',demo:false};
export function pendingInvitation(){return new URLSearchParams(location.hash.slice(1)).get('invite')||new URLSearchParams(location.search).get('invite')||'';}
export function clearInvitation(){
  const address=new URL(location.href);
  address.searchParams.delete('invite');
  const hash=new URLSearchParams(address.hash.slice(1));
  hash.delete('invite');
  address.hash=hash.toString();
  history.replaceState({},'',address.pathname+address.search+address.hash);
}
export function setWorkspace(id:string){sessionStorage.setItem('rr_workspace',id);}
export function exploreDemo(){sessionStorage.setItem('rr_demo','true');sessionStorage.removeItem('rr_workspace');}
export function leaveDemo(){sessionStorage.removeItem('rr_demo');sessionStorage.removeItem('rr_workspace');}
export async function logout(){await api('/auth/logout',{method:'POST'});if(firebaseAuth)await signOut(firebaseAuth);user=null;leaveDemo();}
export async function initializeAuth(onChange:(state:'demo'|'signed_in'|'signed_out')=>void){
  const config=await api<{demo:boolean;auth_provider:string;firebase:{apiKey:string;authDomain:string;projectId:string}}>('/config');
  authConfig={provider:config.auth_provider,demo:config.demo};
  if(pendingInvitation())leaveDemo();
  else if(new URLSearchParams(location.search).get('demo')==='1')exploreDemo();
  if(config.demo&&sessionStorage.getItem('rr_demo')==='true'){onChange('demo');return;}
  if(config.auth_provider==='local'){
    try{await api('/auth/session');onChange('signed_in');}catch(e){if((e as APIError).status===401)onChange('signed_out');else if((e as APIError).status===403)onChange('signed_in');else throw e;}
    return;
  }
  if(!config.firebase.apiKey)throw new Error('Seller sign-in is not configured. Set the Firebase web configuration on the server.');
  const auth=getAuth(getApps()[0]||initializeApp(config.firebase));
  firebaseAuth=auth;
  signIn=()=>signInWithPopup(auth,new GoogleAuthProvider());
  onAuthStateChanged(auth,u=>{user=u;onChange(u?'signed_in':'signed_out');});
}
export async function login(){if(signIn)await signIn();}
export class APIError extends Error{constructor(message:string,public status:number){super(message);}}
export async function api<T=unknown>(path:string,options:RequestInit={}):Promise<T>{
  const headers=new Headers(options.headers);
  if(user)headers.set('Authorization',`Bearer ${await user.getIdToken()}`);
  if(sessionStorage.getItem('rr_demo')==='true')headers.set('X-Demo-Workspace','true');
  const workspace=sessionStorage.getItem('rr_workspace');
  if(workspace)headers.set('X-Workspace-ID',workspace);
  if(options.body && !(options.body instanceof FormData))headers.set('Content-Type','application/json');
  const response=await fetch(`${base}/api${path}`,{...options,headers,credentials:'include'});
  if(!response.ok){
    const data=await response.json().catch(()=>({detail:'The server could not complete this request.'}));
    if(response.status===401&&!path.startsWith('/auth/'))window.dispatchEvent(new Event('rr-session-expired'));
    throw new APIError(typeof data.detail==='string'?data.detail:'Please check your inputs and try again.',response.status);
  }
  return response.json();
}
