const {test}=require('node:test');
const assert=require('node:assert/strict');
const crypto=require('crypto');
const handler=require('../api/backend.js');
process.env.RENDER_API_BASE='https://backend.example';process.env.CONTROL_TOKEN='unit-control';
function cookie(){const iv=crypto.randomBytes(12),cipher=crypto.createCipheriv('aes-256-gcm',crypto.createHash('sha256').update('unit-control').digest(),iv);const enc=Buffer.concat([cipher.update(JSON.stringify({openrouter_key:'fixture-key',validated_at:Date.now()})),cipher.final()]);return 'cyber_secrets='+Buffer.concat([iv,cipher.getAuthTag(),enc]).toString('base64url');}
async function call(path,method='GET',body={},headers={}){const req={query:{path},method,body,headers:{host:'panel.example',...headers}},res={headers:{},statusCode:200};res.setHeader=(k,v)=>res.headers[k]=v;res.status=c=>{res.statusCode=c;return res};res.json=x=>{res.body=x;return res};res.send=x=>{res.body=x;return res};await handler(req,res);return res;}
test('proxy preserves security contracts and scopes player capability',async()=>{
 let calls=[];global.fetch=async(url,init)=>{calls.push({url:String(url),init});return {ok:true,status:200,headers:new Headers({'content-type':'application/json'}),text:async()=>'{"ok":true}'};};
 assert.equal((await call('arbitrary')).statusCode,400);
 assert.equal((await call('start','GET')).statusCode,405);
 assert.equal((await call('stop','POST')).statusCode,401);
 assert.equal((await call('stop','POST',{}, {origin:'https://evil.example',cookie:cookie()})).statusCode,403);
 const token=(await call('player_token','GET',{}, {cookie:cookie()})).body.token;
 assert.ok(token&&!token.includes('unit-control')&&!token.includes('fixture-key'));
 const heartbeat=await call('player/heartbeat','POST',{player_id:'x'},{'x-player-token':token});assert.equal(heartbeat.statusCode,200);
 assert.equal(calls.at(-1).init.headers['X-Control-Token'],'unit-control');
 assert.equal((await call('stop','POST',{}, {'x-player-token':token})).statusCode,401);
 const status=await call('secret_status','GET',{}, {cookie:cookie()});assert.equal(status.body.openrouter_saved,true);assert.ok(!JSON.stringify(status.body).includes('fixture-key'));
 const configured=await call('configure','POST',{}, {cookie:cookie()});assert.equal(configured.statusCode,200);assert.equal(JSON.parse(calls.at(-1).init.body).openrouter_key,'fixture-key');
});
