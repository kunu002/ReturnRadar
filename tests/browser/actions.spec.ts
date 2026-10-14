import {test,expect,type Page} from '@playwright/test';
import type {Action,Dashboard,Product} from '../../src/types';

const session={user:{id:'actions-owner',name:'Asha Merchant',email:'asha@example.com'},workspace:{id:'workspace-actions',name:'Everyday Store'},role:'owner',permissions:['read','import','analyse','chat','propose','approve','manage_team'],demo:true};
const products:Product[]=[
 {id:'bag',name:'Everyday Backpack',variant:'Olive',sku:'BAG-01',price:2499,description:'A daily carry bag.',listing_claim:'Fits daily essentials.',source:'synthetic',image:null,category:'Bags'},
 {id:'shoes',name:'Trail Running Shoes',variant:'Blue',sku:'SHOE-01',price:3999,description:'Shoes for everyday runs.',listing_claim:'Lightweight running shoes.',source:'shopify',image:null,category:'Footwear'},
 {id:'headphones',name:'Wireless Headphones',variant:'Black',sku:'AUDIO-01',price:4999,description:'Wireless headphones.',listing_claim:'Wireless listening.',source:'woocommerce',image:null,category:'Electronics'},
 {id:'shirt',name:'Cotton Everyday Shirt',variant:'White',sku:'SHIRT-01',price:1299,description:'Cotton shirt.',listing_claim:'Daily comfort.',source:'csv',image:null,category:'Clothing'},
 {id:'mug',name:'Ceramic Travel Mug',variant:'Sage',sku:'MUG-01',price:799,description:'A ceramic travel mug.',listing_claim:'For your daily coffee.',source:'synthetic',image:null,category:'Kitchen'},
];
const actions:Action[]=[
 {id:'fit-draft',product_id:'bag',kind:'fit_check',title:'Help buyers check their laptop fit',text:'Check the laptop compartment before sharing its measurements.',status:'draft',issue:'Laptop fit',source:'synthetic',width:26,height:37,depth:3,measurements_verified:false},
 {id:'shoe-sizing',product_id:'shoes',kind:'buyer_guide',guide_type:'footwear',checklist:['Compare your foot length with the verified size chart.','Check the width before choosing your size.'],guidance_verified:false,title:'Clarify shoe width and sizing',text:'Verify width and size measurements before adding a buyer size guide.',status:'draft',issue:'Size & fit',source:'shopify'},
 {id:'audio-compatibility',product_id:'headphones',kind:'buyer_guide',guide_type:'compatibility',checklist:['Check that your device supports the required wireless connection.'],guidance_verified:false,title:'Explain supported devices',text:'Check supported devices and connections against the product specifications.',status:'draft',issue:'Description mismatch',source:'woocommerce'},
 {id:'shirt-care',product_id:'shirt',kind:'buyer_guide',guide_type:'care',checklist:['Check the care label before washing.'],guidance_verified:false,title:'Clarify washing and care',text:'Check the care label and describe the recommended washing instructions.',status:'draft',issue:'Product quality',source:'csv'},
 {id:'mug-quality',product_id:'mug',kind:'quality_check',title:'Check the lid seal',text:'Inspect samples and record whether the lid seal is intact.',status:'draft',issue:'Product quality',source:'synthetic'},
 {id:'mug-packaging',product_id:'mug',kind:'packaging_check',title:'Protect the mug in transit',text:'Test cushioning with a packed sample before changing the dispatch process.',status:'draft',issue:'Packaging',source:'woocommerce'},
 {id:'shoe-inspection',product_id:'shoes',kind:'inspection',title:'Inspect shoe stitching',text:'Check a sample from the affected stock for loose seams.',status:'draft',issue:'Product quality',source:'shopify'},
 {id:'fit-approved',product_id:'bag',kind:'fit_check',title:'Share a verified laptop fit check',text:'Compare your laptop against the verified compartment.',status:'approved',issue:'Laptop fit',source:'synthetic',width:26,height:37,depth:3,measurements_verified:true},
 {id:'fit-unverified',product_id:'bag',kind:'fit_check',title:'An older fit decision needs measurements',text:'Check these dimensions before sharing a fit check.',status:'approved',issue:'Laptop fit',source:'synthetic',width:26,height:37,depth:3,measurements_verified:false},
 {id:'shoe-sizing-approved',product_id:'shoes',kind:'buyer_guide',guide_type:'footwear',checklist:['Compare your foot length with the verified size chart.'],guidance_verified:true,title:'A verified shoe size guide',text:'Add the checked measurements to the shoe size guide.',status:'approved',issue:'Size & fit',source:'shopify'},
 {id:'shirt-care-unverified',product_id:'shirt',kind:'buyer_guide',guide_type:'care',checklist:['Check the care label before washing.'],guidance_verified:false,title:'Care instructions need verification',text:'Review the care label before sharing this guide.',status:'approved',issue:'Product quality',source:'csv'},
 {id:'inspection-approved',product_id:'mug',kind:'inspection',title:'Inspect the mug lid seal',text:'Check the lid seals on the affected stock.',status:'approved',issue:'Product quality',source:'synthetic'},
 {id:'mug-listing-dismissed',product_id:'mug',kind:'listing',title:'Reconsider the mug description',text:'Review the product facts before changing the description.',status:'dismissed',issue:'Description mismatch',source:'synthetic'},
];

async function openActions(page:Page){
 await page.addInitScript(()=>sessionStorage.setItem('rr_demo','true'));
 await page.route('**/api/auth/session',route=>route.fulfill({json:session}));
 await page.route('**/api/dashboard?*',route=>{
  const source=new URL(route.request().url()).searchParams.get('source')||'all';
  const included=actions.filter(action=>source==='all'||action.source===source);
  const dashboard:Dashboard={returns:[],products,patterns:[],reasons:[],trend:[],actions:included,counts:{returns:0,patterns:0,pending:included.filter(action=>action.status==='draft').length,fit_ready:included.filter(action=>action.kind==='fit_check'&&action.status==='approved'&&action.measurements_verified).length},analysis:null,last_sync:null,source_counts:{synthetic:5,shopify:3,woocommerce:2,csv:1},days:28};
  return route.fulfill({json:dashboard});
 });
 await page.goto('/');
 if((page.viewportSize()?.width||1440)<760)await page.getByRole('button',{name:'Open navigation'}).click();
 await page.getByRole('button',{name:'Action center',exact:false}).first().click();
 await expect(page.getByRole('heading',{name:'Find an action'})).toBeVisible();
}

test('Action center combines product, issue, type and text filters and resets empty results',async({page})=>{
 await openActions(page);
 await expect(page.locator('.action-card')).toHaveCount(7);
 await page.getByRole('combobox',{name:'Action product',exact:true}).selectOption('shoes');
 await expect(page.locator('.action-card')).toHaveCount(2);
 await expect(page.locator('.action-result-count')).toHaveText('Showing 2 of 7 actions');
 await page.getByRole('combobox',{name:'Action issue',exact:true}).selectOption('Size & fit');
 await expect(page.getByRole('heading',{name:'Clarify shoe width and sizing'})).toBeVisible();
 await expect(page.locator('.action-card')).toHaveCount(1);
 await page.getByRole('combobox',{name:'Action type',exact:true}).selectOption('inspection');
 await expect(page.getByRole('heading',{name:'No actions match these filters'})).toBeVisible();
 await expect(page.getByRole('heading',{name:'All caught up'})).toHaveCount(0);
 await page.getByRole('button',{name:'Reset filters',exact:true}).first().click();
 await expect(page.locator('.action-card')).toHaveCount(7);
 await expect(page.getByRole('combobox',{name:'Action product',exact:true})).toHaveValue('all');
 await page.getByRole('textbox',{name:'Search actions',exact:true}).fill('  AUDIO-01  ');
 await expect(page.locator('.action-card')).toHaveCount(1);
 await expect(page.getByRole('heading',{name:'Explain supported devices'})).toBeVisible();
 await expect(page.locator('.action-card')).toContainText('Wireless Headphones');
 await page.getByRole('textbox',{name:'Search actions',exact:true}).fill('DOES NOT EXIST');
 await expect(page.locator('.action-result-count')).toHaveText('Showing 0 of 7 actions');
 await expect(page.getByRole('button',{name:'Reset filters',exact:true})).toHaveCount(2);
 await page.getByRole('combobox',{name:'Data source',exact:true}).selectOption('shopify');
 await expect(page.locator('.action-card')).toHaveCount(2);
 await expect(page.getByRole('textbox',{name:'Search actions',exact:true})).toHaveValue('');
 await expect(page.locator('.action-result-count')).toHaveText('Showing 2 of 2 actions · Shopify');
});

test('product guidance stays reviewable and only verified buyer checks have a workspace preview',async({page})=>{
 await openActions(page);
 await page.getByRole('combobox',{name:'Action type',exact:true}).selectOption('buyer_guide:compatibility');
 const compatibility=page.locator('.action-card');
 await expect(compatibility).toContainText('Compatibility & setup');
 await expect(compatibility).toContainText('Wireless Headphones');
 await expect(compatibility.getByRole('link',{name:'Preview',exact:false})).toHaveCount(0);
 await compatibility.getByRole('button',{name:'Review action',exact:true}).click();
 await expect(page.getByRole('dialog')).toContainText('Explain supported devices');
 await expect(page.getByRole('dialog').getByRole('spinbutton')).toHaveCount(0);
 await expect(page.getByRole('dialog').getByRole('checkbox')).not.toBeChecked();
 await expect(page.getByRole('dialog').locator('.modal-actions .primary')).toBeDisabled();
 await page.getByRole('button',{name:'Close review',exact:true}).click();
 await page.getByRole('button',{name:'Reset filters',exact:true}).click();
 await page.getByRole('tab',{name:'Approved',exact:true}).click();
 await expect(page.locator('.action-card')).toHaveCount(5);
 await expect(page.locator('.action-card').getByRole('link',{name:'Preview',exact:false})).toHaveCount(2);
 await expect(page.locator('.action-card').filter({hasText:'Share a verified laptop fit check'}).getByRole('link',{name:'Preview',exact:false})).toHaveAttribute('href','?product=bag&workspace=workspace-actions');
 await expect(page.locator('.action-card').filter({hasText:'A verified shoe size guide'})).toContainText('Buyer guide ready to share');
 await expect(page.locator('.action-card').filter({hasText:'A verified shoe size guide'}).getByRole('link',{name:'Preview',exact:false})).toHaveAttribute('href','?product=shoes&workspace=workspace-actions&check=guide');
 await expect(page.locator('.action-card').filter({hasText:'Inspect the mug lid seal'})).toContainText('Approved for your team · apply the change in your store');
 await expect(page.locator('.action-card').filter({hasText:'Care instructions need verification'})).toContainText('Verify product guidance before sharing a buyer guide');
 await expect(page.locator('.action-card').filter({hasText:'An older fit decision needs measurements'})).toContainText('Verify measurements before sharing a fit check');
 await page.getByRole('combobox',{name:'Action type',exact:true}).selectOption('buyer_guide:footwear');
 await expect(page.locator('.action-card')).toHaveCount(1);
 await expect(page.getByRole('heading',{name:'A verified shoe size guide'})).toBeVisible();
 await page.screenshot({path:'artifacts/action-filters-desktop.png',fullPage:true,animations:'disabled'});
});

test('Action filters remain usable on mobile without horizontal overflow',async({page})=>{
 await page.setViewportSize({width:390,height:844});
 await openActions(page);
 await page.getByRole('combobox',{name:'Action product',exact:true}).selectOption('shirt');
 await page.getByRole('combobox',{name:'Action type',exact:true}).selectOption('buyer_guide:care');
 await expect(page.locator('.action-card')).toHaveCount(1);
 await expect(page.locator('.action-card')).toContainText('Use & care guide');
 await expect(page.getByRole('heading',{name:'Clarify washing and care'})).toBeVisible();
 expect(await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth)).toBe(false);
 await page.screenshot({path:'artifacts/action-filters-mobile.png',fullPage:true,animations:'disabled'});
 await page.getByRole('tab',{name:'Dismissed',exact:true}).click();
 await expect(page.getByRole('heading',{name:'No actions match these filters'})).toBeVisible();
 await page.getByRole('button',{name:'Reset filters',exact:true}).first().click();
 await expect(page.getByRole('heading',{name:'Reconsider the mug description'})).toBeVisible();
});
