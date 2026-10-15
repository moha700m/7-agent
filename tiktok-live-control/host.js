/* Dashboard extensions. LIVE audio is intentionally absent from this file. */
const hostIds=["personality_mode","topic_profile","mood_override","persona_min_duration","idle_threshold","max_proactive_per_10min","memory_ttl_hours","proactive_enabled","crowd_enabled","allow_profile_persona","secondary_model","third_model","tts_provider","tts_fallback"];
let currentStatus={};
const statFields={mood:"Current Mood",crowd_state:"Crowd State",queue_size:"Queue Size",current_ai_model:"AI Model",current_tts_provider:"TTS Provider",latency:"Latency",comments_per_min:"Comments/min",gifts_per_min:"Gifts/min",replies_per_min:"Replies/min",suppressed_spam:"Suppressed spam",deduped_messages:"Deduped messages",reconnect_attempts:"Reconnect attempts",player_connected:"Player",paused:"Responses",last_error:"Last error"};
$("advancedStats").innerHTML=Object.entries(statFields).map(([key,label])=>'<div class="stat"><b>'+esc(label)+'</b><span id="host_'+key+'">—</span></div>').join("");
function hostPayload(){
  const out={};
  for(const id of hostIds){const e=$(id);out[id]=e.type==="checkbox"?e.checked:e.type==="number"?Number(e.value):e.value;}
  return out;
}
async function loadHostOptions(){
  try{
    const j=await api("profiles");
    $("topic_profile").innerHTML=j.profiles.map(p=>'<option value="'+esc(p.id)+'">'+esc(p.label)+'</option>').join("");
    $("mood_override").innerHTML='<option value="auto">Auto</option>'+j.moods.map(m=>'<option>'+esc(m)+'</option>').join("");
    let saved={};try{saved=JSON.parse(localStorage.getItem("cyber-live-settings-v4")||"{}");}catch{}
    setField("topic_profile",saved.topic_profile||"cod_snd");setField("mood_override",saved.mood_override||"auto");
  }catch(e){addLog("تعذر تحميل ملفات البث: "+e.message,"error");}
}
function renderHostStatus(j){
  currentStatus=j;
  const m=j.metrics||{};
  const merged={...m,...j,latency:(m.average_response_latency||0)+"s",player_connected:j.player_connected?"متصل":"بانتظار المشغل",paused:j.paused?"Paused":"Active"};
  for(const key of Object.keys(statFields))$("host_"+key).textContent=merged[key]??"—";
  $("muteButton").textContent=j.muted?"Unmute AI":"Mute AI";
  const metrics=$("sessionMetrics");metrics.replaceChildren();
  for(const [key,value] of Object.entries(m)){
    if(key.endsWith("_per_min"))continue;
    const row=document.createElement("div");row.className="stat";
    const title=document.createElement("b");title.textContent=key;
    const content=document.createElement("span");content.textContent=key==="session_start"?new Date(value*1000).toLocaleString("ar-SA"):String(value);
    row.append(title,content);metrics.append(row);
  }
}
function renderInspector(e){
  const c=e.classification||{};
  addLog([e.event_type?.toUpperCase(),"@"+(e.username||"viewer"),'"'+(e.text||"")+'"',"Class: "+(c.category||"—"),"Priority: "+e.priority,"Decision: "+e.decision,e.ignored_reason?"Reason: "+e.ignored_reason:"",e.ai_reply?"AI: "+e.ai_reply:"",e.latency!=null?"Latency: "+e.latency+"s":""].filter(Boolean).join(" | "),e.decision==="ERROR"?"error":e.decision==="REPLY"?"ai":"status");
}
async function applyHost(){saveSettings(true);try{await api("configure","POST",payload());addLog("تم تطبيق إعدادات المضيف");loadSecretStatus();pollStatus();}catch(e){addLog(e.message,"error");}}
async function forceCharacter(){setField("personality_mode","manual");await changeBroadcastMode();}
async function control(action,extra={}){try{await api("control","POST",{action,...extra});pollStatus();}catch(e){addLog(e.message,"error");}}
async function toggleMute(){await control("mute",{muted:!currentStatus.muted});}
async function restartAgent(){try{await api("restart","POST",payload());addLog("تمت إعادة التشغيل بأمان");pollStatus();}catch(e){addLog(e.message,"error");}}
async function resetMetrics(){try{await api("metrics/reset","POST",{});pollStatus();}catch(e){addLog(e.message,"error");}}
async function copyPlayerLink(){
  try{
    const j=await api("player_token");const url=new URL("player.html",location.href);url.hash="token="+encodeURIComponent(j.token);
    $("playerLink").textContent=url.href;
    if(navigator.clipboard){await navigator.clipboard.writeText(url.href);addLog("تم نسخ رابط المشغل، صالح لمدة 30 يومًا. احفظه لنفسك.");}
  }catch(e){addLog(e.message,"error");}
}
function readCustomPresets(){
  try{
    const list=JSON.parse(localStorage.getItem("cyber-custom-presets-v1")||"[]");
    if(!Array.isArray(list))return [];
    return list.slice(0,30).filter(p=>/^custom_[a-z0-9_]+$/.test(p.id)).map(p=>{
      const keep=(obj,ids)=>Object.fromEntries(ids.filter(k=>obj?.[k]!==undefined).map(k=>[k,obj[k]]));
      return {id:p.id,base_id:String(p.base_id||"cyber_gamer"),label:String(p.label||"Custom").slice(0,80),type:"Custom",voice:String(p.voice||"adam").slice(0,100),voice_label:String(p.voice||"adam"),voice_gender:String(p.voice_gender||""),persona:keep(p.persona,personaIds),behavior:keep(p.behavior,behaviorIds)};
    });
  }catch{return [];}
}
async function saveCustomPreset(){
  const data=payload();const id="custom_"+Date.now().toString(36);
  const custom={id,base_id:data.preset_id,label:data.persona.name+" — Custom",voice:data.tts_voice,voice_label:data.tts_voice,voice_gender:CHARACTER_PRESETS[val("broadcast_mode")]?.voice_gender||"",persona:data.persona,behavior:data.behavior};
  const list=readCustomPresets();list.push(custom);localStorage.setItem("cyber-custom-presets-v1",JSON.stringify(list.slice(-30)));
  CHARACTER_PRESETS[id]=custom;const option=new Option(custom.label,id);$("broadcast_mode").add(option);$("broadcast_mode").value=id;saveSettings(true);
  await applyHost();addLog("حُفظت نسخة محلية من الشخصية والصوت والسلوك.");
}
async function deleteCustomPreset(){
  const id=val("broadcast_mode");if(!id.startsWith("custom_")){addLog("اختر نسخة Custom محلية أولًا");return;}
  localStorage.setItem("cyber-custom-presets-v1",JSON.stringify(readCustomPresets().filter(p=>p.id!==id)));
  delete CHARACTER_PRESETS[id];$("broadcast_mode").querySelector('option[value="'+id+'"]').remove();$("broadcast_mode").value="cyber_gamer";await changeBroadcastMode();
}
async function startSimulation(){
  try{const j=await api("simulation/start","POST",{...payload(),fixture:val("fixture")});$("simResult").textContent=j.fixture?"المحاكاة تعمل دون مزودات؛ الصوت صامت للتأكد من مسار التشغيل.":"المحاكاة تعمل باستخدام AI وTTS الحقيقيين.";loadSecretStatus();pollStatus();}catch(e){$("simResult").textContent=e.message;}
}
async function simulate(type){
  try{
    const j=await api("simulate","POST",{type,username:val("sim_user"),text:val("sim_text"),gift_value:Number(val("sim_gift_value")),count:30});
    $("simResult").textContent="تم إدخال الحدث: "+(j.classification?.category||"burst")+" | Priority: "+(j.priority??"—")+" — راجع السجل المباشر";
  }catch(e){$("simResult").textContent=e.message;}
}
for(const type of ["comment","join","gift","follow","share","like","burst"]){const b=document.createElement("button");b.className="secondary";b.textContent="Fake "+type;b.onclick=()=>simulate(type);$("simulationButtons").append(b);}
async function pollHealth(){try{$("healthDetails").textContent=JSON.stringify(await api("health/details"),null,2);}catch(e){$("healthDetails").textContent=e.message;}}
pollHealth();setInterval(pollHealth,5000);
