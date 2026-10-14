import {test,expect,type APIRequestContext} from '@playwright/test';

const password='Invite123!';
const email=(prefix:string)=>`${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}@example.com`;

async function register(client:APIRequestContext,address:string,workspace:string,invite=''){
 const response=await client.post('/api/auth/register',{data:{name:'Review teammate',email:address,password,workspace_name:workspace,invite_token:invite}});
 expect(response.ok()).toBeTruthy();
 return response.json();
}

async function invite(client:APIRequestContext,address:string,role='analyst'){
 const response=await client.post('/api/workspace/invites',{data:{email:address,role}});
 expect(response.ok()).toBeTruthy();
 return (await response.json()).token as string;
}

test('an inviter can switch accounts without losing the teammate invitation',async({page})=>{
 const owner=email('inviter');const teammate=email('teammate');
 await register(page.request,owner,'Invitation store');
 const token=await invite(page.request,teammate,'admin');
 await page.goto(`/#invite=${encodeURIComponent(token)}`);
 await expect(page.getByRole('heading',{name:'Accept your invitation'})).toBeVisible();
 await expect(page.getByText(owner,{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Join workspace',exact:true}).click();
 await expect(page.getByRole('alert')).toContainText('different email address');
 await page.getByRole('button',{name:'Use a different account',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Join your merchant team'})).toBeVisible();
 expect(new URL(page.url()).hash).toContain('invite=');
 await page.getByLabel('Your name',{exact:true}).fill('Teammate');
 await page.getByLabel('Email address',{exact:true}).fill(teammate);
 await page.getByLabel('Password',{exact:true}).fill(password);
 await page.getByRole('button',{name:'Create account & join',exact:true}).click();
 await expect(page.getByRole('heading',{name:'A clearer picture of your returns.'})).toBeVisible();
 const session=await (await page.request.get('/api/auth/session')).json();
 expect(session.role).toBe('admin');
 expect(session.user.email).toBe(teammate);
 expect(new URL(page.url()).hash).toBe('');
});

test('an existing teammate can accept a new invitation after access to an old workspace was removed',async({page,request})=>{
 await register(request,email('first-owner'),'Former store');
 const teammate=email('returning-teammate');
 const initial=await register(page.request,teammate,'',await invite(request,teammate));
 const removed=await request.patch(`/api/workspace/members/${initial.user.id}`,{data:{role:'analyst',active:false}});
 expect(removed.ok()).toBeTruthy();
 expect((await page.request.get('/api/auth/session')).status()).toBe(403);
 await request.post('/api/auth/logout');
 await register(request,email('next-owner'),'Next store');
 const token=await invite(request,teammate,'viewer');
 await page.goto(`/?campaign=review&invite=${encodeURIComponent(token)}`);
 await expect(page.getByRole('heading',{name:'Accept your invitation'})).toBeVisible();
 await expect(page.getByText(teammate,{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Join workspace',exact:true}).click();
 await expect(page.getByRole('heading',{name:'A clearer picture of your returns.'})).toBeVisible();
 await expect(page.getByText('Next store',{exact:true})).toBeVisible();
 await expect(page.getByRole('button',{name:'Run analysis',exact:true})).toBeDisabled();
 expect(new URL(page.url()).searchParams.get('invite')).toBeNull();
 expect(new URL(page.url()).searchParams.get('campaign')).toBe('review');
 const session=await (await page.request.get('/api/auth/session')).json();
 expect(session.role).toBe('viewer');
});

test('an invitation exits the shared sample workspace and opens merchant signup',async({page,request})=>{
 await page.goto('/?demo=1');
 await expect(page.getByRole('heading',{name:'A clearer picture of your returns.'})).toBeVisible();
 await register(request,email('owner'),'Private store');
 const teammate=email('sample-reviewer');
 const token=await invite(request,teammate,'viewer');
 await page.goto(`/#invite=${encodeURIComponent(token)}`);
 await expect(page.getByRole('heading',{name:'Join your merchant team'})).toBeVisible();
 expect(await page.evaluate(()=>sessionStorage.getItem('rr_demo'))).toBeNull();
 await page.getByLabel('Your name',{exact:true}).fill('Sample reviewer');
 await page.getByLabel('Email address',{exact:true}).fill(teammate);
 await page.getByLabel('Password',{exact:true}).fill(password);
 await page.getByRole('button',{name:'Create account & join',exact:true}).click();
 await expect(page.getByRole('heading',{name:'A clearer picture of your returns.'})).toBeVisible();
 const data=await (await page.request.get('/api/dashboard')).json();
 expect(data.counts.returns).toBe(0);
});
