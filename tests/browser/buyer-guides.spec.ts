import {test,expect} from '@playwright/test';

test('a bag with both approved checks keeps the calculator and opens its buyer guide explicitly',async({page})=>{
 const product={id:'bag',name:'Laptop Backpack',description:'A laptop bag.',image:null,price:2499,variant:'Olive',demo:true,category:'Bags',fit_check_supported:true,fit_available:true,compartment:{width:26,height:37,depth:3},question:'Compare your laptop dimensions.',purchase_check:{kind:'buyer_guide',guide_type:'carry',title:'Check your carrying setup',instructions:'Review the carrying features.',checklist:['Review the published carrying features.','Check the included accessories.']}};
 await page.route('**/api/public/products/bag?*',route=>route.fulfill({json:product}));
 await page.goto('/?product=bag&workspace=merchant-example');
 await expect(page.getByRole('spinbutton',{name:/^Width/})).toBeVisible();
 await page.goto('/?product=bag&workspace=merchant-example&check=guide');
 await expect(page.getByRole('heading',{name:'Check your carrying setup',exact:true})).toBeVisible();
 await expect(page.getByRole('spinbutton',{name:/^Width/})).toHaveCount(0);
 await expect(page.getByRole('checkbox')).toHaveCount(2);
});

test('shoe guidance lets buyers review approved product checks without a laptop calculator',async({page})=>{
 const checkItems=['Compare your foot length with the published size chart.','Check the width guidance for the size you selected.'];
 await page.setViewportSize({width:390,height:844});
 await page.route('**/api/public/products/buyer-shoe**',route=>route.fulfill({json:{
  id:'buyer-shoe',name:'Everyday Running Shoes',category:'Footwear',description:'Lightweight running shoes with a verified size chart.',image:null,price:2999,variant:'White · UK 8',demo:true,fit_available:false,fit_check_supported:false,compartment:null,question:null,
  purchase_check:{kind:'buyer_guide',guide_type:'footwear',title:'Choose your shoe size with clear details',instructions:'Use the merchant’s size chart to review the size and width you need.',checklist:checkItems},
 }}));
 await page.goto('/?product=buyer-shoe&workspace=demo');
 await expect(page.getByRole('heading',{name:'Everyday Running Shoes'})).toBeVisible();
 await expect(page.getByText('Shoe fit guide',{exact:true})).toBeVisible();
 await expect(page.getByRole('spinbutton')).toHaveCount(0);
 await expect(page.getByText('Will your laptop fit in this bag?',{exact:true})).toHaveCount(0);
 await expect(page.getByRole('checkbox')).toHaveCount(2);
 await page.getByRole('checkbox',{name:checkItems[0],exact:true}).check();
 await page.getByRole('button',{name:'Review my checks',exact:true}).click();
 await expect(page.getByRole('status')).toContainText('Some checks still need attention');
 await page.getByRole('checkbox',{name:checkItems[1],exact:true}).check();
 await expect(page.getByRole('status')).toHaveCount(0);
 await page.getByRole('button',{name:'Review my checks',exact:true}).click();
 await expect(page.getByRole('status')).toContainText('Checks reviewed');
 await expect(page.getByText('This checklist helps you review product details. It does not guarantee suitability or fit.')).toBeVisible();
 expect(await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth)).toBe(false);
 await page.screenshot({path:'artifacts/mobile-buyer-guide.png',fullPage:true,animations:'disabled'});
});

test('a product without approved guidance shows what is still needed',async({page})=>{
 await page.route('**/api/public/products/buyer-unknown**',route=>route.fulfill({json:{id:'buyer-unknown',name:'Everyday Product',description:'Details from the store.',image:null,price:null,variant:'Standard',demo:false,fit_available:false,fit_check_supported:false,compartment:null,question:null,purchase_check:null}}));
 await page.goto('/?product=buyer-unknown&workspace=demo');
 await expect(page.getByRole('heading',{name:'Buyer guidance is being prepared'})).toBeVisible();
 await expect(page.getByRole('button',{name:'Review my checks',exact:true})).toHaveCount(0);
 await expect(page.getByRole('spinbutton')).toHaveCount(0);
 await expect(page.getByText('The merchant has not published a verified buyer guide for this product yet.')).toBeVisible();
});
