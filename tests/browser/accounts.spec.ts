import {test,expect} from '@playwright/test';

const password='Radar123';
const email=(name:string)=>`${name}-${Date.now()}-${Math.random().toString(16).slice(2)}@example.com`;

async function createAccount(page:import('@playwright/test').Page,address:string,workspace='Olive Carry'){
 await page.getByRole('button',{name:'Create account',exact:true}).click();
 await page.getByRole('radio',{name:/Start my workspace/}).check();
 await page.getByLabel('Your name',{exact:true}).fill('Merchant Owner');
 await page.getByLabel('Merchant workspace name').fill(workspace);
 await page.getByLabel('Email address',{exact:true}).fill(address);
 await page.getByLabel('Password',{exact:true}).fill(password);
 await page.getByRole('button',{name:'Create workspace',exact:true}).click();
 await expect(page.getByRole('heading',{name:'A clearer picture of your returns.'})).toBeVisible();
}

test('merchant signup, CSV workflow, settings and returning login',async({page})=>{
 const address=email('owner');
 await page.goto('/');
 await expect(page.getByRole('heading',{name:'Welcome back'})).toBeVisible();
 await page.screenshot({path:'artifacts/merchant-login.png',fullPage:true,animations:'disabled'});
 await createAccount(page,address);
 await expect(page.getByText('Olive Carry',{exact:true})).toBeVisible();
 await expect(page.getByText('0',{exact:true}).first()).toBeVisible();
 await page.getByRole('button',{name:'Import CSV',exact:true}).click();
 await page.getByLabel('Choose CSV file').setInputFiles('public/sample-returns.csv');
 await page.getByRole('button',{name:'Import returns',exact:true}).click();
 await expect(page.getByRole('dialog')).not.toBeVisible();
 await page.getByRole('button',{name:'Return inbox',exact:true}).click();
 await expect(page.getByText('5 records',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Settings',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Who can do what?'})).toBeVisible();
 await page.screenshot({path:'artifacts/merchant-team.png',fullPage:true,animations:'disabled'});
 await page.getByRole('button',{name:'Sign out',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Welcome back'})).toBeVisible();
 await page.getByLabel('Email address',{exact:true}).fill(address);
 await page.getByLabel('Password',{exact:true}).fill(password);
 await page.getByRole('button',{name:'Sign in',exact:true}).last().click();
 await expect(page.getByRole('heading',{name:'A clearer picture of your returns.'})).toBeVisible();
 await page.getByRole('button',{name:'Return inbox',exact:true}).click();
 await expect(page.getByText('5 records',{exact:true})).toBeVisible();
});

test('viewer invitation has read-only controls and revocation denies the API',async({page,browser})=>{
 const ownerEmail=email('inviter');const viewerEmail=email('viewer');
 await page.goto('/');await createAccount(page,ownerEmail,'Team Merchant');
 await page.getByRole('button',{name:'Settings',exact:true}).click();
 await page.getByLabel('Teammate email').fill(viewerEmail);
 await page.getByLabel('Teammate role',{exact:true}).selectOption('viewer');
 await page.getByRole('button',{name:'Create invitation',exact:true}).click();
 const link=await page.getByLabel('Invitation link').inputValue();
 const context=await browser.newContext({viewport:{width:390,height:844}});
 const viewer=await context.newPage();
 await viewer.goto(link);
 await viewer.getByLabel('Your name',{exact:true}).fill('Teammate');
 await viewer.getByLabel('Email address',{exact:true}).fill(viewerEmail);
 await viewer.getByLabel('Password',{exact:true}).fill(password);
 await viewer.getByRole('button',{name:'Create account & join',exact:true}).click();
 await expect(viewer.getByRole('heading',{name:'A clearer picture of your returns.'})).toBeVisible();
 await expect(viewer.getByText(/Viewer access: read dashboards/)).toBeVisible();
 await expect(viewer.getByRole('button',{name:'Import CSV',exact:true})).toBeDisabled();
 await expect(viewer.getByRole('button',{name:'Run analysis',exact:true})).toBeDisabled();
 expect(await viewer.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
 await viewer.screenshot({path:'artifacts/merchant-viewer-mobile.png',fullPage:true,animations:'disabled'});
 await viewer.getByRole('button',{name:'Open navigation'}).click();
 await expect(viewer.getByRole('button',{name:'Ask Radar',exact:true})).toHaveCount(0);
 await viewer.getByRole('button',{name:'Settings',exact:true}).click();
 await expect(viewer.getByRole('heading',{name:'Invite a teammate'})).toHaveCount(0);
 await page.reload();await page.getByRole('button',{name:'Settings',exact:true}).click();
 await page.getByRole('row').filter({hasText:viewerEmail}).getByRole('button',{name:'Remove access'}).click();
 await expect(page.getByRole('row').filter({hasText:viewerEmail})).toContainText('Removed');
 const denied=await viewer.evaluate(async()=>({status:(await fetch('/api/dashboard')).status}));
 expect(denied.status).toBe(403);
 await context.close();
});
