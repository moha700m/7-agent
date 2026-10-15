// Local integration test: use a proxy/backend configured with CONTROL_TOKEN=unit-control.
// Install playwright as a development tool before running. No production credentials.
const {chromium}=require('playwright');const crypto=require('crypto');
(async()=>{
const browser=await chromium.launch({executablePath:'/usr/bin/chromium',headless:true,args:['--no-sandbox','--autoplay-policy=no-user-gesture-required']});
const context=await browser.newContext();
const iv=crypto.randomBytes(12),cipher=crypto.createCipheriv('aes-256-gcm',crypto.createHash('sha256').update('unit-control').digest(),iv);
const enc=Buffer.concat([cipher.update(JSON.stringify({openrouter_key:'fixture-key',validated_at:Date.now()})),cipher.final()]);
const cookie=Buffer.concat([iv,cipher.getAuthTag(),enc]).toString('base64url');
await context.addCookies([{name:'cyber_secrets',value:cookie,url:'http://localhost:8080',httpOnly:true,sameSite:'Lax'}]);
const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error'&&!m.text().includes('404'))errors.push(m.text())});
await page.goto('http://localhost:8080');await page.waitForFunction(()=>document.querySelector('#broadcast_mode').options.length===21);
await page.click('[data-tab="simulation"]');await page.check('#fixture');await page.click('button:has-text("بدء المحاكاة")');await page.waitForFunction(()=>document.querySelector('#simResult').textContent.includes('تعمل دون'));
const token=await page.evaluate(async()=>{return (await api('player_token')).token});
const player=await context.newPage();player.on('pageerror',e=>errors.push(e.message));await player.goto('http://localhost:8080/player.html?controls=1#token='+encodeURIComponent(token));
await player.waitForFunction(()=>active===true);
await page.click('button:has-text("Fake comment")');await page.waitForFunction(()=>document.querySelector('#log').textContent.includes('Decision: REPLY'));
await player.waitForFunction(()=>seen.size===1);await player.waitForFunction(()=>busy===false);
const audioState=await player.evaluate(()=>({seen:seen.size,current:current?.id||null,queue:queue.length}));
await page.click('[data-tab="connect"]');await page.selectOption('#broadcast_mode','nora_live');await page.waitForFunction(()=>document.querySelector('#stVoice').textContent.includes('jessica'));
await page.click('[data-tab="host"]');await page.selectOption('#mood_override','calm');await page.click('button:has-text("تطبيق الإعدادات")');await page.waitForFunction(()=>document.querySelector('#host_mood').textContent==='calm');
await page.click('[data-tab="persona"]');await page.fill('#name','نسخة اختبار');await page.click('button:has-text("Save Custom Preset")');await page.waitForFunction(()=>document.querySelector('#broadcast_mode').value.startsWith('custom_'));
const presets=await page.evaluate(()=>JSON.parse(localStorage.getItem('cyber-custom-presets-v1')));if(JSON.stringify(presets).includes('fixture-key'))throw Error('secret in preset');
await page.click('[data-tab="simulation"]');await page.click('button:has-text("Fake burst")');await page.waitForTimeout(300);
await page.click('[data-tab="live"]');await page.screenshot({path:'/tmp/live-host-inspector.png',fullPage:true});
const inspector=await page.locator('#log').innerText();
await page.click('[data-tab="connect"]');await page.click('button:has-text("إيقاف")');
await page.setViewportSize({width:390,height:844});await page.screenshot({path:'/tmp/live-host-mobile.png',fullPage:true});
if(errors.length)throw Error(JSON.stringify(errors));
console.log(JSON.stringify({pass:true,consoleErrors:errors,audioState,presets:presets.length,inspectorHasDedupe:inspector.includes('semantic duplicate')||inspector.includes('shared recent intent')}));
await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
