import {test,expect} from '@playwright/test';
test.beforeEach(async({page})=>{await page.addInitScript(()=>sessionStorage.setItem('rr_demo','true'));});

const chatReceipt={id:'verified-test-call',provider:'Gemini',model:'gemini-3.5-flash-lite',purpose:'chat',at:new Date().toISOString(),response_id:'google-test-response',input_tokens:120,output_tokens:45,total_tokens:165};
const chatResponse={answer:'- The synthetic demo shows laptop-fit complaints.\n- Measure the compartment before approving a fit rule.',points:['The synthetic demo shows laptop-fit complaints.','Measure the compartment before approving a fit rule.'],evidence_ids:['demo-evidence'],follow_up_questions:['How do I approve a fit check?'],receipt:chatReceipt,evidence:[{id:'demo-evidence',product_name:'Everyday Backpack',reason:'Laptop sleeve is too narrow.',source:'synthetic'}],scope:{days:28,source:'synthetic',returns:160,source_counts:{synthetic:160}}};

test('Ask Radar shows functional evidence and follow-up context',async({page})=>{
 await page.setViewportSize({width:1440,height:1100});
 await page.route('**/api/ai-status',route=>route.fulfill({json:{configured:true,model:chatReceipt.model,last_call:chatReceipt,analysis:{mode:'local rules',at:chatReceipt.at,live_calls:0,local_products:10}}}));
 const questions:{question:string;history:{role:string;content:string}[];source:string}[]=[];
 await page.route('**/api/chat',route=>{questions.push(route.request().postDataJSON());return route.fulfill({json:chatResponse});});
 await page.goto('/');
 await page.getByRole('button',{name:'Ask Radar',exact:true}).click();
 await expect(page.getByText('Know what. Know why.')).toBeVisible();
 await page.screenshot({path:'artifacts/chat-desktop.png',fullPage:true,animations:'disabled'});
 await page.getByRole('combobox',{name:'Chat data source'}).selectOption('synthetic');
 await page.getByRole('textbox',{name:'Ask a question'}).fill('Why are laptops not fitting?');
 await page.getByRole('button',{name:'Send question'}).click();
 await expect(page.getByText(chatResponse.points[0],{exact:true}).last()).toBeVisible();
 await expect(page.locator('.chat-answer-points li')).toHaveCount(2);
 await expect(page.locator('.chat-answer-points li').first()).toHaveText(chatResponse.points[0]);
 await page.getByText('Supporting return feedback (1)').click();
 await expect(page.getByText('“Laptop sleeve is too narrow.”')).toBeVisible();
 await expect(page.getByText('google-test-response',{exact:true})).toHaveCount(0);
 await expect(page.getByText('gemini-3.5-flash-lite',{exact:true})).toHaveCount(0);
 await page.getByRole('button',{name:'How do I approve a fit check?',exact:true}).click();
 await expect(page.locator('.chat-message.assistant')).toHaveCount(2);
 expect(questions[0].source).toBe('synthetic');
 expect(questions[1].history).toEqual([{role:'user',content:'Why are laptops not fitting?'},{role:'assistant',content:chatResponse.answer}]);
 await page.getByRole('button',{name:'New chat'}).click();
 await expect(page.getByText('Know what. Know why.')).toBeVisible();
});

test('Ask Radar mobile handles an API error and retries without duplicate questions',async({page})=>{
 await page.setViewportSize({width:390,height:844});
 await page.route('**/api/ai-status',route=>route.fulfill({json:{configured:true,model:chatReceipt.model,last_call:null,analysis:null}}));
 let attempts=0;
 await page.route('**/api/chat',route=>++attempts===1?route.fulfill({status:503,json:{detail:'Gemini quota or rate limit reached. Try later.'}}):route.fulfill({json:chatResponse}));
 await page.goto('/');
 await page.getByRole('button',{name:'Open navigation'}).click();
 await page.getByRole('button',{name:'Ask Radar',exact:true}).click();
 await page.getByRole('textbox',{name:'Ask a question'}).fill('How do I check laptop fit?');
 await page.getByRole('button',{name:'Send question'}).click();
 await expect(page.getByRole('alert')).toContainText('temporarily limited');
 await expect(page.getByText('Live Gemini response',{exact:true})).toHaveCount(0);
 await page.getByRole('button',{name:'Retry question'}).click();
 await expect(page.getByText(chatResponse.points[0],{exact:true}).last()).toBeVisible();
 await expect(page.getByText('How do I check laptop fit?',{exact:true})).toHaveCount(1);
 expect(await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth)).toBe(false);
 await page.screenshot({path:'artifacts/chat-mobile.png',fullPage:true,animations:'disabled'});
});

test('investigate a pattern, approve measurements, and check buyer fit',async({page})=>{
 const errors:string[]=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.goto('/');
 await expect(page.getByRole('heading',{name:'A clearer picture of your returns.'})).toBeVisible();
 await expect(page.getByText('160',{exact:true}).first()).toBeVisible();
 await page.getByRole('button',{name:'Investigate Laptop fit for Everyday Backpack'}).click();
 await expect(page.getByRole('dialog')).toBeVisible();
 await expect(page.getByText("In the buyer's words")).toBeVisible();
 await page.getByRole('tab',{name:'Listing context'}).click();
 await expect(page.getByText(/customers may have seen an earlier product description/)).toBeVisible();
 await page.getByRole('button',{name:'Close investigation'}).click();
 await page.getByRole('button',{name:'Action center'}).click();
 await page.getByRole('button',{name:'Review action'}).first().click();
 const dialog=page.getByRole('dialog');
 await expect(dialog.getByRole('button',{name:'Approve & publish'})).toBeDisabled();
 await dialog.getByRole('checkbox').check();
 await dialog.getByRole('button',{name:'Approve & publish'}).click();
 await expect(dialog).not.toBeVisible();
 await page.getByRole('tab',{name:'Approved',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Help buyers check their laptop fit'})).toBeVisible();
 await page.goto('/?product=p1');
 await expect(page.getByText('Verified compartment: 26 × 37 × 3 cm')).toBeVisible();
 await page.getByRole('spinbutton',{name:'Width'}).fill('25');
 await page.getByRole('spinbutton',{name:'Height'}).fill('35');
 await page.getByRole('spinbutton',{name:'Depth'}).fill('2');
 await page.getByRole('button',{name:'Check my fit'}).click();
 await expect(page.getByRole('heading',{name:'Your measurements are within range'})).toBeVisible();
 await page.getByRole('spinbutton',{name:'Width'}).fill('30');
 await page.getByRole('button',{name:'Check my fit'}).click();
 await expect(page.getByRole('heading',{name:'This laptop is likely too large'})).toBeVisible();
 expect(errors).toEqual([]);
});

test('CSV import, analysis, filtering and mobile navigation',async({page})=>{
 await page.goto('/');
 await page.getByRole('button',{name:'Import CSV',exact:true}).click();
 await page.getByLabel('Choose CSV file').setInputFiles('public/sample-returns.csv');
 await page.getByRole('button',{name:'Import returns',exact:true}).click();
 await expect(page.getByRole('dialog')).not.toBeVisible();
 await page.getByRole('button',{name:'Run analysis',exact:true}).click();
 await expect(page.getByRole('button',{name:'Run analysis',exact:true})).toBeEnabled({timeout:15000});
 await page.getByRole('combobox',{name:'Data source'}).selectOption('csv');
 await expect(page.getByRole('button',{name:'Investigate Laptop fit for CSV Everyday Backpack'})).toBeVisible();
 await page.setViewportSize({width:390,height:844});
 await page.getByRole('button',{name:'Open navigation'}).click();
 await page.getByRole('button',{name:'Return inbox',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Return inbox',exact:true})).toBeVisible();
 await page.getByPlaceholder('Search products, feedback or order IDs…').fill('narrow');
 await expect(page.getByText('1 records',{exact:true})).toBeVisible();
 const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth);
 expect(overflow).toBe(false);
 await page.screenshot({path:'artifacts/mobile-inbox.png',fullPage:true,animations:'disabled'});
});

test('desktop overview and mobile buyer layouts render without overflow',async({page})=>{
 await page.setViewportSize({width:1440,height:1100});
 await page.goto('/');
 await expect(page.getByRole('heading',{name:'On your radar'})).toBeVisible();
 await page.screenshot({path:'artifacts/dashboard.png',fullPage:true,animations:'disabled'});
 await page.setViewportSize({width:390,height:844});
 await page.goto('/?product=p7');
 await expect(page.getByRole('button',{name:'Check my fit'})).toBeVisible();
 expect(await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth)).toBe(false);
 await page.screenshot({path:'artifacts/mobile-fit-check.png',fullPage:true,animations:'disabled'});
});

test('experimental WebMCP adapter uses the visible investigation state',async({page})=>{
 await page.addInitScript(()=>{
  const registry:Record<string,{execute:(input:unknown)=>unknown;inputSchema:unknown;annotations:unknown}>={};
  Object.defineProperty(window,'returnRadarTools',{value:registry});
  Object.defineProperty(document,'modelContext',{value:{registerTool(tool:{name:string;execute:(input:unknown)=>unknown;inputSchema:unknown;annotations:unknown},options:{signal:AbortSignal}){registry[tool.name]=tool;options.signal.addEventListener('abort',()=>delete registry[tool.name]);}}});
 });
 await page.goto('/');
 await expect(page.getByRole('heading',{name:'On your radar'})).toBeVisible();
 const summary=await page.evaluate(async()=>{
  const tools=(window as unknown as {returnRadarTools:Record<string,{execute:(input:unknown)=>Promise<{patterns:{id:string}[]}>}>}).returnRadarTools;
  return tools.read_return_summary.execute({});
 });
 await page.evaluate(async id=>{
  const tools=(window as unknown as {returnRadarTools:Record<string,{execute:(input:unknown)=>Promise<unknown>}>}).returnRadarTools;
  await tools.start_return_investigation.execute({pattern_id:id});
 },summary.patterns[0].id);
 await expect(page.getByRole('dialog')).toBeVisible();
 const invalid=await page.evaluate(async()=>{
  const tools=(window as unknown as {returnRadarTools:Record<string,{execute:(input:unknown)=>Promise<unknown>}>}).returnRadarTools;
  try{await tools.start_return_investigation.execute({pattern_id:'unknown'});return false;}catch{return true;}
 });
 expect(invalid).toBe(true);
 await expect(page.getByRole('dialog')).toBeVisible();
});
