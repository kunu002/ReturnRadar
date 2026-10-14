import {test, expect, type Page} from '@playwright/test';

const password = 'Radar123!';
const address = (prefix: string) => `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}@example.com`;

async function signup(page: Page, email: string) {
 await page.goto('/');
 await page.getByRole('button', {name:'Create account', exact:true}).click();
 await page.getByRole('radio', {name:/Start my workspace/}).check();
 await page.getByLabel('Your name', {exact:true}).fill('Connection Owner');
 await page.getByLabel('Merchant workspace name').fill('Connector Review Store');
 await page.getByLabel('Email address', {exact:true}).fill(email);
 await page.getByLabel('Password', {exact:true}).fill(password);
 await page.getByRole('button', {name:'Create workspace', exact:true}).click();
 await expect(page.getByRole('heading', {name:'A clearer picture of your returns.'})).toBeVisible();
}

test('Data Connections offers a real CSV import and available store connections', async ({page}) => {
 await signup(page, address('connections'));
 await page.getByRole('button', {name:'Data Connections', exact:true}).click();
 await expect(page.getByRole('heading', {name:'Data Connections', exact:true})).toBeVisible();
 await expect(page.getByRole('heading', {name:'Shopify', exact:true})).toBeVisible();
 await expect(page.getByRole('heading', {name:'WooCommerce', exact:true})).toBeVisible();
 await expect(page.getByRole('button', {name:'Connect Shopify', exact:true})).toBeEnabled();
 await expect(page.getByRole('button', {name:'Connect WooCommerce', exact:true})).toBeEnabled();
 await expect(page.getByText('0 orders', {exact:true})).toBeVisible();
 await page.getByRole('button', {name:'Connect Shopify', exact:true}).click();
 const guide = page.getByRole('dialog');
 await expect(guide).toBeVisible();
 await guide.getByText('How to get your connection details', {exact:true}).click();
 await expect(guide).toContainText(/read products, orders, and returns/i);
 await guide.getByRole('button', {name:'Close connection setup', exact:true}).click();
 await page.locator('.source-card').filter({has:page.getByRole('heading', {name:'Shopify', exact:true})}).getByRole('button', {name:'Import a prepared CSV'}).click();
 const importer = page.getByRole('dialog');
 await expect(importer.getByLabel('Choose CSV file')).toBeVisible();
 await expect(importer.getByLabel('Export platform')).toHaveValue('shopify');
 await importer.getByLabel('Choose CSV file').setInputFiles('public/sample-returns.csv');
 await importer.getByRole('button', {name:'Import returns', exact:true}).click();
 await expect(page.getByRole('dialog')).not.toBeVisible();

 const data = await page.evaluate(async () => (await fetch('/api/connections')).json());
 expect(data.coverage.returns.records).toBe(5);
 expect(data.source_counts.csv).toBe(5);
 expect(data.imports[0].platform).toBe('shopify');
 expect(data.connectors.find((item: {id:string}) => item.id === 'shopify').status).toBe('available');
 await page.screenshot({path:'artifacts/data-connections.png', fullPage:true, animations:'disabled'});

 await page.getByRole('button', {name:'View update history', exact:true}).click();
 await expect(page.getByRole('heading', {name:'Recent activity', exact:true})).toBeVisible();
});

test('Data Connections stays usable on mobile and does not claim sample data was imported', async ({page}) => {
 await page.setViewportSize({width:390, height:844});
 await page.addInitScript(() => sessionStorage.setItem('rr_demo', 'true'));
 await page.goto('/');
 await page.getByRole('button', {name:'Open navigation', exact:true}).click();
 await page.getByRole('button', {name:'Data Connections', exact:true}).click();
 await expect(page.getByRole('heading', {name:'Data Connections', exact:true})).toBeVisible();
 await expect(page.getByRole('button', {name:'Import return CSV', exact:true})).toBeEnabled();
 const data = await page.evaluate(async () => (await fetch('/api/connections', {headers:{'X-Demo-Workspace':'true'}})).json());
 expect(data.coverage.returns.records).toBeGreaterThanOrEqual(160);
 expect(data.source_counts.synthetic).toBe(160);
 expect(data.imports.every((run: {platform:string}) => run.platform !== 'synthetic')).toBe(true);
 expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
 await expect(page.getByRole('button', {name:'Connect WooCommerce', exact:true})).toBeDisabled();
 await expect(page.getByText(/WooCommerce provides refunds and refund reasons/)).toBeVisible();
 expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
 await page.screenshot({path:'artifacts/data-connections-mobile.png', fullPage:true, animations:'disabled'});
});

test('Store setup checks credentials, updates data, and disconnects through functional flows', async ({page}) => {
 await signup(page, address('store-flow'));
 const summary={stores:[] as object[],coverage:{returns:{records:0},sales:{orders:0},refunds:{records:0}},source_counts:{},imports:[],updates:[],active_updates:[]};
 const store={id:'mock-store',platform:'woocommerce',url:'https://shop.example.com',name:'Review Shoe Store',status:'connected',connected_at:new Date().toISOString(),last_sync:null as object|null,last_error:null};
 let updateRequested=false;
 await page.route('**/api/connections', async route=>{
  if(route.request().method()==='POST'){summary.stores=[store];await route.fulfill({json:store});}
  else await route.fulfill({json:summary});
 });
 await page.route('**/api/connections/test', route=>route.fulfill({json:{name:'Review Shoe Store',verified:true}}));
 await page.route('**/api/connections/mock-store/sync', route=>{updateRequested=true;return route.fulfill({json:{event_id:'update-1',status:'pending'}});});
 await page.route('**/api/jobs', route=>{
  if(updateRequested){store.last_sync={products:2,orders:5,returns:1,refunds:1,at:new Date().toISOString()};summary.coverage.sales.orders=5;}
  return route.fulfill({json:updateRequested?[{id:'update-1',status:'complete',kind:'connector_sync'}]:[]});
 });
 await page.route('**/api/connections/mock-store/disconnect', route=>{summary.stores=[];return route.fulfill({json:{disconnected:true}});});
 await page.getByRole('button', {name:'Data Connections',exact:true}).click();
 await page.getByRole('button', {name:'Connect WooCommerce',exact:true}).click();
 const modal=page.getByRole('dialog');
 await modal.getByLabel('Store address',{exact:true}).fill('https://shop.example.com');
 await modal.getByLabel('Consumer key',{exact:true}).fill('ck_example');
 await modal.getByLabel('Consumer secret',{exact:true}).fill('cs_example');
 await modal.getByRole('button', {name:'Check connection',exact:true}).click();
 await expect(modal.getByText('Review Shoe Store is ready to connect.')).toBeVisible();
 await modal.getByRole('button', {name:'Save connection',exact:true}).click();
 await expect(page.getByRole('heading', {name:'Review Shoe Store Connected',exact:true})).toBeVisible();
 await page.getByRole('button', {name:'Update data',exact:true}).click();
 await expect(page.getByText('Review Shoe Store is up to date. Run analysis to refresh your recommendations.')).toBeVisible();
 await expect(page.getByText('5 orders',{exact:true})).toBeVisible();
 await page.getByRole('button', {name:'Disconnect',exact:true}).click();
 await expect(page.getByRole('dialog')).toContainText('Your imported records and review history remain available.');
 await page.getByRole('button', {name:'Disconnect store',exact:true}).click();
 await expect(page.getByText('Review Shoe Store is disconnected. Your imported data is still available.')).toBeVisible();
 await expect(page.getByRole('button', {name:'Update data',exact:true})).toHaveCount(0);
});

test('Shopify uses app credentials and explains why a dashboard link is not a store address',async({page})=>{
 await signup(page,address('shopify-app'));
 const payloads:Record<string,string>[]=[];
 await page.route('**/api/connections/test',route=>{payloads.push(route.request().postDataJSON());return route.fulfill({json:{name:'Owned Shopify Store',verified:true}});});
 await page.getByRole('button',{name:'Data Connections',exact:true}).click();
 await page.getByRole('button',{name:'Connect Shopify',exact:true}).click();
 const form=page.getByRole('dialog');
 await form.getByLabel('Shopify connection method',{exact:true}).selectOption('client_credentials');
 await expect(form.getByLabel('Store access token',{exact:true})).toHaveCount(0);
 await form.getByLabel('Client ID',{exact:true}).fill('client-id');
 await form.getByLabel('Client secret',{exact:true}).fill('private-app-secret');
 await form.getByLabel('Store address',{exact:true}).fill('https://dev.shopify.com/dashboard/239070117/apps/433642668033/settings');
 await form.getByRole('button',{name:'Check connection',exact:true}).click();
 await expect(form.getByRole('alert')).toContainText('not an app settings link');
 expect(payloads).toHaveLength(0);
 await form.getByLabel('Store address',{exact:true}).fill('merchant.myshopify.com');
 await form.getByRole('button',{name:'Check connection',exact:true}).click();
 await expect(form.getByText('Owned Shopify Store is ready to connect.')).toBeVisible();
 expect(payloads[0]).toMatchObject({platform:'shopify',url:'merchant.myshopify.com',shopify_auth:'client_credentials',client_id:'client-id',client_secret:'private-app-secret',access_token:''});
 await expect(form.getByRole('alert')).toHaveCount(0);
});

test('Shopify reconnect restores the saved connection method and clears unsaved credentials',async({page})=>{
 await signup(page,address('shopify-reconnect'));
 const store={id:'owned-shopify',platform:'shopify',shopify_auth:'client_credentials',url:'https://merchant.myshopify.com',name:'Owned Shopify Store',status:'connected',connected_at:new Date().toISOString(),last_sync:null,last_error:null};
 await page.route('**/api/connections',route=>route.fulfill({json:{stores:[store],coverage:{returns:{records:0},sales:{orders:0}},source_counts:{},imports:[],updates:[],active_updates:[]}}));
 await page.getByRole('button',{name:'Data Connections',exact:true}).click();
 await page.getByRole('button',{name:'Reconnect',exact:true}).click();
 const form=page.getByRole('dialog');
 await expect(form.getByLabel('Shopify connection method',{exact:true})).toHaveValue('client_credentials');
 await expect(form.getByLabel('Store address',{exact:true})).toHaveValue(store.url);
 await expect(form.getByLabel('Client ID',{exact:true})).toHaveValue('');
 await expect(form.getByLabel('Client secret',{exact:true})).toHaveValue('');
 await expect(form.getByLabel('Store access token',{exact:true})).toHaveCount(0);
 await form.getByLabel('Client ID',{exact:true}).fill('unsaved-client-id');
 await form.getByLabel('Client secret',{exact:true}).fill('unsaved-client-secret');
 await form.getByLabel('Shopify connection method',{exact:true}).selectOption('access_token');
 await form.getByLabel('Store access token',{exact:true}).fill('unsaved-access-token');
 await form.getByRole('button',{name:'Close connection setup',exact:true}).click();
 await page.getByRole('button',{name:'Reconnect',exact:true}).click();
 await expect(form.getByLabel('Shopify connection method',{exact:true})).toHaveValue('client_credentials');
 await expect(form.getByLabel('Client ID',{exact:true})).toHaveValue('');
 await expect(form.getByLabel('Client secret',{exact:true})).toHaveValue('');
 await form.getByLabel('Shopify connection method',{exact:true}).selectOption('access_token');
 await expect(form.getByLabel('Store access token',{exact:true})).toHaveValue('');
});
