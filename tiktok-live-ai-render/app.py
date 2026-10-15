import os
import math
import time
import random
import asyncio
import threading
from flask import Flask, jsonify, request, Response
from TikTokLive import TikTokLiveClient
from TikTokLive.events import ConnectEvent, DisconnectEvent, CommentEvent, JoinEvent, GiftEvent, FollowEvent, LikeEvent

app = Flask(__name__)

TIKTOK_USERNAME = os.getenv("TIKTOK_USERNAME", "mgz7o2").lstrip("@")
DEMO_TOKEN = os.getenv("DEMO_TOKEN", "")
MAX_PLAYERS = int(os.getenv("MAX_PLAYERS", "24"))
WAIT_SECONDS = float(os.getenv("WAIT_SECONDS", "16"))
COUNTDOWN_SECONDS = float(os.getenv("COUNTDOWN_SECONDS", "5"))
RACE_SECONDS = float(os.getenv("RACE_SECONDS", "22"))
RESULT_SECONDS = float(os.getenv("RESULT_SECONDS", "9"))
COLORS = ["#ff5d73","#4dabf7","#ffd43b","#69db7c","#b197fc","#ffa94d","#f783ac","#63e6be","#74c0fc","#e599f7","#ff8787","#8ce99a"]

class RaceGame:
    def __init__(self):
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.phase = "waiting"
        self.phase_started = time.time()
        self.round = 1
        self.players = {}
        self.waiting_next = {}
        self.scores = {}
        self.winners = []
        self.live = "CONNECTING"
        self.room_id = None
        self.last_error = ""
        self.reconnects = 0
        self.events = 0
        self.client = None
        self.client_loop = None
        threading.Thread(target=self._game_loop, daemon=True).start()
        threading.Thread(target=self._tiktok_loop, daemon=True).start()

    def _player(self, username, name):
        rng = random.Random(f"{username}:{self.round}")
        return dict(
            username=username,
            name=(str(name or username or "لاعب").strip()[:24] or "لاعب"),
            progress=0.0,
            pace=rng.uniform(.93, 1.07),
            boost=0.0,
            color=COLORS[len(self.players) % len(COLORS)],
            joined_at=time.time(),
            finished_at=None,
            rank=None,
        )

    def join(self, username, name):
        username = str(username or "").strip()[:80]
        if not username:
            return
        with self.lock:
            self.events += 1
            if username in self.players:
                return
            if username in self.waiting_next:
                return
            if self.phase in ("waiting", "countdown") and len(self.players) < MAX_PLAYERS:
                if not self.players:
                    self.phase_started = time.time()
                self.players[username] = self._player(username, name)
            else:
                self.waiting_next[username] = (str(name or username).strip()[:24] or username)

    def boost(self, username, amount):
        with self.lock:
            p = self.players.get(str(username))
            if p:
                p["boost"] = min(.30, p["boost"] + max(0.0, float(amount)))

    def _countdown(self):
        if len(self.players) >= 2:
            self.phase = "countdown"
            self.phase_started = time.time()

    def _begin(self):
        self.phase = "racing"
        self.phase_started = time.time()
        self.winners = []
        for i, p in enumerate(self.players.values()):
            p["progress"] = 0.0
            p["finished_at"] = None
            p["rank"] = None
            p["color"] = COLORS[i % len(COLORS)]

    def _finish(self):
        ranked = sorted(
            self.players.values(),
            key=lambda p: (p["finished_at"] is None, p["finished_at"] or 10**20, -p["progress"])
        )
        for i, p in enumerate(ranked, 1):
            p["rank"] = i
        self.winners = []
        for p in ranked[:3]:
            self.winners.append({"rank": p["rank"], "name": p["name"], "username": p["username"]})
            row = self.scores.setdefault(p["username"], {"name": p["name"], "points": 0, "wins": 0})
            row["name"] = p["name"]
            row["points"] += 4 - p["rank"]
            if p["rank"] == 1:
                row["wins"] += 1
        self.phase = "finished"
        self.phase_started = time.time()

    def _next_round(self):
        queued = list(self.waiting_next.items())
        self.players = {}
        self.waiting_next = {}
        self.winners = []
        self.round += 1
        self.phase = "waiting"
        self.phase_started = time.time()
        for username, name in queued[:MAX_PLAYERS]:
            self.players[username] = self._player(username, name)
        if self.players:
            self.phase_started = time.time()

    def _tick(self, dt):
        now = time.time()
        if self.phase == "waiting":
            if len(self.players) >= 2 and now - self.phase_started >= WAIT_SECONDS:
                self._countdown()
            return
        if self.phase == "countdown":
            if now - self.phase_started >= COUNTDOWN_SECONDS:
                self._begin()
            return
        if self.phase == "racing":
            for p in self.players.values():
                if p["finished_at"] is not None:
                    continue
                p["boost"] = max(0.0, p["boost"] - dt * .06)
                jitter = random.uniform(.985, 1.015)
                p["progress"] = min(1.0, p["progress"] + (dt / RACE_SECONDS) * p["pace"] * jitter * (1 + p["boost"]))
                if p["progress"] >= 1:
                    p["finished_at"] = now
            if all(p["finished_at"] is not None for p in self.players.values()) or now - self.phase_started >= RACE_SECONDS * 1.35:
                self._finish()
            return
        if self.phase == "finished" and now - self.phase_started >= RESULT_SECONDS:
            self._next_round()

    def _game_loop(self):
        last = time.time()
        while not self.stop.wait(.1):
            now = time.time()
            dt = min(.25, now - last)
            last = now
            with self.lock:
                self._tick(dt)

    def state(self):
        with self.lock:
            now = time.time()
            players = sorted(self.players.values(), key=lambda p: (p["progress"], p["boost"]), reverse=True)
            if self.phase == "waiting":
                remaining = max(0, math.ceil(WAIT_SECONDS - (now - self.phase_started))) if len(players) >= 2 else None
            elif self.phase == "countdown":
                remaining = max(0, math.ceil(COUNTDOWN_SECONDS - (now - self.phase_started)))
            elif self.phase == "racing":
                remaining = max(0, math.ceil(RACE_SECONDS * 1.35 - (now - self.phase_started)))
            else:
                remaining = max(0, math.ceil(RESULT_SECONDS - (now - self.phase_started)))
            leaders = sorted(self.scores.values(), key=lambda x: (x["points"], x["wins"]), reverse=True)[:5]
            return dict(
                ok=True, live=self.live, username=TIKTOK_USERNAME, room_id=self.room_id,
                phase=self.phase, round=self.round, remaining=remaining,
                players=players, player_count=len(players), waiting_next=len(self.waiting_next),
                winners=self.winners, leaderboard=leaders, max_players=MAX_PLAYERS,
                last_error=self.last_error, reconnects=self.reconnects, events=self.events
            )

    def demo(self, count=8):
        with self.lock:
            for i in range(max(2, min(MAX_PLAYERS, int(count)))):
                self.join(f"demo_{self.round}_{i}", f"لاعب {i+1}")
            self._countdown()

    def _tiktok_loop(self):
        attempt = 0
        while not self.stop.is_set():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            client = None
            try:
                with self.lock:
                    self.live = "CONNECTING"
                client = TikTokLiveClient(unique_id=f"@{TIKTOK_USERNAME}")
                self.client, self.client_loop = client, loop

                def who(event):
                    user = getattr(event, "user", None)
                    username = str(getattr(user, "unique_id", "") or getattr(user, "id", "") or "")
                    name = str(getattr(user, "nickname", "") or username)
                    return username, name

                @client.on(ConnectEvent)
                async def on_connect(_):
                    nonlocal attempt
                    attempt = 0
                    with self.lock:
                        self.live = "LIVE"
                        self.room_id = client.room_id
                        self.last_error = ""
                    print(f"RACE_TIKTOK_CONNECTED room={client.room_id}", flush=True)

                @client.on(DisconnectEvent)
                async def on_disconnect(_):
                    with self.lock:
                        self.live = "RECONNECTING"

                @client.on(JoinEvent)
                async def on_join(event):
                    username, name = who(event)
                    self.join(username, name)

                @client.on(CommentEvent)
                async def on_comment(event):
                    username, name = who(event)
                    text = str(getattr(event, "comment", "") or "").strip().lower()
                    if text in {"دخول","ادخل","العب","لعب","join","play"}:
                        self.join(username, name)

                @client.on(FollowEvent)
                async def on_follow(event):
                    username, name = who(event)
                    self.join(username, name)
                    self.boost(username, .10)

                @client.on(LikeEvent)
                async def on_like(event):
                    username, name = who(event)
                    self.join(username, name)
                    count = max(1, int(getattr(event, "count", 1) or 1))
                    self.boost(username, min(.05, count * .002))

                @client.on(GiftEvent)
                async def on_gift(event):
                    gift = getattr(event, "gift", None)
                    if getattr(gift, "streakable", False) and getattr(event, "streaking", False):
                        return
                    username, name = who(event)
                    self.join(username, name)
                    count = max(1, int(getattr(event, "repeat_count", 1) or 1))
                    diamonds = max(0, int(getattr(gift, "diamond_count", 0) or 0)) * count
                    self.boost(username, min(.22, .04 + math.log10(max(1, diamonds)) * .05))

                async def connect():
                    task = await client.start(process_connect_events=False)
                    await task

                loop.run_until_complete(connect())
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                with self.lock:
                    self.live = "RECONNECTING"
                    self.last_error = "تعذر اتصال TikTok؛ إعادة المحاولة تلقائيًا"
                    self.reconnects += 1
                print(f"RACE_TIKTOK_RECONNECT {type(exc).__name__}", flush=True)
            finally:
                if client:
                    try:
                        loop.run_until_complete(asyncio.wait_for(client.disconnect(close_client=True), 3))
                    except Exception:
                        pass
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    try:
                        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                    except Exception:
                        pass
                loop.close()
                self.client = self.client_loop = None
                self.room_id = None
            if self.stop.is_set():
                break
            attempt += 1
            self.stop.wait(min(30, (2, 5, 10, 20, 30)[min(attempt-1, 4)]))

GAME = RaceGame()

HTML = r"""<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>سباق الجمهور</title>
<style>
*{box-sizing:border-box}html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#070a10;color:#fff;font-family:Tahoma,Arial,sans-serif}
body{background:radial-gradient(circle at 50% 0,#18243b 0,#0b1019 42%,#05070b 100%)}
.wrap{height:100%;padding:28px;display:grid;grid-template-rows:auto 1fr auto;gap:18px}
.top{display:flex;align-items:center;justify-content:space-between;gap:18px}
.title{font-size:44px;font-weight:900;letter-spacing:-1px}.sub{opacity:.68;font-size:19px;margin-top:5px}
.badges{display:flex;gap:10px;align-items:center}.badge{padding:10px 16px;border-radius:999px;background:#131b28;border:1px solid #263248;font-weight:700}
.live{background:#38141b;border-color:#70303c}.live.ok{background:#123322;border-color:#285f42}
.main{display:grid;grid-template-columns:minmax(0,1fr) 310px;gap:18px;min-height:0}
.track,.side{background:rgba(13,18,28,.9);border:1px solid #222d40;border-radius:22px;box-shadow:0 20px 50px #0008}
.track{padding:18px;overflow:hidden;position:relative}.side{padding:18px}
.message{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;text-align:center;pointer-events:none;z-index:2}
.message .box{padding:26px 38px;border-radius:24px;background:#080c14dc;border:1px solid #27334a;backdrop-filter:blur(7px)}
.big{font-size:68px;font-weight:1000}.small{font-size:22px;opacity:.75;margin-top:8px}
.lanes{height:100%;display:flex;flex-direction:column;gap:8px;justify-content:center}
.lane{height:56px;position:relative;border-radius:14px;background:linear-gradient(90deg,#111927,#0c121c);border:1px solid #1f2a3d;overflow:hidden}
.line{position:absolute;top:0;bottom:0;left:92%;width:5px;background:repeating-linear-gradient(0deg,#fff 0 8px,#222 8px 16px);opacity:.7}
.car{position:absolute;left:8px;top:5px;height:44px;display:flex;align-items:center;gap:9px;transition:left .48s linear;max-width:60%}
.icon{font-size:32px;filter:drop-shadow(0 3px 5px #000)}.name{font-size:16px;font-weight:900;white-space:nowrap;text-shadow:0 2px 4px #000}
.boost{position:absolute;inset:0;border-radius:14px;opacity:0;transition:.2s;box-shadow:inset 0 0 25px #fff3}.boost.on{opacity:1}
.side h3{margin:0 0 14px;font-size:22px}.lb{display:grid;gap:10px}.lbrow{padding:12px;border-radius:13px;background:#101827;display:grid;grid-template-columns:34px 1fr auto;gap:8px;align-items:center}.rank{font-weight:1000;font-size:20px}.pts{font-weight:900;color:#ffd43b}
.help{margin-top:20px;padding:15px;border-radius:14px;background:#0b121d;color:#b7c2d7;line-height:1.8;font-size:15px}
.bottom{display:flex;align-items:center;justify-content:space-between;gap:16px;font-size:17px;color:#b5bfd1}.bottom strong{color:white}
.podium{display:flex;gap:12px;align-items:flex-end;justify-content:center;margin-top:18px}.pod{min-width:150px;padding:18px 12px;border-radius:18px 18px 10px 10px;background:#121b2a}.pod.one{height:145px;border:2px solid #ffd43b}.pod.two{height:118px}.pod.three{height:98px}
@media(max-width:900px){.main{grid-template-columns:1fr}.side{display:none}.title{font-size:32px}.wrap{padding:14px}.lane{height:50px}}
</style>
</head>
<body>
<div class="wrap">
  <div class="top">
    <div><div class="title">🏁 سباق الجمهور</div><div class="sub">ادخل البث… تدخل السباق تلقائيًا</div></div>
    <div class="badges"><div id="live" class="badge live">TikTok</div><div id="round" class="badge">الجولة 1</div><div id="count" class="badge">0 لاعب</div></div>
  </div>
  <div class="main">
    <div class="track"><div id="message" class="message"></div><div id="lanes" class="lanes"></div></div>
    <div class="side">
      <h3>🏆 الترتيب</h3><div id="leaderboard" class="lb"></div>
      <div class="help"><b>طريقة اللعب</b><br>الدخول للبث = تدخل السباق<br>❤️ لايك = دفعة خفيفة<br>➕ متابعة = بوست<br>🎁 هدية = بوست أقوى<br><br>السباق يبدأ تلقائيًا.</div>
    </div>
  </div>
  <div class="bottom"><span id="status">جاري الاتصال…</span><span>القادمين للجولة التالية: <strong id="next">0</strong></span></div>
</div>
<script>
const lanes=document.getElementById('lanes'),msg=document.getElementById('message'),live=document.getElementById('live');
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function phaseMessage(s){
  if(s.phase==='waiting'){
    if(s.player_count<2)return '<div class="box"><div class="big">بانتظار لاعبين</div><div class="small">أي شخص يدخل البث ينضاف تلقائيًا</div></div>';
    return '<div class="box"><div class="big">جاهزين؟</div><div class="small">السباق يبدأ بعد '+s.remaining+' ث</div></div>';
  }
  if(s.phase==='countdown')return '<div class="box"><div class="big">'+s.remaining+'</div><div class="small">استعدوا 🚦</div></div>';
  if(s.phase==='finished'){
    let w=s.winners||[];
    return '<div class="box"><div class="big">🏆 النتائج</div><div class="podium">'+
      (w[1]?'<div class="pod two">🥈<br><b>'+esc(w[1].name)+'</b></div>':'')+
      (w[0]?'<div class="pod one">🥇<br><b>'+esc(w[0].name)+'</b></div>':'')+
      (w[2]?'<div class="pod three">🥉<br><b>'+esc(w[2].name)+'</b></div>':'')+
      '</div></div>';
  }
  return '';
}
function draw(s){
  live.textContent=s.live==='LIVE'?'🔴 LIVE':'⚪ '+s.live;
  live.classList.toggle('ok',s.live==='LIVE');
  document.getElementById('round').textContent='الجولة '+s.round;
  document.getElementById('count').textContent=s.player_count+' لاعب';
  document.getElementById('next').textContent=s.waiting_next||0;
  document.getElementById('status').textContent=s.last_error||('الحالة: '+({waiting:'تجميع لاعبين',countdown:'استعداد',racing:'السباق شغال',finished:'النتائج'}[s.phase]||s.phase));
  msg.innerHTML=phaseMessage(s);
  let list=(s.players||[]).slice(0,14);
  lanes.innerHTML=list.map((p,i)=>{
    let x=Math.max(1,Math.min(91,(p.progress||0)*91));
    return '<div class="lane"><div class="line"></div><div class="boost '+((p.boost||0)>.02?'on':'')+'"></div><div class="car" style="left:'+x+'%;color:'+p.color+'"><span class="icon">🏎️</span><span class="name">'+esc(p.name)+'</span></div></div>';
  }).join('');
  let lb=s.leaderboard||[];
  document.getElementById('leaderboard').innerHTML=lb.length?lb.map((r,i)=>'<div class="lbrow"><span class="rank">'+(i+1)+'</span><b>'+esc(r.name)+'</b><span class="pts">'+r.points+' نقطة</span></div>').join(''):'<div style="opacity:.55">ما فيه نتائج للحين</div>';
}
async function poll(){
  try{let r=await fetch('/api/state',{cache:'no-store'});if(!r.ok)throw 0;draw(await r.json())}
  catch{document.getElementById('status').textContent='إعادة اتصال…'}
  setTimeout(poll,450);
}
poll();
</script>
</body></html>"""

@app.get("/")
def index():
    return Response(HTML, mimetype="text/html")

@app.get("/health")
def health():
    s = GAME.state()
    return jsonify(ok=True, live=s["live"], phase=s["phase"], players=s["player_count"], error=s["last_error"])

@app.get("/api/state")
def state():
    return jsonify(GAME.state())

@app.post("/api/demo")
def demo():
    if DEMO_TOKEN and request.headers.get("X-Demo-Token", "") != DEMO_TOKEN:
        return jsonify(error="unauthorized"), 401
    body = request.get_json(silent=True) or {}
    GAME.demo(body.get("count", 8))
    return jsonify(GAME.state())

@app.post("/api/reset")
def reset():
    if DEMO_TOKEN and request.headers.get("X-Demo-Token", "") != DEMO_TOKEN:
        return jsonify(error="unauthorized"), 401
    with GAME.lock:
        GAME.players.clear()
        GAME.waiting_next.clear()
        GAME.winners = []
        GAME.phase = "waiting"
        GAME.phase_started = time.time()
        GAME.round += 1
    return jsonify(GAME.state())

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
