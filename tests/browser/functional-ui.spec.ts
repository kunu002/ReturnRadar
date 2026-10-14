import {test,expect} from '@playwright/test';

test('Ask Radar explains feedback without displaying provider diagnostics',async({page})=>{
 await page.addInitScript(()=>sessionStorage.setItem('rr_demo','true'));
 const receipt={id:'internal-call-private',provider:'Gemini',model:'gemini-private-model',purpose:'chat',at:new Date().toISOString(),response_id:'internal-response-private',input_tokens:100,output_tokens:20,total_tokens:120};
 await page.route('**/api/ai-status',route=>route.fulfill({json:{configured:true,model:receipt.model,last_call:receipt,analysis:{mode:'Gemini',at:receipt.at,records:160,live_calls:10}}}));
 let attempts=0;
 await page.route('**/api/chat',route=>++attempts===1?route.fulfill({status:503,json:{detail:'Gemini API schema validation failed. Check GEMINI_API_KEY in .env.'}}):route.fulfill({json:{answer:'Review the size guide before changing the listing.',points:['Review the size guide before changing the listing.'],evidence_ids:['internal-return-private'],follow_up_questions:[],receipt,evidence:[{id:'internal-return-private',product_name:'Trail Running Shoes',reason:'The shoes are too narrow.',source:'synthetic'}],scope:{days:28,source:'synthetic',returns:160,source_counts:{synthetic:160}}}}));
 await page.goto('/');
 await page.getByRole('button',{name:'Ask Radar',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Your return insights'})).toBeVisible();
 await expect(page.locator('.chat-side').getByText('Latest analysis complete')).toBeVisible();
 await page.getByRole('textbox',{name:'Ask a question'}).fill('How can I reduce shoe returns?');
 await page.getByRole('button',{name:'Send question'}).click();
 await expect(page.getByRole('alert')).toContainText('Radar couldn’t answer that question right now.');
 await expect(page.getByRole('alert')).not.toContainText(/Gemini|API|schema|\.env|key/i);
 await page.getByRole('button',{name:'Retry question'}).click();
 await expect(page.locator('.chat-answer-points li')).toHaveText(['Review the size guide before changing the listing.']);
 await page.getByText('Supporting return feedback (1)').click();
 await expect(page.getByText('“The shoes are too narrow.”')).toBeVisible();
 await expect(page.locator('.ask-radar-layout')).not.toContainText(/Gemini|internal-call-private|internal-response-private|internal-return-private|Token usage|API-call|gemini-private-model/);
 await expect(page.getByText('Includes sample records for demonstration')).toBeVisible();
 await page.setViewportSize({width:390,height:844});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth)).toBe(false);
});

test('Sign-in preserves account feedback and hides infrastructure errors',async({page})=>{
 let attempts=0;
 await page.route('**/api/auth/login',route=>++attempts===1?route.fulfill({status:401,json:{detail:'Email or password is incorrect.'}}):route.fulfill({status:503,json:{detail:'Firebase auth server configuration is unavailable. Set API key.'}}));
 await page.goto('/');
 await page.getByLabel('Email address',{exact:true}).fill('reviewer@example.com');
 await page.getByLabel('Password',{exact:true}).fill('RadarUI82!');
 await page.getByRole('button',{name:'Sign in',exact:true}).last().click();
 await expect(page.getByRole('alert')).toHaveText('Email or password is incorrect.');
 await page.getByRole('button',{name:'Sign in',exact:true}).last().click();
 await expect(page.getByRole('alert')).toHaveText('Sign-in is temporarily unavailable. Please try again shortly.');
 await expect(page.getByRole('alert')).not.toContainText(/Firebase|server|configuration|API|key/i);
});

test('Store setup preserves field guidance without revealing credentials or diagnostics',async({page})=>{
 await page.goto('/');
 await page.getByRole('button',{name:'Create account',exact:true}).click();
 await page.getByRole('radio',{name:/Start my workspace/}).check();
 await page.getByLabel('Your name',{exact:true}).fill('Connection Reviewer');
 await page.getByLabel('Merchant workspace name').fill('Field Guidance Store');
 await page.getByLabel('Email address',{exact:true}).fill(`connector-fields-${Date.now()}@example.com`);
 await page.getByLabel('Password',{exact:true}).fill('RadarUI82!');
 await page.getByRole('button',{name:'Create workspace',exact:true}).click();
 await page.getByRole('button',{name:'Data Connections',exact:true}).click();
 await page.getByRole('button',{name:'Connect Shopify',exact:true}).click();
 const dialog=page.getByRole('dialog');
 await dialog.getByLabel('Store address',{exact:true}).fill('https://example.com');
 await dialog.getByLabel('Store access token',{exact:true}).fill(`shpat_${'a'.repeat(32)}`);
 await dialog.getByRole('button',{name:'Check connection',exact:true}).click();
 await expect(dialog.getByRole('alert')).toHaveText('Enter your store’s myshopify.com address.');
 await dialog.getByRole('button',{name:'Save connection',exact:true}).click();
 await expect(dialog.getByRole('alert')).toHaveText('Enter your store’s myshopify.com address.');
 await dialog.getByLabel('Store address',{exact:true}).fill('https://review-store.myshopify.com');
 await dialog.getByLabel('Store access token',{exact:true}).fill(' ');
 await dialog.getByRole('button',{name:'Check connection',exact:true}).click();
 await expect(dialog.getByRole('alert')).toHaveText('Enter the store access token.');
 await page.route('**/api/connections/test',route=>route.fulfill({status:422,json:{detail:'Shopify token shpat_do_not_render API validation exception.'}}));
 await dialog.getByLabel('Store access token',{exact:true}).fill(`shpat_${'a'.repeat(32)}`);
 await dialog.getByRole('button',{name:'Check connection',exact:true}).click();
 await expect(dialog.getByRole('alert')).toContainText('Check the store address, connection details, and read access.');
 await expect(dialog.getByRole('alert')).not.toContainText(/shpat_|API|exception/i);
});
