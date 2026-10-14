import asyncio
import base64
import hmac
import itertools
import os
import queue
import re
import threading
import time
from collections import deque

import requests
from flask import Flask, jsonify, request
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
EVENTS = deque(maxlen=600)
EVENT_ID = 0
INCOMING = queue.PriorityQueue(maxsize=1000)
SEQ = itertools.count()

STATE = {
    "running": False,
    "tiktok": "OFFLINE",
    "room_id": None,
    "username": "",
    "last_error": "",
    "client": None,
    "tiktok_thread": None,
    "responder_thread": None,
    "config": {},
    "last_join_reply": 0.0,
    "last_spoken_at": 0.0,
    "like_bucket": 0,
}


def default_persona():
    return {
        "name": "سايبر",
        "identity": "مضيف ذكاء اصطناعي سعودي للبث يساعد صاحب البث ويتفاعل مع الجمهور.",
        "dialect": "سعودي طبيعي بلمسة نجدية",
        "tone": "رايق، واثق، خفيف دم، سريع",
        "energy": 7,
        "humor": 6,
        "sarcasm": 3,
        "max_words": 14,
        "stream_context": "بث ألعاب وتفاعل مباشر مع الجمهور.",
        "owner_name": "",
        "game_or_topic": "ألعاب",
        "special_instructions": "لا تطول، لا تكرر نفس الترحيب، وخلك طبيعي.",
        "avoid_topics": "السب القاسي، التنمر، المعلومات الشخصية، الادعاءات غير المؤكدة",
        "comment_instruction": "جاوب التعليق نفسه باختصار، وإذا كان سؤال جاوبه مباشرة.",
        "join_instruction": "رحب ترحيب قصير جدًا وباسم الشخص إذا كان مناسب.",
        "gift_instruction": "اشكر المرسل واذكر الهدية بدون مبالغة.",
        "follow_instruction": "اشكر المتابعة بجملة قصيرة.",
        "share_instruction": "اشكر المشاركة بجملة قصيرة.",
        "like_instruction": "اشكر الجمهور بشكل جماعي على دفعة اللايكات.",
    }


def default_behavior():
    return {
        "respond_comments": True,
        "respond_gifts": True,
        "respond_follows": True,
        "respond_shares": True,
        "respond_joins": True,
        "respond_likes": False,
        "join_cooldown": 20,
        "global_cooldown": 3.0,
        "like_threshold": 100,
        "temperature": 0.9,
    }


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
    match = re.search(r"tiktok\.com/@([^/?#]+)", value, flags=re.I)
    if match:
        value = match.group(1)
    value = value.replace("@", "").strip().strip("/")
    if not re.fullmatch(r"[A-Za-z0-9._-]{2,64}", value):
        raise ValueError("رابط أو يوزر TikTok غير صالح")
    return value


def redact_error(exc):
    text = str(exc)
    text = re.sub(r"sk-or-[A-Za-z0-9_-]+", "[OPENROUTER_KEY]", text)
    text = re.sub(r"sk_[A-Za-z0-9_-]+", "[VIBI_KEY]", text)
    return text[:700]


def clamp_num(value, low, high, fallback):
    try:
        return max(low, min(high, float(value)))
    except Exception:
        return fallback


def build_config(data):
    persona = default_persona()
    persona.update({k: v for k, v in (data.get("persona") or {}).items() if v is not None})
    behavior = default_behavior()
    behavior.update({k: v for k, v in (data.get("behavior") or {}).items() if v is not None})

    persona["energy"] = int(clamp_num(persona.get("energy"), 0, 10, 7))
    persona["humor"] = int(clamp_num(persona.get("humor"), 0, 10, 6))
    persona["sarcasm"] = int(clamp_num(persona.get("sarcasm"), 0, 10, 3))
    persona["max_words"] = int(clamp_num(persona.get("max_words"), 4, 40, 14))

    behavior["join_cooldown"] = int(clamp_num(behavior.get("join_cooldown"), 0, 300, 20))
    behavior["global_cooldown"] = clamp_num(behavior.get("global_cooldown"), 0, 30, 3.0)
    behavior["like_threshold"] = int(clamp_num(behavior.get("like_threshold"), 10, 5000, 100))
    behavior["temperature"] = clamp_num(behavior.get("temperature"), 0, 2, 0.9)

    return {
        "openrouter_key": (data.get("openrouter_key") or "").strip(),
        "model": (data.get("model") or "google/gemini-2.5-flash").strip(),
        "vibi_key": (data.get("vibi_key") or "").strip(),
        "voice_id": (data.get("voice_id") or "").strip(),
        "vibi_model": (data.get("vibi_model") or "eleven_v3").strip(),
        "persona": persona,
        "behavior": behavior,
    }


def build_system_prompt(cfg):
    p = cfg.get("persona") or default_persona()
    max_words = p.get("max_words", 14)
    return f"""أنت {p.get("name", "سايبر")}.
هويتك: {p.get("identity", "")}
أنت مضيف ذكاء اصطناعي في TikTok LIVE. لا تدّعي أنك إنسان، وإذا سئلت عن هويتك قل بوضوح إنك AI.
اللهجة: {p.get("dialect", "سعودي طبيعي")}.
النبرة: {p.get("tone", "رايق وسريع")}.
الطاقة من 10: {p.get("energy", 7)}.
الفكاهة من 10: {p.get("humor", 6)}.
السخرية الخفيفة من 10: {p.get("sarcasm", 3)}.
اسم صاحب البث إن وُجد: {p.get("owner_name", "")}.
موضوع/لعبة البث: {p.get("game_or_topic", "")}.
سياق البث: {p.get("stream_context", "")}.
تعليمات خاصة: {p.get("special_instructions", "")}.
مواضيع تجنبها: {p.get("avoid_topics", "")}.

قواعد الرد:
- استخدم اللغة المحلية والطبيعية، وتفاعل مباشرة مع المشاهد.
- الرد صوتي؛ لا تستخدم Markdown أو رموز مزعجة أو شرح تقني.
- لا تخترع أحداثًا داخل اللعبة ولا معلومات عن المشاهد.
- لا تكرر نفس العبارة بشكل آلي.
- غالبًا لا تتجاوز {max_words} كلمة.
- إذا كان التعليق غير واضح، اطلب توضيحًا بجملة قصيرة.
- لا تكثر طلبات المتابعة أو الهدايا ولا تضغط على المشاهدين.
"""


def event_instruction(cfg, etype):
    p = cfg.get("persona") or default_persona()
    mapping = {
        "comment": p.get("comment_instruction"),
        "join": p.get("join_instruction"),
        "gift": p.get("gift_instruction"),
        "follow": p.get("follow_instruction"),
        "share": p.get("share_instruction"),
        "like": p.get("like_instruction"),
    }
    return mapping.get(etype) or "تفاعل باختصار وبشكل طبيعي."


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


def openrouter_reply(event, cfg=None):
    cfg = cfg or STATE["config"]
    key = cfg.get("openrouter_key", "")
    model = cfg.get("model", "google/gemini-2.5-flash")
    behavior = cfg.get("behavior") or default_behavior()
    name = event.get("name", "مشاهد")
    etype = event.get("type", "comment")
    text = event.get("text", "")

    if etype == "comment":
        user_text = f'المشاهد "{name}" كتب: {text}'
    elif etype == "gift":
        user_text = f'المشاهد "{name}" أرسل هدية: {text}'
    elif etype == "follow":
        user_text = f'المشاهد "{name}" تابع الحساب الآن.'
    elif etype == "share":
        user_text = f'المشاهد "{name}" شارك البث.'
    elif etype == "like":
        user_text = f'الجمهور أرسل دفعة لايكات: {text}.'
    else:
        user_text = f'المشاهد "{name}" دخل البث.'

    user_text += "\nطريقة التعامل مع هذا الحدث: " + event_instruction(cfg, etype)

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
                {"role": "system", "content": build_system_prompt(cfg)},
                {"role": "user", "content": user_text},
            ],
            "temperature": behavior.get("temperature", 0.9),
            "max_tokens": 120,
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
    return answer[:450]


def vibi_speak(text, cfg=None):
    cfg = cfg or STATE["config"]
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


def event_priority(etype):
    return {
        "gift": 0,
        "comment": 1,
        "follow": 2,
        "share": 2,
        "join": 3,
        "like": 4,
    }.get(etype, 5)


def enqueue_event(event):
    try:
        INCOMING.put_nowait((event_priority(event.get("type")), next(SEQ), event))
    except queue.Full:
        push_event("status", "تم تجاهل حدث بسبب امتلاء طابور الردود")


def should_reply(event):
    cfg = STATE["config"]
    behavior = cfg.get("behavior") or default_behavior()
    etype = event.get("type")

    switches = {
        "comment": "respond_comments",
        "gift": "respond_gifts",
        "follow": "respond_follows",
        "share": "respond_shares",
        "join": "respond_joins",
        "like": "respond_likes",
    }
    key = switches.get(etype)
    if key and not bool(behavior.get(key, True)):
        return False

    if etype == "join":
        now = time.time()
        cooldown = behavior.get("join_cooldown", 20)
        with STATE_LOCK:
            if now - STATE["last_join_reply"] < cooldown:
                return False
            STATE["last_join_reply"] = now
    return True


def responder_loop():
    while True:
        with STATE_LOCK:
            running = STATE["running"]
        if not running:
            return
        try:
            _, _, event = INCOMING.get(timeout=1)
        except queue.Empty:
            continue

        try:
            if not should_reply(event):
                continue

            with STATE_LOCK:
                cfg = dict(STATE["config"])
                behavior = cfg.get("behavior") or default_behavior()
                wait_for = behavior.get("global_cooldown", 3.0) - (time.time() - STATE["last_spoken_at"])
            if wait_for > 0:
                time.sleep(wait_for)

            reply = openrouter_reply(event, cfg)
            audio_b64, mime = vibi_speak(reply, cfg)
            with STATE_LOCK:
                STATE["last_spoken_at"] = time.time()
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
        return getattr(user, "nickname", None) or getattr(user, "unique_id", None) or "مشاهد"

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
        if text:
            enqueue_event({"type": "comment", "name": name, "text": text})

    @client.on(JoinEvent)
    async def on_join(event):
        name = name_of(event)
        push_event("join", f"{name} دخل البث", name=name)
        enqueue_event({"type": "join", "name": name, "text": ""})

    @client.on(FollowEvent)
    async def on_follow(event):
        name = name_of(event)
        push_event("follow", f"{name} تابع الحساب", name=name)
        enqueue_event({"type": "follow", "name": name, "text": ""})

    @client.on(ShareEvent)
    async def on_share(event):
        name = name_of(event)
        push_event("share", f"{name} شارك البث", name=name)
        enqueue_event({"type": "share", "name": name, "text": ""})

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
        enqueue_event({"type": "gift", "name": name, "text": text})

    @client.on(LikeEvent)
    async def on_like(event):
        count = int(getattr(event, "count", 0) or 0)
        if not count:
            return
        with STATE_LOCK:
            STATE["like_bucket"] += count
            behavior = (STATE["config"].get("behavior") or default_behavior())
            threshold = int(behavior.get("like_threshold", 100))
            speak_likes = bool(behavior.get("respond_likes", False))
            bucket = STATE["like_bucket"]
            if speak_likes and bucket >= threshold:
                STATE["like_bucket"] = 0
            else:
                bucket = 0
        if bucket:
            push_event("like", f"دفعة لايكات: {bucket}")
            enqueue_event({"type": "like", "name": "الجمهور", "text": str(bucket)})

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


@app.before_request
def protect_control_api():
    if not request.path.startswith("/api/"):
        return None
    expected = os.getenv("CONTROL_TOKEN", "")
    if not expected:
        return jsonify({"ok": False, "error": "CONTROL_TOKEN غير مضبوط على السيرفر"}), 503
    received = request.headers.get("X-Control-Token", "")
    if not hmac.compare_digest(received, expected):
        return jsonify({"ok": False, "error": "غير مصرح"}), 401
    return None


@app.get("/")
def index():
    return jsonify({
        "ok": True,
        "name": "TikTok AI LIVE backend",
        "control": "Use the Vercel control panel",
    })


@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "tiktok-ai-live-cyber"})


@app.get("/api/status")
def api_status():
    with STATE_LOCK:
        p = (STATE["config"].get("persona") or {})
        return jsonify({
            "running": STATE["running"],
            "tiktok": STATE["tiktok"],
            "room_id": STATE["room_id"],
            "username": STATE["username"],
            "agent_name": p.get("name", "سايبر"),
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

    cfg = build_config(data)
    result = {"ok": True, "username": username}

    try:
        ok, msg = test_openrouter(cfg["openrouter_key"])
        result["openrouter"] = {"ok": ok, "message": msg}
    except Exception as exc:
        result["openrouter"] = {"ok": False, "message": redact_error(exc)}

    try:
        ok, msg = test_vibi(cfg["vibi_key"])
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


@app.post("/api/preview")
def api_preview():
    data = request.get_json(silent=True) or {}
    cfg = build_config(data)
    if not cfg["openrouter_key"] or not cfg["vibi_key"] or not cfg["voice_id"]:
        return jsonify({"ok": False, "error": "حط OpenRouter Key وVibi Key وVoice ID"}), 400
    event_type = (data.get("preview_type") or "comment").strip()
    preview_text = (data.get("preview_text") or "السلام عليكم").strip()
    event = {"type": event_type, "name": "مشاهد تجريبي", "text": preview_text}
    try:
        reply = openrouter_reply(event, cfg)
        audio_b64, mime = vibi_speak(reply, cfg)
        return jsonify({"ok": True, "reply": reply, "audio_b64": audio_b64, "mime": mime})
    except Exception as exc:
        return jsonify({"ok": False, "error": redact_error(exc)}), 502


@app.post("/api/start")
def api_start():
    data = request.get_json(silent=True) or {}
    try:
        username = clean_username(data.get("tiktok", ""))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    cfg = build_config(data)
    if not cfg["openrouter_key"] or not cfg["vibi_key"] or not cfg["voice_id"]:
        return jsonify({"ok": False, "error": "حط OpenRouter Key وVibi Key وVoice ID"}), 400

    with STATE_LOCK:
        if STATE["running"]:
            return jsonify({"ok": False, "error": "الوكيل شغال أصلًا. وقفه أول."}), 409
        STATE["running"] = True
        STATE["username"] = username
        STATE["tiktok"] = "CONNECTING"
        STATE["room_id"] = None
        STATE["last_error"] = ""
        STATE["config"] = cfg
        STATE["last_join_reply"] = 0.0
        STATE["last_spoken_at"] = 0.0
        STATE["like_bucket"] = 0

    while True:
        try:
            INCOMING.get_nowait()
        except queue.Empty:
            break

    push_event("status", f"جاري الاتصال بـ @{username}...")

    responder = threading.Thread(target=responder_loop, daemon=True, name="responder")
    tiktok = threading.Thread(target=start_tiktok_client, args=(username,), daemon=True, name="tiktok")

    with STATE_LOCK:
        STATE["responder_thread"] = responder
        STATE["tiktok_thread"] = tiktok

    responder.start()
    tiktok.start()
    return jsonify({
        "ok": True,
        "message": "بدأ الاتصال",
        "agent_name": cfg["persona"].get("name", "سايبر"),
    })


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


if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, threaded=True)
