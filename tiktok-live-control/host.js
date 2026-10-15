/* Dashboard extensions. LIVE audio is intentionally absent from this file. */
const hostIds=["personality_mode","topic_profile","mood_override","persona_min_duration","idle_threshold","max_proactive_per_10min","memory_ttl_hours","proactive_enabled","crowd_enabled","allow_profile_persona","secondary_model","third_model","tts_provider","tts_fallback",
"self_host_enabled","self_host_mode","self_host_activity","self_host_idle_before_start","self_host_min_gap","self_host_max_gap","self_host_max_consecutive","self_host_banter_level","self_host_question_frequency","self_host_topic_repeat_cooldown","self_host_topic_timeout","self_host_allow_stories","self_host_allow_debates","self_host_allow_banter","self_host_allow_challenges","self_host_allow_polls",
"race_commentator_enabled","race_commentator_explain","race_commentator_min_gap","race_commentator_hype"];
let currentStatus={},TOPIC_PROFILES={};
const statFields={mood:"Current Mood",crowd_state:"Crowd State",queue_size:"Queue Size",current_ai_model:"AI Model",current_tts_provider:"TTS Provider",latency:"Latency",comments_per_min:"Comments/min",gifts_per_min:"Gifts/min",replies_per_min:"Replies/min",suppressed_spam:"Suppressed spam",deduped_messages:"Deduped messages",reconnect_attempts:"Reconnect attempts",player_connected:"Player",paused:"Responses",last_error:"Last error"};
$("advancedStats").innerHTML=Object.entries(statFields).map(([key,label])=>'<div class="stat"><b>'+esc(label)+'</b><span id="host_'+key+'">—</span></div>').join("");
const hostPanel=document.getElementById("host");
if(hostPanel){
  const first=hostPanel.querySelector(".card");
  first.insertAdjacentHTML("afterend",`
  <div class="card" id="selfHostCard">
    <div class="title">SELF HOST MODE — سوالف تلقائية</div>
    <div class="grid3">
      <label class="toggle"><input id="self_host_enabled" type="checkbox" checked> تشغيل المضيف التلقائي</label>
      <div><label>Mode</label><select id="self_host_mode"><option value="auto">Auto</option><option value="manual">Manual</option></select></div>
      <div><label>Host Activity</label><select id="self_host_activity"><option value="low">Low</option><option value="normal" selected>Normal</option><option value="high">High</option><option value="chaos">Chaos</option></select></div>
      <div><label>Idle before talking</label><input id="self_host_idle_before_start" type="number" min="10" max="120" value="20"></div>
      <div><label>Min speaking gap</label><input id="self_host_min_gap" type="number" min="10" max="60" value="20"></div>
      <div><label>Max speaking gap</label><input id="self_host_max_gap" type="number" min="15" max="120" value="40"></div>
      <div><label>Max consecutive lines</label><input id="self_host_max_consecutive" type="number" min="1" max="5" value="2"></div>
      <div><label>Banter Level</label><input id="self_host_banter_level" type="number" min="0" max="10" value="6"></div>
      <div><label>Question Frequency</label><select id="self_host_question_frequency"><option value="low">Low</option><option value="medium">Medium</option><option value="high" selected>High</option></select></div>
      <div><label>Topic Repeat Cooldown (sec)</label><input id="self_host_topic_repeat_cooldown" type="number" min="300" max="3600" value="1200"></div>
      <div><label>Topic Timeout (sec)</label><input id="self_host_topic_timeout" type="number" min="30" max="300" value="90"></div>
      <label class="toggle"><input id="self_host_allow_stories" type="checkbox" checked> Stories</label>
      <label class="toggle"><input id="self_host_allow_debates" type="checkbox" checked> Gaming Debates</label>
      <label class="toggle"><input id="self_host_allow_banter" type="checkbox" checked> Banter</label>
      <label class="toggle"><input id="self_host_allow_challenges" type="checkbox" checked> Challenges</label>
      <label class="toggle"><input id="self_host_allow_polls" type="checkbox" checked> Quick Polls</label>
    </div>
    <div class="grid3" style="margin-top:12px">
      <div class="stat"><b>State</b><span id="sh_state">—</span></div>
      <div class="stat"><b>Current Topic</b><span id="sh_topic">—</span></div>
      <div class="stat"><b>Engagement</b><span id="sh_engagement">0</span></div>
      <div class="stat"><b>Participants</b><span id="sh_participants">0</span></div>
      <div class="stat"><b>Next Speak</b><span id="sh_next">—</span></div>
      <div class="stat"><b>Last Line</b><span id="sh_last">—</span></div>
    </div>
    <div class="actions">
      <button class="accent" onclick="applySelfHost()">تطبيق Self Host</button>
      <button class="secondary" onclick="selfHostAction('speak-now')">Speak Now</button>
      <button class="secondary" onclick="selfHostAction('new-topic')">New Topic</button>
      <button class="secondary" onclick="selfHostAction('stop-topic')">Stop Topic</button>
      <button class="secondary" onclick="selfHostAction('pause')">أنا أتكلم الآن</button>
      <button class="secondary" onclick="selfHostAction('resume')">رجع AI</button>
    </div>
    <p class="hint">أي تعليق أو متابعة أو هدية أو مشاركة حقيقية تأخذ الأولوية على السوالف التلقائية.</p>
  </div>`);

  const selfHostCard=document.getElementById("selfHostCard")||first;
  selfHostCard.insertAdjacentHTML("afterend",`
  <div class="card" id="raceCommentatorCard">
    <div class="title">🎙️ معلّق سباق الجمهور — صوت واحد</div>
    <div class="grid3">
      <label class="toggle"><input id="race_commentator_enabled" type="checkbox" checked> تشغيل المعلّق الذكي</label>
      <label class="toggle"><input id="race_commentator_explain" type="checkbox" checked> يشرح اللعبة والقوانين</label>
      <div><label>أقل فاصل بين التعليقات — ثانية</label><input id="race_commentator_min_gap" type="number" min="4" max="30" value="8"></div>
      <div><label>مستوى الحماس 0–10</label><input id="race_commentator_hype" type="number" min="0" max="10" value="7"></div>
      <div class="stat"><b>اتصال السباق</b><span id="rc_state">—</span></div>
      <div class="stat"><b>الجولة / الحالة</b><span id="rc_phase">—</span></div>
      <div class="stat"><b>المتسابقون</b><span id="rc_players">0</span></div>
      <div class="stat"><b>المتصدر</b><span id="rc_leader">—</span></div>
      <div class="stat"><b>التعليق القادم</b><span id="rc_next">—</span></div>
      <div class="stat"><b>آخر حدث علّق عليه</b><span id="rc_last">—</span></div>
    </div>
    <div class="actions">
      <button class="accent" onclick="applyRaceCommentator()">تفعيل المعلّق</button>
      <button class="secondary" onclick="raceCommentatorSpeakNow()">علّق الآن</button>
    </div>
    <p class="hint">يقرأ بيانات سباق الجمهور من السيرفر مباشرة. يستخدم نفس الشخصية والصوت المختار حاليًا، والتعليقات والهدايا الحقيقية لها الأولوية. عند تشغيله تتوقف السوالف التلقائية حتى يبقى عندك صوت واحد فقط.</p>
    <div id="rc_error" class="hint"></div>
  </div>`);
}
function hostPayload(){
  const out={};
  for(const id of hostIds){const e=$(id);out[id]=e.type==="checkbox"?e.checked:e.type==="number"?Number(e.value):e.value;}
  return out;
}
async function loadHostOptions(){
  try{
    const j=await api("profiles");
    TOPIC_PROFILES=Object.fromEntries(j.profiles.map(p=>[p.id,p]));
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
  if($("stVoice")&&j.tts_voice_label)$("stVoice").textContent=j.tts_voice_label+" · "+(j.voice_gender||"");

  if($("sh_state"))$("sh_state").textContent=j.self_host_state||"—";
  if($("sh_topic"))$("sh_topic").textContent=j.current_topic||"—";
  if($("sh_engagement"))$("sh_engagement").textContent=(j.topic_engagement??0)+"/100";
  if($("sh_participants"))$("sh_participants").textContent=j.topic_participants??0;
  if($("sh_next"))$("sh_next").textContent=(j.next_self_host_in??0)+"s";
  if($("sh_last"))$("sh_last").textContent=j.last_self_host_line||"—";

  if($("rc_state"))$("rc_state").textContent=j.race_commentator_connected?"متصل بالسباق":(j.race_commentator_enabled?"جاري الاتصال":"متوقف");
  if($("rc_phase"))$("rc_phase").textContent=(j.race_commentator_round&&j.race_commentator_round!=="—"?"جولة "+j.race_commentator_round+" · ":"")+(j.race_commentator_phase||"—");
  if($("rc_players"))$("rc_players").textContent=j.race_commentator_players??0;
  if($("rc_leader"))$("rc_leader").textContent=j.race_commentator_leader||"—";
  if($("rc_next"))$("rc_next").textContent=(j.race_commentator_next_in??0)+"s";
  if($("rc_last"))$("rc_last").textContent=j.race_commentator_last_event||"—";
  if($("rc_error"))$("rc_error").textContent=j.race_commentator_last_error?"آخر خطأ: "+j.race_commentator_last_error:"المصدر: "+(j.race_commentator_source||"سباق الجمهور");
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
function selfHostPayload(){
  const out={};for(const id of hostIds.filter(x=>x.startsWith("self_host_"))){const e=$(id);if(e)out[id]=e.type==="checkbox"?e.checked:e.type==="number"?Number(e.value):e.value;}return out;
}
async function applySelfHost(){
  saveSettings(true);
  try{await api("self-host/configure","POST",selfHostPayload());addLog("تم تطبيق وضع السوالف التلقائي");pollStatus();}
  catch(e){addLog(e.message,"error");}
}
async function selfHostAction(action){
  try{await api("self-host/"+action,"POST",{});addLog("Self Host: "+action);pollStatus();}
  catch(e){addLog(e.message,"error");}
}
function raceCommentatorPayload(){
  return {
    race_commentator_enabled:!!$("race_commentator_enabled")?.checked,
    race_commentator_explain:!!$("race_commentator_explain")?.checked,
    race_commentator_min_gap:Number($("race_commentator_min_gap")?.value||8),
    race_commentator_hype:Number($("race_commentator_hype")?.value||7)
  };
}
async function applyRaceCommentator(){
  saveSettings(true);
  const data=raceCommentatorPayload();
  if(data.race_commentator_enabled){
    setField("self_host_enabled",false);
    setField("proactive_enabled",false);
  }
  try{
    const j=await api("commentator/configure","POST",data);
    renderHostStatus(j);
    addLog(data.race_commentator_enabled?"تم تشغيل معلّق السباق بنفس الصوت الحالي":"تم إيقاف معلّق السباق");
    saveSettings(true);
  }catch(e){addLog("تعذر تطبيق المعلّق: "+e.message,"error");}
}
async function raceCommentatorSpeakNow(){
  try{await api("commentator/speak-now","POST",{});addLog("طلبت من المعلّق تعليقًا فوريًا");pollStatus();}
  catch(e){addLog(e.message,"error");}
}
async function forceCharacter(){setField("personality_mode","manual");await changeBroadcastMode();}
async function control(action,extra={}){try{await api("control","POST",{action,...extra});pollStatus();}catch(e){addLog(e.message,"error");}}
async function toggleMute(){await control("mute",{muted:!currentStatus.muted});}
async function restartAgent(){try{await api("restart","POST",payload());addLog("تمت إعادة التشغيل بأمان");pollStatus();}catch(e){addLog(e.message,"error");}}
async function resetMetrics(){try{await api("metrics/reset","POST",{});pollStatus();}catch(e){addLog(e.message,"error");}}
async function copyPlayerLink(){
  try{
    const j=await api("player_token");const url=new URL("player.html?v=20261015-8",location.href);url.hash="token="+encodeURIComponent(j.token);
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
for(const [type,label] of [["idle","Simulate Idle 30s"],["topic_response","Topic Response"],["no_response","No Response"],["owner_talking","Owner Talking"]]){
  const b=document.createElement("button");b.className="secondary";b.textContent=label;b.onclick=async()=>{try{const body={type,username:val("sim_user"),text:val("sim_text"),seconds:30,value:true};const j=await api("simulate","POST",body);$("simResult").textContent="Simulation: "+label+" ✓";pollStatus();}catch(e){$("simResult").textContent=e.message;}};$("simulationButtons").append(b);
}
async function pollHealth(){try{$("healthDetails").textContent=JSON.stringify(await api("health/details"),null,2);}catch(e){$("healthDetails").textContent=e.message;}}
pollHealth();setInterval(pollHealth,5000);

$("topic_profile").addEventListener("change",()=>{
  const profile=TOPIC_PROFILES[val("topic_profile")];if(!profile)return;
  if(val("allow_profile_persona")&&val("personality_mode")==="manual"&&CHARACTER_PRESETS[profile.recommended]){
    setField("broadcast_mode",profile.recommended);applyPresetFields(CHARACTER_PRESETS[profile.recommended]);
  }
  setField("game_or_topic",profile.label);setField("stream_context",profile.context);saveSettings(true);
});
