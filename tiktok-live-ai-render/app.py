import asyncio
import base64
import os
import queue
import re
import threading
import time
import uuid
from collections import deque

import requests
from flask import Flask, jsonify, render_template_string, request
from TikTokLive import TikTokLiveClient
from TikTokLive.client.errors import UserOfflineError
from TikTokLive.events import (
    CommentEvent,
    ConnectEvent,
    DisconnectEvent,
    FollowEvent,
    GiftEvent,
    JoinEvent,
    LikeEvent,
    ShareEvent,
)

app = Flask(__name__)

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
VIBI_BASE = "https://api.vibi.pro"

STATE_LOCK = threading.RLock()
EVENTS = deque(maxlen=500)
EVENT_ID = 0
INCOMING = queue.Queue(maxsize=1000)

STATE = {
    "running": False,
    "tiktok": "OFFLINE",
    "room_id": None,
    "username": "",
    "openrouter_ok": False,
    "vibi_ok": False,
    "last_error": "",
    "client": None,
    "tiktok_thread": None,
    "responder_thread": None,
    "config": {},
    "last_join_reply": 0.0,
}

SYSTEM_PROMPT = """أنت رفيق بث سعودي اسمه سايبر.
تتكلم عامية سعودية طبيعية وقصيرة جدًا.
ردودك غالبًا من 3 إلى 12 كلمة.
رحب بالناس ورد على تعليقاتهم والهدايا والمتابعات بدون رسمية.
لا تخترع شيء عن اللعب أو الشخص.
إذا كان الكلام غير واضح قل باختصار إنك ما فهمت.
لا تذكر أنك مساعد ولا تشرح النظام إلا إذا سئلت مباشرة.
لا تستخدم سب أو تنمر أو معلومات حساسة.
اجعل الرد مناسبًا للنطق الصوتي مباشرة، بدون رموز أو Markdown."""


def push_event(kind, message, **extra):
    global EVENT_ID
    with STATE_LOCK:
        EVENT_ID += 1
        item = {
            "id": EVENT_ID,
            "ts": time.strftime("%H:%M:%S"),
            "kind": kind,
            "message": message,
            **extra,
        }
        EVENTS.append(item)
        return item


def clean_username(value: str) -> str:
    value = (value or "").strip()
    m = re.search(r"tiktok\.com/@([^/?#]+)", value, flags=re.I)
    if m:
        value = m.group(1)
    value = value.replace("@", "").strip().strip("/")
    if not re.fullmatch(r"[A-Za-z0-9._-]{2,64}", value):
        raise ValueError("رابط أو يوزر TikTok غير صالح")
    return value


def redact_error(exc):
    text = str(exc)
    text = re.sub(r"sk-or-[A-Za-z0-9_-]+", "[OPENROUTER_KEY]", text)
    text = re.sub(r"sk_[A-Za-z0-9_-]+", "[VIBI_KEY]", text)
    return text[:500]


def test_openrouter(key):
    if not key:
        return False, "مفتاح OpenRouter ناقص"
    r = requests.get(
        f"{OPENROUTER_BASE}/key",
        headers={"Authorization": f"Bearer {key}"},
        timeout=12,
    )
    if r.status_code != 200:
        return False, f"OpenRouter HTTP {r.status_code}"
    return True, "OpenRouter متصل"


def test_vibi(key):
    if not key:
        return False, "مفتاح Vibi ناقص"
    r = requests.get(
        f"{VIBI_BASE}/v1/auth/me",
        headers={"xi-api-key": key},
        timeout=12,
    )
    if r.status_code != 200:
        return False, f"Vibi HTTP {r.status_code}"
    data = r.json() if r.content else {}
    credits = data.get("credits") or data.get("balance") or data.get("remaining_credits")
    msg = "Vibi متصل"
    if credits is not None:
        msg += f" | الرصيد: {credits}"
    return True, msg


def tiktok_live_check(username):
    async def _check():
        client = TikTokLiveClient(unique_id=f"@{username}")
        return await client.is_live()
    return asyncio.run(_check())


def openrouter_reply(event):
    cfg = STATE["config"]
    key = cfg.get("openrouter_key", "")
    model = cfg.get("model", "google/gemini-2.5-flash")
    name = event.get("name", "مشاهد")
    etype = event.get("type", "comment")
    text = event.get("text", "")

    if etype == "comment":
        user_text = f'المشاهد "{name}" كتب في بث TikTok: {text}'
    elif etype == "gift":
        user_text = f'المشاهد "{name}" أرسل هدية: {text}'
    elif etype == "follow":
        user_text = f'المشاهد "{name}" تابع الحساب الآن.'
    elif etype == "share":
        user_text = f'المشاهد "{name}" شارك البث.'
    else:
        user_text = f'المشاهد "{name}" دخل البث. رحب به باختصار.'

    r = requests.post(
        f"{OPENROUTER_BASE}/chat/completions",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": os.getenv("RENDER_EXTERNAL_URL", "https://render.com"),
            "X-Title": "TikTok AI Live",
        },
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_text},
            ],
            "temperature": 0.9,
            "max_tokens": 90,
        },
        timeout=25,
    )
    if r.status_code != 200:
        raise RuntimeError(f"OpenRouter HTTP {r.status_code}")
    data = r.json()
    answer = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    answer = re.sub(r"\s+", " ", answer)
    if not answer:
        raise RuntimeError("OpenRouter رجع رد فارغ")
    return answer[:350]


def vibi_speak(text):
    cfg = STATE["config"]
    key = cfg.get("vibi_key", "")
    voice_id = cfg.get("voice_id", "").strip()
    model = cfg.get("vibi_model", "eleven_v3").strip() or "eleven_v3"
    if not voice_id:
        raise RuntimeError("Voice ID ناقص")

    headers = {"xi-api-key": key, "Content-Type": "application/json"}
    created = requests.post(
        f"{VIBI_BASE}/v1/text-to-speech/{voice_id}",
        headers=headers,
        json={
            "text": text,
            "model_id": model,
            "language_code": "ar",
            "provider": "elevenlabs",
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
                "speed": 1.0,
            },
        },
        timeout=25,
    )
    if created.status_code not in (200, 201, 202):
        raise RuntimeError(f"Vibi create HTTP {created.status_code}")
    task = created.json()
    task_id = task.get("id")
    if not task_id:
        raise RuntimeError("Vibi لم يرجع Task ID")

    deadline = time.time() + 35
    audio_url = None
    while time.time() < deadline:
        time.sleep(0.45)
        r = requests.get(
            f"{VIBI_BASE}/v1/history/{task_id}",
            headers={"xi-api-key": key},
            timeout=10,
        )
        if r.status_code != 200:
            continue
        row = r.json()
        if row.get("status") == "failed":
            raise RuntimeError("Vibi فشل في توليد الصوت")
        if row.get("status") == "completed":
            audio_url = (row.get("result") or {}).get("audio_url") or row.get("audio_url")
            if audio_url:
                break

    if not audio_url:
        raise RuntimeError("انتهت مهلة انتظار صوت Vibi")

    audio = requests.get(audio_url, headers={"xi-api-key": key}, timeout=20)
    if audio.status_code != 200:
        audio = requests.get(audio_url, timeout=20)
    if audio.status_code != 200 or not audio.content:
        raise RuntimeError(f"تحميل صوت Vibi HTTP {audio.status_code}")

    mime = audio.headers.get("content-type", "audio/mpeg")
    return base64.b64encode(audio.content).decode("ascii"), mime


def should_reply(event):
    etype = event.get("type")
    if etype in {"comment", "gift", "follow", "share"}:
        return True
    if etype == "join":
        now = time.time()
        with STATE_LOCK:
            if now - STATE["last_join_reply"] < 12:
                return False
            STATE["last_join_reply"] = now
        return True
    return False


def responder_loop():
    while True:
        with STATE_LOCK:
            running = STATE["running"]
        if not running:
            return
        try:
            event = INCOMING.get(timeout=1)
        except queue.Empty:
            continue
        try:
            if not should_reply(event):
                continue
            reply = openrouter_reply(event)
            audio_b64, mime = vibi_speak(reply)
            push_event("ai", reply, name=event.get("name", ""), audio_b64=audio_b64, mime=mime)
        except Exception as exc:
            msg = redact_error(exc)
            with STATE_LOCK:
                STATE["last_error"] = msg
            push_event("error", msg)


def start_tiktok_client(username):
    client = TikTokLiveClient(unique_id=f"@{username}")
    with STATE_LOCK:
        STATE["client"] = client

    def name_of(event):
        user = getattr(event, "user", None)
        return (
            getattr(user, "nickname", None)
            or getattr(user, "unique_id", None)
            or "مشاهد"
        )

    @client.on(ConnectEvent)
    async def on_connect(event):
        with STATE_LOCK:
            STATE["tiktok"] = "LIVE"
            STATE["room_id"] = client.room_id
        push_event("status", f"متصل ببث @{event.unique_id} | Room ID: {client.room_id}")

    @client.on(DisconnectEvent)
    async def on_disconnect(_):
        with STATE_LOCK:
            STATE["tiktok"] = "OFFLINE"
        push_event("status", "انقطع اتصال TikTok")

    @client.on(CommentEvent)
    async def on_comment(event):
        name = name_of(event)
        text = (getattr(event, "comment", "") or "").strip()
        push_event("comment", f"{name}: {text}", name=name)
        try:
            INCOMING.put_nowait({"type": "comment", "name": name, "text": text})
        except queue.Full:
            pass

    @client.on(JoinEvent)
    async def on_join(event):
        name = name_of(event)
        push_event("join", f"{name} دخل البث", name=name)
        try:
            INCOMING.put_nowait({"type": "join", "name": name, "text": ""})
        except queue.Full:
            pass

    @client.on(FollowEvent)
    async def on_follow(event):
        name = name_of(event)
        push_event("follow", f"{name} تابع الحساب", name=name)
        try:
            INCOMING.put_nowait({"type": "follow", "name": name, "text": ""})
        except queue.Full:
            pass

    @client.on(ShareEvent)
    async def on_share(event):
        name = name_of(event)
        push_event("share", f"{name} شارك البث", name=name)
        try:
            INCOMING.put_nowait({"type": "share", "name": name, "text": ""})
        except queue.Full:
            pass

    @client.on(GiftEvent)
    async def on_gift(event):
        gift = getattr(event, "gift", None)
        if getattr(gift, "streakable", False) and getattr(event, "streaking", False):
            return
        name = name_of(event)
        gift_name = getattr(gift, "name", "هدية")
        count = getattr(event, "repeat_count", 1) or 1
        text = f"{count}x {gift_name}"
        push_event("gift", f"{name} أرسل {text}", name=name)
        try:
            INCOMING.put_nowait({"type": "gift", "name": name, "text": text})
        except queue.Full:
            pass

    @client.on(LikeEvent)
    async def on_like(event):
        count = getattr(event, "count", 0) or 0
        if count:
            push_event("like", f"وصلت {count} لايك")

    try:
        client.run(process_connect_events=False)
    except UserOfflineError:
        with STATE_LOCK:
            STATE["tiktok"] = "OFFLINE"
            STATE["room_id"] = None
            STATE["running"] = False
        push_event("error", "الحساب مو باث LIVE الآن")
    except Exception as exc:
        msg = redact_error(exc)
        with STATE_LOCK:
            STATE["tiktok"] = "ERROR"
            STATE["last_error"] = msg
            STATE["running"] = False
        push_event("error", msg)


@app.get("/")
def index():
    return render_template_string(HTML)


@app.get("/health")
def health():
    return jsonify({"ok": True})


@app.get("/api/status")
def api_status():
    with STATE_LOCK:
        return jsonify({
            "running": STATE["running"],
            "tiktok": STATE["tiktok"],
            "room_id": STATE["room_id"],
            "username": STATE["username"],
            "openrouter_ok": STATE["openrouter_ok"],
            "vibi_ok": STATE["vibi_ok"],
            "last_error": STATE["last_error"],
        })


@app.get("/api/events")
def api_events():
    try:
        since = int(request.args.get("since", "0"))
    except ValueError:
        since = 0
    with STATE_LOCK:
        rows = [x for x in EVENTS if x["id"] > since]
    return jsonify({"events": rows[-100:]})


@app.post("/api/test")
def api_test():
    data = request.get_json(silent=True) or {}
    try:
        username = clean_username(data.get("tiktok", ""))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    openrouter_key = (data.get("openrouter_key") or "").strip()
    vibi_key = (data.get("vibi_key") or "").strip()

    result = {"ok": True, "username": username}

    try:
        ok, msg = test_openrouter(openrouter_key)
        result["openrouter"] = {"ok": ok, "message": msg}
    except Exception as exc:
        result["openrouter"] = {"ok": False, "message": redact_error(exc)}

    try:
        ok, msg = test_vibi(vibi_key)
        result["vibi"] = {"ok": ok, "message": msg}
    except Exception as exc:
        result["vibi"] = {"ok": False, "message": redact_error(exc)}

    try:
        live = tiktok_live_check(username)
        result["tiktok"] = {
            "ok": bool(live),
            "message": "الحساب باث LIVE" if live else "الحساب مو باث الآن",
        }
    except Exception as exc:
        result["tiktok"] = {"ok": False, "message": redact_error(exc)}

    result["ok"] = all(x.get("ok") for x in [result["openrouter"], result["vibi"], result["tiktok"]])
    return jsonify(result)


@app.post("/api/start")
def api_start():
    data = request.get_json(silent=True) or {}
    try:
        username = clean_username(data.get("tiktok", ""))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    cfg = {
        "openrouter_key": (data.get("openrouter_key") or "").strip(),
        "model": (data.get("model") or "google/gemini-2.5-flash").strip(),
        "vibi_key": (data.get("vibi_key") or "").strip(),
        "voice_id": (data.get("voice_id") or "").strip(),
        "vibi_model": (data.get("vibi_model") or "eleven_v3").strip(),
    }

    if not cfg["openrouter_key"] or not cfg["vibi_key"] or not cfg["voice_id"]:
        return jsonify({"ok": False, "error": "حط OpenRouter Key و Vibi Key و Voice ID"}), 400

    with STATE_LOCK:
        if STATE["running"]:
            return jsonify({"ok": False, "error": "الوكيل شغال أصلًا. وقفه أول."}), 409
        STATE["running"] = True
        STATE["username"] = username
        STATE["tiktok"] = "CONNECTING"
        STATE["room_id"] = None
        STATE["last_error"] = ""
        STATE["config"] = cfg
        STATE["last_join_reply"] = 0

    push_event("status", f"جاري الاتصال بـ @{username}...")

    responder = threading.Thread(target=responder_loop, daemon=True, name="responder")
    tiktok = threading.Thread(target=start_tiktok_client, args=(username,), daemon=True, name="tiktok")

    with STATE_LOCK:
        STATE["responder_thread"] = responder
        STATE["tiktok_thread"] = tiktok

    responder.start()
    tiktok.start()
    return jsonify({"ok": True, "message": "بدأ الاتصال"})


@app.post("/api/stop")
def api_stop():
    with STATE_LOCK:
        STATE["running"] = False
        client = STATE.get("client")
        STATE["tiktok"] = "STOPPING"

    if client is not None:
        try:
            loop = getattr(client, "_asyncio_loop", None)
            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(client.disconnect(), loop)
        except Exception:
            pass

    with STATE_LOCK:
        STATE["tiktok"] = "OFFLINE"
        STATE["room_id"] = None
        STATE["client"] = None
    push_event("status", "تم إيقاف الوكيل")
    return jsonify({"ok": True})


HTML = r"""<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TikTok AI LIVE</title>
<style>
:root{font-family:system-ui,-apple-system,Segoe UI,Tahoma,Arial,sans-serif;color:#f5f5f5;background:#0c0d10}
*{box-sizing:border-box}body{margin:0;background:#0c0d10}.wrap{max-width:850px;margin:auto;padding:24px}
h1{font-size:24px;margin:0 0 5px}.sub{color:#9ca3af;margin:0 0 20px}
.card{background:#15171c;border:1px solid #292c33;border-radius:16px;padding:18px;margin-bottom:14px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}@media(max-width:700px){.grid{grid-template-columns:1fr}}
label{display:block;font-size:13px;color:#b8bdc7;margin-bottom:6px}
input{width:100%;background:#0f1115;color:#fff;border:1px solid #343842;border-radius:10px;padding:12px;font-size:14px;outline:none}
input:focus{border-color:#6f7787}.actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:14px}
button{border:0;border-radius:10px;padding:11px 16px;font-weight:700;cursor:pointer;background:#fff;color:#111}
button.secondary{background:#2a2e36;color:#fff}button.danger{background:#5b2228;color:#fff}
.status{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}@media(max-width:700px){.status{grid-template-columns:1fr 1fr}}
.pill{background:#0f1115;border:1px solid #2b2f37;border-radius:12px;padding:10px}
.pill b{display:block;font-size:12px;color:#9097a3;margin-bottom:4px}.ok{color:#4ade80}.bad{color:#fb7185}.wait{color:#facc15}
#log{height:330px;overflow:auto;background:#0a0b0e;border-radius:12px;padding:12px;font-size:14px}
.row{padding:8px 6px;border-bottom:1px solid #1b1e24}.row small{color:#737b87;margin-left:8px}.ai{color:#86efac}.comment{color:#fff}.gift{color:#fde68a}.error{color:#fda4af}.statusline{color:#93c5fd}
.note{font-size:12px;color:#8b93a0;line-height:1.7;margin-top:8px}
</style>
</head>
<body>
<div class="wrap">
  <h1>TikTok AI LIVE</h1>
  <p class="sub">رابط البث → OpenRouter → Vibi → صوت في المتصفح</p>

  <div class="card">
    <div class="grid">
      <div><label>رابط TikTok LIVE أو @username</label><input id="tiktok" placeholder="https://www.tiktok.com/@username/live"></div>
      <div><label>OpenRouter Model</label><input id="model" value="google/gemini-2.5-flash"></div>
      <div><label>OpenRouter Key</label><input id="orkey" type="password" placeholder="sk-or-v1-..."></div>
      <div><label>Vibi Key</label><input id="vkey" type="password" placeholder="sk_..."></div>
      <div><label>Vibi Voice ID</label><input id="voice" placeholder="Voice ID"></div>
      <div><label>Vibi Model</label><input id="vmodel" value="eleven_v3"></div>
    </div>
    <div class="actions">
      <button class="secondary" onclick="testAll()">اختبار الاتصال</button>
      <button onclick="startAgent()">تشغيل الوكيل</button>
      <button class="danger" onclick="stopAgent()">إيقاف</button>
    </div>
    <div class="note">المفاتيح لا تُحفظ في ملفات؛ تبقى في ذاكرة السيرفر أثناء التشغيل فقط. افتح الصفحة وقت البث وخَل TikTok LIVE Studio يلتقط صوت المتصفح/النظام.</div>
  </div>

  <div class="card status">
    <div class="pill"><b>TikTok</b><span id="stTikTok" class="wait">OFFLINE</span></div>
    <div class="pill"><b>Room ID</b><span id="stRoom">—</span></div>
    <div class="pill"><b>OpenRouter</b><span id="stOR">—</span></div>
    <div class="pill"><b>Vibi</b><span id="stVibi">—</span></div>
  </div>

  <div class="card">
    <div id="log"></div>
  </div>
</div>
<script>
let lastId=0;
const $=id=>document.getElementById(id);
function payload(){
 return {
  tiktok:$("tiktok").value.trim(),
  model:$("model").value.trim(),
  openrouter_key:$("orkey").value.trim(),
  vibi_key:$("vkey").value.trim(),
  voice_id:$("voice").value.trim(),
  vibi_model:$("vmodel").value.trim()
 };
}
function addLog(msg,kind="status"){
 const d=document.createElement("div"); d.className="row "+(kind==="ai"?"ai":kind==="gift"?"gift":kind==="error"?"error":kind==="comment"?"comment":"statusline");
 d.innerHTML="<small>"+new Date().toLocaleTimeString("ar-SA")+"</small>"+esc(msg);
 $("log").appendChild(d); $("log").scrollTop=$("log").scrollHeight;
}
function esc(s){return String(s??"").replace(/[&<>"']/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[m]))}
async function post(url,body){
 const r=await fetch(url,{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify(body||{})});
 const j=await r.json().catch(()=>({}));
 if(!r.ok) throw new Error(j.error||("HTTP "+r.status));
 return j;
}
async function testAll(){
 addLog("جاري فحص TikTok وOpenRouter وVibi...");
 try{
  const j=await post("/api/test",payload());
  $("stOR").textContent=j.openrouter.message; $("stOR").className=j.openrouter.ok?"ok":"bad";
  $("stVibi").textContent=j.vibi.message; $("stVibi").className=j.vibi.ok?"ok":"bad";
  $("stTikTok").textContent=j.tiktok.message; $("stTikTok").className=j.tiktok.ok?"ok":"bad";
  addLog(j.ok?"كل الاتصالات ناجحة ✓":"فيه اتصال يحتاج تعديل",j.ok?"status":"error");
 }catch(e){addLog(e.message,"error")}
}
async function startAgent(){
 try{ const j=await post("/api/start",payload()); addLog(j.message||"بدأ التشغيل"); }catch(e){addLog(e.message,"error")}
}
async function stopAgent(){
 try{ await post("/api/stop",{}); addLog("تم الإيقاف"); }catch(e){addLog(e.message,"error")}
}
async function pollStatus(){
 try{
  const j=await fetch("/api/status").then(r=>r.json());
  $("stTikTok").textContent=j.tiktok||"—";
  $("stTikTok").className=j.tiktok==="LIVE"?"ok":j.tiktok==="ERROR"?"bad":"wait";
  $("stRoom").textContent=j.room_id||"—";
 }catch{}
}
async function pollEvents(){
 try{
  const j=await fetch("/api/events?since="+lastId).then(r=>r.json());
  for(const e of (j.events||[])){
   lastId=Math.max(lastId,e.id||0);
   addLog(e.message,e.kind);
   if(e.audio_b64){
    const a=new Audio("data:"+(e.mime||"audio/mpeg")+";base64,"+e.audio_b64);
    a.play().catch(()=>addLog("المتصفح منع التشغيل التلقائي؛ اضغط أي مكان ثم جرّب.","error"));
   }
  }
 }catch{}
}
setInterval(pollStatus,1500);
setInterval(pollEvents,700);
pollStatus(); pollEvents();
</script>
</body>
</html>"""

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, threaded=True)
