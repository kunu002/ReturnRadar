/** Keep merchant-facing errors useful without exposing provider or infrastructure details. */
const connectionGuidance=new Map([
 ['Enter your store\'s myshopify.com address, not the app settings page.','Use your store’s myshopify.com address from Settings → Domains, not an app settings link.'],
 ['Enter your Shopify app Client ID and Client secret.','Enter your Shopify app Client ID and Client secret.'],
 ['Enter your store\'s myshopify.com address.','Enter your store’s myshopify.com address.'],
 ['Enter the store access token.','Enter the store access token.'],
 ['Enter the WooCommerce consumer key and consumer secret.','Enter the WooCommerce consumer key and consumer secret.'],
 ['Use your store\'s HTTPS address without a login, query, or port.','Use your store’s secure home address beginning with https://, without extra sign-in details or settings.'],
 ['Connect a public store with a secure HTTPS address.','Use your store’s public, secure address beginning with https://.'],
 ['The store redirected the request. Enter its final HTTPS store address.','Enter the store’s current secure address beginning with https://.'],
 ['The access token belongs to a different Shopify store.','These connection details belong to another Shopify store. Check the store address and access token.'],
].map(([message,guidance])=>[message.replace(/[’‘]/g,"'").toLowerCase(),guidance]));

export function functionalError(error:unknown,fallback='We couldn’t complete this request. Please try again.'):string{
 const message=typeof error==='string'?error:error instanceof Error?error.message:'';
 const status=typeof error==='object'&&error!==null&&'status' in error?Number(error.status):undefined;
 // Only known field guidance is allowed through. Never echo credential values or diagnostic details.
 const guidance=connectionGuidance.get(message.trim().replace(/[’‘]/g,"'").toLowerCase());
 if(guidance)return guidance;
 if(/quota|rate.?limit|resource.?exhausted|too many|six questions/i.test(message))return 'This request is temporarily limited. Please try again later.';
 if(/timeout|timed out|deadline|taking too long/i.test(message))return 'This is taking longer than usual. Please try again.';
 if(/failed to fetch|network|connection refused|load failed|unreachable/i.test(message))return 'We couldn’t reach your workspace. Refresh the page and try again.';
 if(/email or password is incorrect/i.test(message))return 'Email or password is incorrect.';
 if(status===401)return 'Please sign in again to continue.';
 if(/gemini|firebase|firestore|google.?genai|api|http|schema|token|traceback|exception|backend|server|cloud tasks|configured|configuration|\/api\/|127\.0\.0\.1|localhost|\.env|\bkey\b|stack trace|\bSQL\b/i.test(message))return fallback;
 if(status===403&&(!message||/^forbidden$/i.test(message)))return 'Your role does not allow this action. Ask your workspace owner for access.';
 if(status===404&&(!message||/^not found$/i.test(message)))return fallback;
 if(status===405||status!==undefined&&status>=500)return fallback;
 return message&&message.length<=300?message:fallback;
}
