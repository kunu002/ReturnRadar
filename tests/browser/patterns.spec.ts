import {test,expect} from '@playwright/test';
import type {Dashboard,Pattern,Product} from '../../src/types';

test('a slower source response cannot replace the latest dashboard selection',async({page})=>{
 await page.addInitScript(()=>sessionStorage.setItem('rr_demo','true'));
 let startShop:()=>void=()=>{};let releaseShop:()=>void=()=>{};
 const shopStarted=new Promise<void>(resolve=>{startShop=resolve;});
 const shopReleased=new Promise<void>(resolve=>{releaseShop=resolve;});
 await page.route('**/api/dashboard?*',async route=>{
  const source=new URL(route.request().url()).searchParams.get('source')||'all';
  if(source==='shopify'){startShop();await shopReleased;}
  const data:Dashboard={returns:[],products:[],patterns:[],reasons:[],trend:[],actions:[],counts:{returns:0,patterns:0,pending:source==='all'?9:source==='shopify'?3:1,fit_ready:0},analysis:null,last_sync:null,source_counts:{},days:28};
  await route.fulfill({json:data});
 });
 await page.goto('/');
 const count=page.locator('.stat-card').filter({hasText:'Actions to review'}).locator('.stat-number');
 await expect(count).toHaveText('9');
 await page.getByRole('combobox',{name:'Data source',exact:true}).selectOption('shopify');
 await shopStarted;
 await page.getByRole('combobox',{name:'Data source',exact:true}).selectOption('woocommerce');
 await expect(count).toHaveText('1');
 const staleResponse=page.waitForResponse(response=>response.url().includes('/api/dashboard')&&response.url().includes('source=shopify'));
 releaseShop();await staleResponse;
 await expect(page.getByRole('combobox',{name:'Data source',exact:true})).toHaveValue('woocommerce');
 await expect(count).toHaveText('1');
});

test('dashboard preview opens every pattern and keeps exploration filters useful',async({page})=>{
 await page.addInitScript(()=>sessionStorage.setItem('rr_demo','true'));
 const products:Product[]=Array.from({length:13},(_,index)=>({id:`p-${index}`,name:`Pattern product ${index+1}`,variant:'Default',sku:`SKU-${index}`,price:null,description:'Product details.',listing_claim:'Product details.',source:'synthetic',image:null}));
 const patterns:Pattern[]=products.map((product,index)=>({id:`pattern-${index}`,product_id:product.id,product_name:product.name,variant:'Default',issue:index%2?'Packaging':'Size & fit',count:8,sample_size:12,share:66.7,recent_count:5,recent_sample:6,previous_count:3,previous_sample:6,change_pp:33.3,emerging:index<5,evidence:[],minimum_sample:5,listing_history_available:false}));
 const data:Dashboard={returns:[],products,patterns,reasons:[],trend:[],actions:[],counts:{returns:104,patterns:13,pending:0,fit_ready:0},analysis:null,last_sync:null,source_counts:{synthetic:104},days:28};
 await page.route('**/api/dashboard?*',route=>route.fulfill({json:data}));
 await page.goto('/');
 await expect(page.getByRole('button',{name:/Investigate .* for Pattern product/})).toHaveCount(4);
 await page.getByRole('button',{name:'See all 13 patterns',exact:true}).click();
 await expect(page.getByRole('heading',{name:'All return patterns',exact:true})).toBeVisible();
 await expect(page.getByRole('button',{name:/Investigate .* for Pattern product/})).toHaveCount(13);
 await page.getByRole('combobox',{name:'Pattern issue',exact:true}).selectOption('Packaging');
 await expect(page.getByRole('button',{name:/Investigate .* for Pattern product/})).toHaveCount(6);
 await page.getByRole('textbox',{name:'Search patterns',exact:true}).fill('Pattern product 12');
 await expect(page.getByRole('button',{name:'Investigate Packaging for Pattern product 12'})).toBeVisible();
 await expect(page.getByRole('button',{name:/Investigate .* for Pattern product/})).toHaveCount(1);
 await page.getByRole('button',{name:'Reset filters',exact:true}).click();
 await page.getByRole('combobox',{name:'Pattern signal',exact:true}).selectOption('emerging');
 await expect(page.getByRole('button',{name:/Investigate .* for Pattern product/})).toHaveCount(5);
 await page.setViewportSize({width:390,height:844});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
 await page.screenshot({path:'artifacts/all-patterns-mobile.png',fullPage:true,animations:'disabled'});
});
