import {test,expect,type Page} from '@playwright/test';
import type {Dashboard,Session} from '../../src/types';

const email=()=>`sample-reviewer-${Date.now()}-${Math.random().toString(16).slice(2)}@example.com`;
async function createSampleAccount(page:Page){
 await page.goto('/');
 await page.getByRole('button',{name:'Create account',exact:true}).click();
 await expect(page.getByRole('radio',{name:/Explore demo/})).toBeChecked();
 await page.getByLabel('Your name',{exact:true}).fill('Demo Reviewer');
 await page.getByLabel('Email address',{exact:true}).fill(email());
 await page.getByLabel('Password',{exact:true}).fill('Radar123');
 await page.getByRole('button',{name:'Create account & explore demo',exact:true}).click();
 await expect(page.getByRole('heading',{name:'A clearer picture of your returns.'})).toBeVisible();
 await expect(page.getByRole('region',{name:'Sample workspace',exact:true})).toContainText('Your private copy');
}

test('a personal sample store shows the workflow and preserves both workspaces when switching',async({page})=>{
 await createSampleAccount(page);
 await expect(page.getByRole('region',{name:'Explore the return reduction workflow'})).toBeVisible();
 await page.getByRole('button',{name:'Products',exact:true}).click();
 await expect(page.getByText('18 products',{exact:true})).toBeVisible();
 for(const name of ['Everyday Cotton Shirt','Everyday Running Shoes','Wireless Headphones','Travel Coffee Mug','Wooden Puzzle Set'])await expect(page.getByRole('heading',{name,exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Product Reviews',exact:true}).click();
 await expect(page.locator('.review-cycle')).toHaveCount(3);
 await expect(page.locator('.review-cycle').first()).toContainText('Planned');
 await page.getByRole('button',{name:'Settings',exact:true}).click();
 await page.locator('.workspace-explore').getByRole('button',{name:'Start my workspace',exact:true}).click();
 await page.getByLabel('Merchant workspace name').fill('My actual store');
 await page.getByRole('button',{name:'Create workspace',exact:true}).click();
 await expect(page.getByRole('region',{name:'Sample workspace',exact:true})).toHaveCount(0);
 await expect(page.locator('.store-switch')).toContainText('My actual store');
 await page.getByRole('button',{name:'Import CSV',exact:true}).click();
 await page.getByLabel('Choose CSV file').setInputFiles('public/sample-returns.csv');
 await page.getByRole('button',{name:'Import returns',exact:true}).click();
 await expect(page.getByRole('dialog')).not.toBeVisible();
 await page.getByRole('combobox',{name:'Data source',exact:true}).selectOption('csv');
 await page.getByRole('button',{name:'Settings',exact:true}).click();
 await page.getByRole('button',{name:'Explore demo',exact:true}).click();
 await expect(page.locator('.store-switch')).toContainText('My sample store');
 await expect(page.getByRole('combobox',{name:'Data source',exact:true})).toHaveValue('all');
 await page.getByRole('button',{name:'Return inbox',exact:true}).click();
 await expect(page.getByText('256 records',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Settings',exact:true}).click();
 await page.getByRole('button',{name:'Open my workspace',exact:true}).click();
 await page.getByRole('button',{name:'Return inbox',exact:true}).click();
 await expect(page.getByText('5 records',{exact:true})).toBeVisible();
});

test('different reviewers receive independent sample workspaces even with matching names',async({page,browser})=>{
 await createSampleAccount(page);
 const first=await page.request.get('/api/auth/session');
 const firstSession=await first.json() as Session;
 expect(firstSession.demo).toBe(false);
 expect(firstSession.workspace.sample_data).toBe(true);
 await page.getByRole('button',{name:'Import CSV',exact:true}).click();
 await page.getByLabel('Choose CSV file').setInputFiles('public/sample-returns.csv');
 await page.getByRole('button',{name:'Import returns',exact:true}).click();
 await expect(page.getByRole('dialog')).not.toBeVisible();
 const context=await browser.newContext({viewport:{width:390,height:844}});
 const other=await context.newPage();
 await createSampleAccount(other);
 const second=await other.request.get('/api/auth/session');
 const secondSession=await second.json() as Session;
 expect(secondSession.workspace.name).toBe(firstSession.workspace.name);
 expect(secondSession.workspace.id).not.toBe(firstSession.workspace.id);
 const denied=await other.request.get('/api/dashboard',{headers:{'X-Workspace-ID':firstSession.workspace.id}});
 expect(denied.status()).toBe(403);
 const dashboard=await other.request.get('/api/dashboard?days=28&source=all');
 expect((await dashboard.json() as Dashboard).counts.returns).toBe(256);
 expect(await other.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
 await other.screenshot({path:'artifacts/private-demo-mobile.png',fullPage:true,animations:'disabled'});
 await context.close();
});

for(const mode of ['sample','empty'] as const){
 test(`first signed-in workspace setup requires an explicit ${mode} choice`,async({page})=>{
  const session:Session={user:{id:'new-reviewer',name:'Jury Reviewer',email:'jury@example.com'},workspace:{id:`onboarding-${mode}`,name:mode==='sample'?'My sample store':'My real store',sample_data:mode==='sample'},role:'owner',permissions:['read','import','analyse','propose','approve','members','chat'],demo:false};
  const data:Dashboard={returns:[],products:[],patterns:[],reasons:[],trend:[],actions:[],counts:{returns:0,patterns:0,pending:0,fit_ready:0},analysis:null,last_sync:null,source_counts:{},days:28};
  let created=false;
  const submissions:{mode:string;name:string}[]=[];
  await page.route('**/api/config',route=>route.fulfill({json:{demo:false,auth_provider:'local',firebase:{apiKey:'',authDomain:'',projectId:''}}}));
  await page.route('**/api/auth/session',route=>route.fulfill({json:created?session:{needs_workspace:true}}));
  await page.route('**/api/auth/identity',route=>route.fulfill({json:{user:session.user}}));
  await page.route('**/api/auth/workspace',route=>{submissions.push(route.request().postDataJSON());created=true;return route.fulfill({json:session});});
  await page.route('**/api/dashboard?*',route=>route.fulfill({json:data}));
  await page.route('**/api/reviews',route=>route.fulfill({json:{cycles:[],counts:{planned:0,active:0,completed:0,due:0},products:[]}}));
  await page.goto('/');
  await expect(page.getByRole('heading',{name:'Choose your starting point'})).toBeVisible();
  await expect(page.getByText('Signed in as',{exact:false})).toContainText('jury@example.com');
  expect(submissions).toHaveLength(0);
  if(mode==='empty'){
   await page.getByRole('radio',{name:/Start my workspace/}).check();
   await page.getByRole('button',{name:'Start my workspace',exact:true}).click();
   expect(submissions).toHaveLength(0);
   await page.getByLabel('Merchant workspace name').fill('My real store');
  }else await expect(page.getByLabel('Sample workspace name (optional)')).toHaveValue('');
  await page.getByRole('button',{name:mode==='sample'?'Explore demo':'Start my workspace',exact:true}).click();
  await expect(page.getByRole('heading',{name:'A clearer picture of your returns.'})).toBeVisible();
  expect(submissions).toEqual([{mode,name:mode==='sample'?'':'My real store'}]);
  await expect(page.getByRole('region',{name:'Sample workspace',exact:true})).toHaveCount(mode==='sample'?1:0);
 });
}
