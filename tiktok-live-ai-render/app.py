import os
import math
import time
import random
import asyncio
import threading
from flask import Flask, jsonify, request, Response
from TikTokLive import TikTokLiveClient
from TikTokLive.events import ConnectEvent, DisconnectEvent, CommentEvent, JoinEvent, GiftEvent, FollowEvent, LikeEvent, ShareEvent

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
        self.last_tiktok_event = ""
        self.last_tiktok_event_at = 0.0
        threading.Thread(target=self._game_loop, daemon=True).start()
        threading.Thread(target=self._tiktok_loop, daemon=True).start()

    def _record_tiktok_event(self, kind, username="", extra=""):
        with self.lock:
            self.last_tiktok_event = str(kind)
            self.last_tiktok_event_at = time.time()
        print(f"RACE_EVENT {kind} user={username} {extra}".strip(), flush=True)

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
            effective_live = "LIVE" if self.room_id else self.live
            return dict(
                ok=True, live=effective_live, username=TIKTOK_USERNAME, room_id=self.room_id,
                phase=self.phase, round=self.round, remaining=remaining,
                players=players, player_count=len(players), waiting_next=len(self.waiting_next),
                winners=self.winners, leaderboard=leaders, max_players=MAX_PLAYERS,
                last_error=self.last_error, reconnects=self.reconnects, events=self.events,
                last_tiktok_event=self.last_tiktok_event, last_tiktok_event_at=self.last_tiktok_event_at
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
                        self.room_id = None

                @client.on(JoinEvent)
                async def on_join(event):
                    username, name = who(event)
                    self._record_tiktok_event("JOIN", username)
                    self.join(username, name)

                @client.on(CommentEvent)
                async def on_comment(event):
                    username, name = who(event)
                    text = str(getattr(event, "comment", "") or "").strip()
                    self._record_tiktok_event("COMMENT", username, f"text={text[:40]}")
                    # Any comment is enough to enter the race. This is much more reliable
                    # than depending only on JoinEvent, which TikTok may omit for viewers.
                    self.join(username, name)

                @client.on(FollowEvent)
                async def on_follow(event):
                    username, name = who(event)
                    self._record_tiktok_event("FOLLOW", username)
                    self.join(username, name)
                    self.boost(username, .10)

                @client.on(LikeEvent)
                async def on_like(event):
                    username, name = who(event)
                    count = max(1, int(getattr(event, "count", 1) or 1))
                    self._record_tiktok_event("LIKE", username, f"count={count}")
                    self.join(username, name)
                    self.boost(username, min(.05, count * .002))

                @client.on(ShareEvent)
                async def on_share(event):
                    username, name = who(event)
                    self._record_tiktok_event("SHARE", username)
                    self.join(username, name)
                    self.boost(username, .06)

                @client.on(GiftEvent)
                async def on_gift(event):
                    gift = getattr(event, "gift", None)
                    if getattr(gift, "streakable", False) and getattr(event, "streaking", False):
                        return
                    username, name = who(event)
                    count = max(1, int(getattr(event, "repeat_count", 1) or 1))
                    self._record_tiktok_event("GIFT", username, f"count={count}")
                    self.join(username, name)
                    diamonds = max(0, int(getattr(gift, "diamond_count", 0) or 0)) * count
                    self.boost(username, min(.22, .04 + math.log10(max(1, diamonds)) * .05))

                async def connect():
                    task = await client.start(process_connect_events=True)
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
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#070a10">
<link rel="icon" href="data:,">
<title>سباق الجمهور — TikTok LIVE</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;600;700;800;900&display=swap');
:root{color-scheme:dark;--bg:#070a10;--surface:#101722;--surface-2:#151e2c;--line:#253044;--line-soft:#1b2636;--text:#f4f7fb;--muted:#9aa8ba;--cyan:#55d9f2;--violet:#9182ff;--orange:#ff9559;--gold:#ffd36c;--green:#72dca7;--red:#ff6478;--font:'Tajawal','IBM Plex Sans Arabic','Noto Sans Arabic','Segoe UI',Tahoma,sans-serif}
*{box-sizing:border-box}html,body{margin:0;width:100%;height:100%;overflow:hidden;background:var(--bg);color:var(--text);font-family:var(--font);-webkit-font-smoothing:antialiased}body{font-size:clamp(14px,1.35vw,18px)}[hidden]{display:none!important}button{font:inherit}
.broadcast-wall{position:relative;width:100vw;height:100dvh;overflow:hidden;background:radial-gradient(ellipse at 42% 44%,#132031 0,transparent 42%),radial-gradient(ellipse at 78% 9%,#132139 0,transparent 38%),linear-gradient(145deg,#0b1018,#070a10 62%,#090d14)}
.broadcast-wall:before{content:"";position:absolute;inset:-40%;opacity:.17;background-image:linear-gradient(rgba(100,145,190,.11) 1px,transparent 1px),linear-gradient(90deg,rgba(100,145,190,.11) 1px,transparent 1px);background-size:68px 68px;mask-image:linear-gradient(115deg,transparent 8%,#000 42%,transparent 88%);animation:grid-drift 28s linear infinite;pointer-events:none}
.broadcast-wall:after{content:"";position:absolute;inset:0;background:repeating-linear-gradient(112deg,transparent 0 20%,rgba(72,191,225,.018) 20.1% 20.2%,transparent 20.3% 40%);background-size:180% 100%;animation:speed-drift 19s linear infinite;pointer-events:none}
.frame{position:relative;z-index:1;width:100%;height:100%;padding:clamp(78px,5.8vh,112px) clamp(135px,15.8vw,170px) clamp(175px,14vh,276px) clamp(24px,8.2vw,90px)}
.content{height:100%;min-height:0;display:grid;grid-template-rows:auto minmax(0,1fr) auto;gap:clamp(10px,1.2vh,18px)}
.topbar{min-height:clamp(62px,5.6vh,88px);display:flex;align-items:center;justify-content:space-between;gap:18px;padding:0 clamp(2px,.5vw,8px)}
.brandline{display:flex;align-items:center;gap:clamp(10px,1.5vw,18px);min-width:0}
.brand-mark{width:clamp(42px,4vw,58px);height:clamp(42px,4vw,58px);flex:0 0 auto;display:grid;place-items:center;border-radius:18px;background:linear-gradient(145deg,#153546,#101c2b);border:1px solid #2c5668;box-shadow:inset 0 0 20px #56ddf217}
.brand-mark svg{width:72%;height:72%;display:block}
.brand-copy{min-width:0}.game-title{font-size:clamp(28px,4.5vw,52px);line-height:1.03;font-weight:900;letter-spacing:-.035em;white-space:nowrap}.game-sub{margin-top:5px;color:#a9b7c8;font-size:clamp(15px,1.8vw,21px);line-height:1.2}
.top-meta{display:flex;align-items:center;justify-content:flex-end;gap:clamp(6px,.8vw,11px);min-width:0;flex-wrap:wrap}
.badge{height:clamp(36px,3.5vw,48px);display:inline-flex;align-items:center;justify-content:center;gap:8px;padding:0 clamp(10px,1.4vw,17px);border-radius:999px;border:1px solid #293448;background:#111a27;color:#d7e1ed;font-size:clamp(14px,1.55vw,19px);font-weight:800;white-space:nowrap}
.badge.round{color:#c9d5e3}.badge.count{color:var(--cyan);border-color:#255165;background:#10202b}.live-badge{min-width:82px;border-color:#6a303c;background:#2b151d;color:#ff9ba7}.live-badge .live-dot{width:8px;height:8px;border-radius:50%;background:#ff5369;box-shadow:0 0 12px #ff5369;animation:live-beat 1.8s ease-in-out infinite}.live-badge[data-status="connected"]{border-color:#275c47;background:#10261e;color:#94e9ba}.live-badge[data-status="connected"] .live-dot{background:#59da97;box-shadow:0 0 12px #59da97}.live-badge[data-status="connecting"]{border-color:#625330;background:#272216;color:#efd27d}.live-badge[data-status="connecting"] .live-dot{background:#e4c466;box-shadow:0 0 10px #e4c466;animation:none}.live-badge[data-status="offline"]{border-color:#4b5361;background:#171c23;color:#b5c0cb}.live-badge[data-status="offline"] .live-dot{background:#8593a2;box-shadow:none;animation:none}
.main-stage{display:grid;grid-template-columns:minmax(0,1fr) minmax(250px,292px);gap:clamp(12px,1.35vw,20px);min-height:0}
.race-card,.side-board{position:relative;min-width:0;min-height:0;border:1px solid #263246;border-radius:clamp(20px,2vw,28px);background:linear-gradient(155deg,rgba(17,25,37,.98),rgba(10,15,23,.98));box-shadow:0 18px 46px rgba(0,0,0,.28)}
.race-card{overflow:hidden;display:grid;grid-template-rows:auto minmax(0,1fr);padding:clamp(14px,1.6vw,22px)}
.race-card:before{content:"";position:absolute;inset:0;background:radial-gradient(ellipse at 58% 45%,rgba(42,124,155,.09),transparent 49%);pointer-events:none}
.race-heading{position:relative;z-index:1;display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:clamp(42px,4.2vh,58px);padding:0 2px 10px;border-bottom:1px solid rgba(125,151,182,.14)}
.phase-label{display:flex;align-items:center;gap:10px;min-width:0;color:#eaf3fa;font-size:clamp(18px,2.1vw,26px);font-weight:800}.phase-light{width:9px;height:9px;flex:0 0 auto;border-radius:50%;background:var(--cyan);box-shadow:0 0 14px #55d9f277}.phase-label[data-phase="waiting"] .phase-light{background:#9cacbd;box-shadow:none}.phase-label[data-phase="countdown"] .phase-light{background:var(--orange);box-shadow:0 0 14px #ff955977;animation:live-beat .7s infinite}.phase-label[data-phase="finished"] .phase-light{background:var(--gold);box-shadow:0 0 14px #ffd36c88}
.phase-detail{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--muted);font-size:clamp(14px,1.55vw,19px);font-weight:600;text-align:left}
.track-field{position:relative;min-height:0;overflow:hidden;isolation:isolate;padding-top:clamp(12px,1.3vw,18px)}
.track-field:before{content:"";position:absolute;inset:0 0 12px;z-index:-2;background:repeating-linear-gradient(0deg,transparent 0 48px,rgba(109,141,171,.035) 49px,transparent 50px),radial-gradient(ellipse at 55% 50%,rgba(16,104,133,.09),transparent 62%)}
.track-field:after{content:"";position:absolute;z-index:-1;inset:8% 3% 9%;border:1px solid rgba(112,149,179,.08);border-radius:24px;pointer-events:none}
.lanes{position:relative;z-index:1;width:100%;height:100%;display:flex;flex-direction:column;justify-content:center;gap:clamp(8px,.62vh,12px);padding:0 1px 28px;direction:ltr}
.lane{position:relative;flex:1 1 64px;min-height:48px;max-height:clamp(70px,4.8vh,92px);overflow:hidden;border-radius:clamp(15px,1.7vw,22px);border:1px solid #28364a;background:linear-gradient(90deg,#111a26,#121c29 54%,#0f1824);box-shadow:inset 0 1px 0 rgba(255,255,255,.025);direction:ltr;will-change:transform;transition:transform 320ms cubic-bezier(.2,.75,.25,1),border-color 220ms,background 220ms}
.lane:after{content:"";position:absolute;inset:0 auto 0 0;width:35%;background:linear-gradient(90deg,rgba(0,0,0,.08),transparent);pointer-events:none}
.lane.is-leader{border-color:#347186;background:linear-gradient(90deg,#12212c,#142633 70%,#111c29);box-shadow:inset 0 0 0 1px #55d9f21a,0 0 18px #35b9d511}
.lane.is-winner{border-color:#836a36;background:linear-gradient(90deg,#191a20,#1d1c20 68%,#191a20);box-shadow:inset 0 0 0 1px #ffd36c20}
.rank-badge{position:absolute;left:12px;top:50%;transform:translateY(-50%);width:clamp(34px,3.4vw,46px);height:clamp(34px,3.4vw,46px);display:grid;place-items:center;border-radius:13px;background:#1a2634;color:#c2ceda;font-size:clamp(16px,1.7vw,21px);font-weight:900;z-index:3;direction:ltr}
.is-leader .rank-badge{background:#123545;color:#73e5f7;box-shadow:0 0 18px #55d9f220}.is-winner .rank-badge{background:#3a3020;color:var(--gold)}
.player-info{position:absolute;z-index:3;left:clamp(54px,6vw,76px);top:50%;transform:translateY(-50%);width:clamp(100px,28%,310px);min-width:0;display:flex;align-items:center;gap:7px;direction:rtl;text-align:right}
.player-crown{display:none;color:var(--gold);font-size:clamp(15px,1.7vw,21px);line-height:1;flex:0 0 auto}.is-leader .player-crown{display:inline-block}.player-name{display:block;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#f2f6fa;font-size:clamp(17px,2.4vw,28px);line-height:1.1;font-weight:800;text-shadow:0 1px 6px #0008}
.race-rail{position:absolute;left:43%;right:4.2%;top:50%;height:clamp(12px,1.35vh,17px);transform:translateY(-50%);border-radius:999px;background:#080d14;border:1px solid #2b384a;box-shadow:inset 0 2px 4px #0009;direction:ltr}
.race-rail:before{content:"";position:absolute;inset:0;border-radius:inherit;background:repeating-linear-gradient(90deg,transparent 0 9%,rgba(173,197,218,.13) 9.2% 9.5%,transparent 9.7% 18%);opacity:.42}
.rail-fill{position:absolute;left:0;top:0;bottom:0;width:0;border-radius:inherit;background:linear-gradient(90deg,color-mix(in srgb,var(--player-color) 54%,transparent),var(--player-color));opacity:.57;transition:width 430ms cubic-bezier(.2,.8,.25,1);box-shadow:0 0 12px color-mix(in srgb,var(--player-color) 30%,transparent)}
.vehicle{position:absolute;z-index:4;left:0;top:50%;width:clamp(47px,5.1vw,66px);height:auto;transform:translate(-50%,-50%);color:var(--player-color);filter:drop-shadow(0 4px 5px rgba(0,0,0,.55));transition:left 430ms cubic-bezier(.2,.8,.25,1);will-change:left}
.vehicle svg{display:block;width:100%;height:auto;overflow:visible}.vehicle:after{content:"";position:absolute;left:13%;right:72%;top:48%;height:3px;border-radius:9px;background:linear-gradient(90deg,transparent,var(--player-color));opacity:.42;filter:blur(2px)}
.finish-mark{position:absolute;z-index:3;right:-2px;top:50%;width:clamp(10px,1vw,14px);height:clamp(30px,4vh,46px);transform:translateY(-50%);border-radius:2px;background:repeating-conic-gradient(#e8edf3 0 25%,#17202b 0 50%) 50% / 8px 8px;box-shadow:0 0 9px rgba(208,230,246,.22)}
.boost-flare{position:absolute;z-index:5;left:47%;top:50%;transform:translate(-50%,-50%) scale(.7);opacity:0;pointer-events:none;padding:5px 11px;border:1px solid #ffb66e66;border-radius:999px;background:#361f16;color:#ffd3a2;font-size:clamp(13px,1.4vw,17px);font-weight:900;white-space:nowrap}
.boost-event{animation:car-boost 430ms ease-out}.boost-event .vehicle{animation:car-pop 450ms cubic-bezier(.2,.8,.2,1)}.boost-event .boost-flare{animation:boost-tag 850ms ease-out}.boost-major .boost-flare{border-color:#ffc981aa;background:#4a2718;color:#ffe1b8;box-shadow:0 0 18px #ff995522}
.near-finish .finish-mark{animation:finish-pulse 1.1s ease-in-out infinite;box-shadow:0 0 18px #ffb760bb}.near-finish .race-rail{box-shadow:inset 0 2px 4px #0009,0 0 11px #ffaf4a2b}
.near-label{position:absolute;z-index:5;top:3px;right:6%;color:#ffe1a3;font-size:clamp(13px,1.45vw,17px);font-weight:900;opacity:0;transform:translateY(4px);transition:opacity 180ms,transform 180ms}.is-near .near-label{opacity:1;transform:translateY(0)}
.pack-note{position:absolute;bottom:0;left:50%;transform:translateX(-50%);padding:5px 12px;border:1px solid #30465a;border-radius:999px;background:#101a26;color:#bdcbd9;font-size:clamp(13px,1.4vw,17px);font-weight:700;white-space:nowrap;z-index:4}
.empty-race{position:absolute;inset:12% 6% 14%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;text-align:center;pointer-events:none}
.empty-title{font-size:clamp(25px,3.2vw,38px);font-weight:900;color:#edf5fa}.empty-copy{font-size:clamp(16px,1.85vw,22px);color:#9cacbd}.idle-road{position:relative;width:min(72%,480px);height:74px;margin-top:14px;border-block:1px solid #283a4b;background:repeating-linear-gradient(90deg,transparent 0 30px,rgba(166,195,216,.16) 30px 56px,transparent 56px 86px);background-position:0 center;animation:road-scroll 3s linear infinite;opacity:.72}
.idle-car{position:absolute;top:50%;width:66px;color:#63d9ee;transform:translateY(-50%);animation:idle-drive 4.5s ease-in-out infinite}.idle-car svg{width:100%;display:block}.idle-car.second{color:#8276e8;animation-delay:-2.2s}
.phase-overlay{position:absolute;z-index:8;inset:0;display:grid;place-items:center;pointer-events:none;background:radial-gradient(ellipse at center,rgba(4,8,13,.55),rgba(4,8,13,.08) 68%,transparent)}
.countdown-mark{display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;min-width:160px;filter:drop-shadow(0 8px 22px #0009)}.countdown-caption{color:#d7e5ee;font-size:clamp(18px,2.2vw,27px);font-weight:800;letter-spacing:.04em}.countdown-value{font-size:clamp(112px,17vh,176px);line-height:.94;font-weight:900;color:#fff;text-shadow:0 0 32px #4fd5f177,0 8px 22px #000b;animation:none}.countdown-value.count-pop{animation:count-pop 800ms cubic-bezier(.18,.85,.28,1) both}.countdown-value.go{font-size:clamp(68px,9vw,116px);color:var(--cyan);letter-spacing:-.04em}
.winner-spotlight{position:absolute;z-index:8;left:37%;right:11%;top:50%;transform:translateY(-50%);display:flex;align-items:center;gap:clamp(12px,2vw,24px);padding:clamp(15px,2vw,26px) clamp(18px,2.6vw,34px);border:1px solid #806b39;border-radius:24px;background:linear-gradient(110deg,rgba(22,24,27,.97),rgba(50,40,23,.97));box-shadow:0 18px 50px #0009,0 0 32px #ffd36c18;animation:winner-enter 650ms cubic-bezier(.18,.85,.28,1) both}
.winner-cup{width:clamp(44px,5vw,68px);height:clamp(44px,5vw,68px);flex:0 0 auto;display:grid;place-items:center;border-radius:18px;background:#4a3a1d;color:var(--gold);font-size:clamp(28px,3.5vw,44px)}.winner-copy{min-width:0}.winner-overline{color:#e7ca82;font-size:clamp(14px,1.5vw,18px);font-weight:800}.winner-name{max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#fff2d1;font-size:clamp(28px,4vw,46px);line-height:1.15;font-weight:900}.winner-caption{color:#d6c89f;font-size:clamp(16px,1.8vw,21px);font-weight:700}
.winner-freeze .lane{filter:saturate(.75);transition:none}.event-toast{position:absolute;z-index:10;left:50%;top:clamp(60px,8vh,100px);transform:translate(-50%,-8px);max-width:min(82%,520px);padding:10px 18px;border:1px solid #346176;border-radius:999px;background:#10232d;color:#c8f7ff;font-size:clamp(15px,1.7vw,20px);font-weight:800;text-align:center;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;opacity:0;pointer-events:none;box-shadow:0 8px 24px #0007}
.event-toast.show{animation:toast-in 1.35s ease both}.confetti{position:absolute;z-index:7;top:38%;left:50%;width:6px;height:10px;border-radius:2px;background:var(--confetti,#55d9f2);opacity:0;pointer-events:none;animation:confetti-fall 1.3s ease-out var(--delay,0ms) both}.side-board{display:flex;flex-direction:column;overflow:hidden;padding:clamp(16px,1.5vw,22px)}
.board-heading{display:flex;align-items:center;justify-content:space-between;gap:8px;padding-bottom:15px;border-bottom:1px solid #263246}.board-title{display:flex;align-items:center;gap:9px;font-size:clamp(21px,1.95vw,26px);font-weight:900;white-space:nowrap;flex-shrink:0}.board-title svg{width:26px;height:26px;color:var(--gold)}.board-label{color:#94a5b8;font-size:clamp(13px,1.3vw,16px);font-weight:700;white-space:nowrap}
.leaderboard{display:grid;gap:8px;padding-top:14px}.lbrow{min-width:0;min-height:clamp(49px,5.6vh,64px);display:grid;grid-template-columns:34px minmax(0,1fr) auto;align-items:center;gap:8px;padding:8px 10px;border:1px solid #202d3e;border-radius:15px;background:#111a27}.lb-rank{width:28px;height:28px;display:grid;place-items:center;border-radius:9px;background:#1c2938;color:#bdcad9;font-size:17px;font-weight:900;direction:ltr}.lbrow:nth-child(1) .lb-rank{background:#3e351e;color:var(--gold)}.lbrow:nth-child(2) .lb-rank{background:#29323c;color:#dce4eb}.lbrow:nth-child(3) .lb-rank{background:#3d2b22;color:#e9b08a}.lb-name{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#e9f0f6;font-size:clamp(16px,1.65vw,20px);font-weight:800}.lb-points{color:#f2d27f;font-size:clamp(14px,1.45vw,17px);font-weight:800;white-space:nowrap}.empty-board{padding:22px 12px;color:#8b9aad;text-align:center;font-size:17px}
.interaction-key{margin-top:auto;padding:14px 13px;border:1px solid #223144;border-radius:16px;background:#0d151f}.interaction-title{margin-bottom:10px;color:#d9e4ec;font-size:18px;font-weight:800}.interaction-line{display:flex;align-items:center;gap:9px;margin-top:7px;color:#aebdcb;font-size:16px}.interaction-icon{width:25px;height:25px;display:grid;place-items:center;flex:0 0 auto;border-radius:8px;background:#192634;color:var(--cyan);font-weight:900}
.interaction-key{padding:12px}.interaction-title{margin-bottom:8px}.interaction-line{margin-top:6px;line-height:1.25}.interaction-icon.like{color:#ff8ca1;background:#2a1c27}.interaction-icon.follow{color:var(--cyan)}.interaction-icon.gift{color:#ffc27e;background:#2b241d}.interaction-icon.join{color:#9c91ff;background:#211e34}
.viewer-guide{display:none;min-width:0;padding:7px 9px;border:1px solid #223144;border-radius:16px;background:linear-gradient(135deg,rgba(15,25,36,.96),rgba(11,17,26,.97));box-shadow:0 8px 24px #0003}.guide-head{min-height:24px;display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:6px}.guide-title{display:flex;align-items:center;gap:7px;color:#e5eef6;font-size:clamp(15px,1.65vw,19px);font-weight:900;white-space:nowrap}.guide-title-mark{color:var(--cyan);font-size:18px}.guide-entry{max-width:56%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding:4px 9px;border:1px solid #274453;border-radius:999px;background:#10212b;color:#c2f4fa;font-size:clamp(12px,1.3vw,15px);font-weight:800}.guide-actions{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:7px}.guide-action{position:relative;min-width:0;min-height:56px;display:grid;grid-template-columns:28px minmax(0,1fr);grid-template-rows:auto auto;align-content:center;align-items:center;column-gap:7px;padding:5px 8px;border:1px solid #273648;border-radius:12px;background:linear-gradient(120deg,#141e2a,#101822);overflow:hidden}.guide-action:after{content:"";position:absolute;inset:0 auto 0 0;width:28%;background:linear-gradient(90deg,color-mix(in srgb,var(--guide-color) 9%,transparent),transparent);pointer-events:none}.guide-icon{grid-row:1/3;width:27px;height:27px;display:grid;place-items:center;border-radius:9px;background:color-mix(in srgb,var(--guide-color) 14%,#111923);color:var(--guide-color);font-size:17px;font-weight:900;z-index:1}.guide-action.like{--guide-color:#ff8ca1}.guide-action.follow{--guide-color:#55d9f2}.guide-action.gift{--guide-color:#ffbd78}.guide-name{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#eef4f8;font-size:clamp(14px,1.5vw,17px);font-weight:900;line-height:1.1}.guide-effect{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#aab9c8;font-size:clamp(12px,1.25vw,14px);font-weight:700;line-height:1.15}.guide-meter{grid-column:1/-1;display:flex;gap:3px;margin-top:4px;height:3px}.guide-meter i{flex:1;border-radius:999px;background:#2b3746}.guide-meter i.on{background:var(--guide-color);box-shadow:0 0 6px color-mix(in srgb,var(--guide-color) 35%,transparent)}
.footer-deck{min-width:0;display:grid;grid-template-rows:auto auto;gap:9px}.mobile-board{display:none;min-width:0}.mobile-board .board-heading{padding-bottom:5px;border-bottom:0}.mobile-board .board-title{font-size:clamp(17px,1.9vw,22px)}.mobile-board .board-title svg{width:21px;height:21px}.mobile-board .board-label{font-size:14px}.mobile-board .leaderboard{padding-top:0;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px}.mobile-board .lbrow{min-height:38px;grid-template-columns:24px minmax(0,1fr) auto;gap:5px;padding:5px 7px;border-radius:12px}.mobile-board .lb-rank{width:22px;height:22px;border-radius:7px;font-size:14px}.mobile-board .lb-name{font-size:clamp(15px,1.65vw,18px)}.mobile-board .lb-points{font-size:clamp(13px,1.45vw,16px)}.mobile-board .empty-board{padding:8px}
.tip-line{min-height:clamp(38px,4.2vh,50px);display:flex;align-items:center;justify-content:space-between;gap:12px;padding:0 6px;color:#bdc9d5;font-size:clamp(15px,1.7vw,20px)}.tip-copy{display:flex;align-items:center;gap:9px;min-width:0}.tip-symbol{width:30px;height:30px;display:grid;place-items:center;flex:0 0 auto;border:1px solid #2b5260;border-radius:10px;background:#10242e;color:var(--cyan);font-weight:900}.tip-text{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:700}.queue-indicator{display:inline-flex;align-items:center;gap:7px;padding:7px 11px;border:1px solid #29384a;border-radius:999px;background:#111923;color:#aebdcb;font-size:clamp(13px,1.45vw,17px);font-weight:700;white-space:nowrap}.queue-indicator strong{color:#eef5fa;font-size:1.08em}
.results-deck{display:none;min-width:0}.results-deck.active{display:block}.results-heading{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:8px}.results-title{color:#f3e1ad;font-size:clamp(18px,2vw,24px);font-weight:900}.next-round{color:#c7d2dc;font-size:clamp(14px,1.55vw,18px);font-weight:800;white-space:nowrap}.podium{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));align-items:end;gap:clamp(7px,1vw,13px)}.pod{min-width:0;min-height:clamp(76px,8.2vh,110px);display:flex;flex-direction:column;align-items:center;justify-content:center;gap:4px;padding:8px 9px;border:1px solid #303948;border-radius:16px 16px 13px 13px;background:linear-gradient(160deg,#18212c,#101722);text-align:center}.pod.first{min-height:clamp(91px,9.7vh,128px);border-color:#816c39;background:linear-gradient(155deg,#29251a,#181821);box-shadow:0 0 22px #ffd36c13}.pod-rank{color:#bfcbd6;font-size:clamp(13px,1.4vw,16px);font-weight:800}.pod.first .pod-rank{color:var(--gold)}.pod-name{width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#f2f5f7;font-size:clamp(17px,2vw,23px);font-weight:900}.pod.first .pod-name{color:#fff0c4;font-size:clamp(19px,2.35vw,28px)}.pod-medal{font-size:clamp(16px,1.8vw,21px);line-height:1}
#demo-error{position:absolute;inset:auto auto 12px 12px;color:#fa8b95;font-size:12px;opacity:.85}
@keyframes grid-drift{to{transform:translate3d(68px,68px,0)}}@keyframes speed-drift{to{background-position:180% 0}}@keyframes live-beat{0%,100%{opacity:1}50%{opacity:.55}}@keyframes road-scroll{to{background-position:86px center}}@keyframes idle-drive{0%,100%{left:15%;transform:translateY(-50%) translateX(-3px)}50%{left:80%;transform:translateY(-50%) translateX(3px)}}@keyframes count-pop{0%{opacity:0;transform:scale(.68)}60%{opacity:1;transform:scale(1.1)}100%{opacity:1;transform:scale(1)}}@keyframes car-pop{0%{transform:translate(-50%,-50%) scale(1)}38%{transform:translate(-50%,-50%) scale(1.1)}100%{transform:translate(-50%,-50%) scale(1)}}@keyframes car-boost{0%,100%{box-shadow:inset 0 0 0 transparent}25%{box-shadow:inset 0 0 34px color-mix(in srgb,var(--player-color) 28%,transparent)}}@keyframes boost-tag{0%{opacity:0;transform:translate(-50%,-50%) scale(.72)}22%{opacity:1;transform:translate(-50%,-50%) scale(1)}78%{opacity:1;transform:translate(-50%,-50%) scale(1)}100%{opacity:0;transform:translate(-50%,-65%) scale(.94)}}@keyframes finish-pulse{0%,100%{filter:brightness(1)}50%{filter:brightness(1.75)}}@keyframes winner-enter{0%{opacity:0;transform:translateY(calc(-50% + 12px)) scale(.97)}100%{opacity:1;transform:translateY(-50%) scale(1)}}@keyframes toast-in{0%{opacity:0;transform:translate(-50%,-8px)}12%,76%{opacity:1;transform:translate(-50%,0)}100%{opacity:0;transform:translate(-50%,-5px)}}@keyframes confetti-fall{0%{opacity:0;transform:translate3d(var(--dx,0),-16px,0) rotate(0)}12%{opacity:.95}100%{opacity:0;transform:translate3d(calc(var(--dx,0) + 30px),120px,0) rotate(230deg)}}
@media (orientation:landscape) and (min-width:760px){.main-stage{direction:ltr}.race-card,.side-board{direction:rtl}.frame{padding:clamp(30px,5vh,58px) clamp(120px,8.5vw,170px) clamp(28px,4vh,48px) clamp(34px,4vw,74px)}.content{gap:clamp(8px,1.1vh,14px)}.main-stage{grid-template-columns:minmax(0,1fr) minmax(250px,19vw);gap:clamp(12px,1.35vw,22px)}.footer-deck{grid-template-columns:minmax(0,1fr) auto;grid-template-rows:auto;align-items:center;gap:18px}.mobile-board{display:none!important}.viewer-guide{display:none!important}.tip-line{min-height:38px}.side-board{display:flex}}
@media (orientation:portrait), (max-width:759px){.footer-deck{min-height:clamp(196px,11.5vh,220px);grid-template-rows:auto auto auto}.frame{padding-right:clamp(135px,15.8vw,170px)}.topbar{gap:8px}.top-meta{gap:4px;flex-wrap:nowrap}.badge{height:32px;padding:0 8px;font-size:12px}.live-badge{min-width:0}.live-badge .live-word{font-size:11px}.main-stage{grid-template-columns:minmax(0,1fr)}.side-board{display:none}.mobile-board{display:block}.viewer-guide{display:block}.race-card{padding:clamp(11px,1.5vw,18px)}.race-heading{min-height:40px}.phase-label{font-size:clamp(16px,2vw,22px)}.phase-detail{max-width:45%;font-size:clamp(13px,1.5vw,17px)}.lane{flex-basis:clamp(48px,5.1vh,76px);min-height:46px}.player-info{width:clamp(94px,28%,235px);left:clamp(47px,6vw,68px)}.race-rail{left:42%;right:4%}.vehicle{width:clamp(43px,5vw,60px)}.footer-deck{gap:4px}.viewer-guide{padding:6px 8px}.guide-head{min-height:22px;margin-bottom:5px}.guide-action{min-height:52px;padding:4px 6px}.guide-meter{margin-top:3px}.tip-line{min-height:34px}.interaction-key{display:none}.winner-spotlight{left:28%;right:7%;}}
@media (max-width:600px){.footer-deck{min-height:170px}.frame{padding-left:22px;padding-right:135px;padding-bottom:clamp(150px,14vh,188px)}.topbar{gap:8px}.brand-mark{width:38px;height:38px;border-radius:13px}.game-title{font-size:clamp(24px,5vw,32px)}.game-sub{font-size:14px}.top-meta{gap:5px}.badge{height:32px;padding:0 9px;font-size:13px}.live-badge{min-width:0}.live-badge .live-word{display:none}.race-card{padding:9px;border-radius:18px}.race-heading{min-height:36px;padding-bottom:7px}.phase-label{font-size:15px;gap:7px}.phase-light{width:7px;height:7px}.phase-detail{max-width:43%;font-size:12px}.lane{flex-basis:clamp(45px,4.7vh,62px);min-height:43px;border-radius:13px}.rank-badge{left:7px;width:30px;height:30px;border-radius:9px;font-size:15px}.player-info{left:42px;width:clamp(82px,27%,160px);gap:4px}.player-name{font-size:clamp(15px,2.4vw,17px)}.race-rail{left:41%;right:4%;height:10px}.vehicle{width:42px}.finish-mark{width:9px}.near-label{right:5%;top:2px;font-size:12px}.pack-note{font-size:12px}.mobile-board .board-heading{padding-bottom:3px}.mobile-board .board-title{font-size:16px}.mobile-board .leaderboard{gap:4px}.mobile-board .lbrow{min-height:34px;gap:4px;padding:4px 5px}.mobile-board .lb-rank{width:20px;height:20px;font-size:12px}.mobile-board .lb-name{font-size:12px}.mobile-board .lb-points{font-size:11px}.viewer-guide{padding:5px 6px;border-radius:13px}.guide-head{min-height:20px;margin-bottom:4px}.guide-title{font-size:14px;gap:5px}.guide-title-mark{font-size:15px}.guide-entry{font-size:11px;padding:3px 6px}.guide-actions{gap:4px}.guide-action{min-height:47px;grid-template-columns:22px minmax(0,1fr);column-gap:5px;padding:3px 5px;border-radius:10px}.guide-icon{width:21px;height:21px;border-radius:7px;font-size:14px}.guide-name{font-size:12px}.guide-effect{font-size:11px}.guide-meter{margin-top:2px;height:2px}.tip-line{min-height:29px;padding:0 2px}.tip-symbol{width:23px;height:23px;border-radius:7px}.tip-text{font-size:12px}.queue-indicator{padding:4px 6px;font-size:10px}.winner-spotlight{left:20%;right:5%;gap:10px;padding:12px 14px}.winner-cup{width:38px;height:38px;border-radius:12px;font-size:24px}.winner-name{font-size:24px}.winner-overline{font-size:12px}.winner-caption{font-size:14px}.podium{gap:5px}.pod{min-height:68px;padding:6px 5px;border-radius:12px}.pod.first{min-height:82px}.pod-name{font-size:14px}.pod.first .pod-name{font-size:17px}.results-title{font-size:16px}.next-round{font-size:12px}}
@media (max-width:430px){.guide-head{align-items:flex-start;flex-direction:column;gap:3px}.guide-entry{max-width:100%;align-self:flex-start}.guide-action{grid-template-columns:1fr;grid-template-rows:auto auto auto;justify-items:center;gap:1px;padding:3px 2px}.guide-icon{grid-row:auto;width:18px;height:18px;font-size:12px}.guide-name{font-size:11px}.guide-effect{font-size:10px}.guide-meter{width:80%;grid-column:auto}}
@media (max-height:760px) and (orientation:landscape){.topbar{min-height:52px}.brand-mark{width:40px;height:40px}.game-title{font-size:30px}.game-sub{font-size:14px}.race-card{padding:11px}.race-heading{min-height:37px}.lane{flex-basis:42px;min-height:36px}.rank-badge{width:31px;height:31px}.player-name{font-size:17px}.vehicle{width:48px}.lbrow{min-height:43px}.interaction-key{padding:9px 11px}.interaction-line{margin-top:4px}}
@media (prefers-reduced-motion:reduce){*,*:before,*:after{scroll-behavior:auto!important;animation-duration:.01ms!important;animation-iteration-count:1!important;transition-duration:.01ms!important}}
</style>
</head>
<body>
<div class="broadcast-wall">
  <div class="frame">
    <div class="content">
      <header class="topbar">
        <div class="brandline">
          <div class="brand-mark" aria-hidden="true"><svg viewBox="0 0 64 64" fill="none"><path d="M9 38h43l-5 12H17L9 38Z" fill="#56D9F2"/><path d="M18 36 25 22h14l9 14H18Z" fill="#EAF8FB"/><path d="m28 25-5 10h20l-6-10H28Z" fill="#142C3A"/><path d="M8 52h48" stroke="#55D9F2" stroke-width="3" stroke-linecap="round"/><circle cx="20" cy="50" r="5" fill="#0A1018" stroke="#C6F7FF" stroke-width="2"/><circle cx="45" cy="50" r="5" fill="#0A1018" stroke="#C6F7FF" stroke-width="2"/><path d="M8 14h10v6H8zm13 0h10v6H21zm13 0h10v6H34zm13 0h10v6H47zM8 22h10v6H8zm26 0h10v6H34zm13 0h10v6H47z" fill="#55D9F2" opacity=".8"/></svg></div>
          <div class="brand-copy"><div class="game-title">سباق الجمهور</div><div class="game-sub" id="subtitle">ادخل البث… تدخل السباق تلقائيًا</div></div>
        </div>
        <div class="top-meta">
          <div id="live" class="badge live-badge" data-status="connecting"><span class="live-dot"></span><span class="live-word">اتصال</span></div>
          <div id="round" class="badge round">الجولة 1</div>
          <div id="count" class="badge count" dir="ltr">0 / 24</div>
        </div>
      </header>
      <main class="main-stage">
        <section id="raceCard" class="race-card" aria-label="مضمار السباق">
          <div class="race-heading">
            <div id="phaseLabel" class="phase-label" data-phase="waiting"><span class="phase-light"></span><span id="phaseText">المضمار جاهز</span></div>
            <div id="phaseDetail" class="phase-detail">ادخل البث وشارك في الجولة</div>
          </div>
          <div id="trackField" class="track-field">
            <div id="lanes" class="lanes" aria-live="polite"></div>
            <div id="emptyRace" class="empty-race"><div class="empty-title">الجولة الجاية تبدأ بعد شوي</div><div class="empty-copy">أول ما تدخل البث، سيارتك تدخل السباق</div><div class="idle-road"><div class="idle-car"><svg viewBox="0 0 96 54" fill="none" aria-hidden="true"><path d="M10 34.5c1.2-4.2 4.3-6.8 9-7.7l8.6-2 10.4-11.2c1.8-1.9 4.3-3 7-3h13.1c3.8 0 6.7 1.7 9.1 4.6l8.7 10.6 8.5 2.2c4.4 1.1 7.2 4.1 8.1 8.5l.8 4.1c.5 2.4-1.3 4.6-3.8 4.6h-6.6a12 12 0 0 0-23.2 0H37a12 12 0 0 0-23.2 0h-2.2c-2.6 0-4.3-2.2-3.8-4.6l2.2-6.1Z" fill="currentColor"/><path d="m32 23.3 8.7-9.1c1-1 2.5-1.6 4-1.6h12.8c2 0 3.8.9 5.1 2.4l6.8 8.3H32Z" fill="#D8F8FC" fill-opacity=".85"/><circle cx="25.5" cy="42" r="6.2" fill="#101722" stroke="#EAF7F8" stroke-width="2.6"/><circle cx="68.4" cy="42" r="6.2" fill="#101722" stroke="#EAF7F8" stroke-width="2.6"/></svg></div><div class="idle-car second"><svg viewBox="0 0 96 54" fill="none" aria-hidden="true"><path d="M10 34.5c1.2-4.2 4.3-6.8 9-7.7l8.6-2 10.4-11.2c1.8-1.9 4.3-3 7-3h13.1c3.8 0 6.7 1.7 9.1 4.6l8.7 10.6 8.5 2.2c4.4 1.1 7.2 4.1 8.1 8.5l.8 4.1c.5 2.4-1.3 4.6-3.8 4.6h-6.6a12 12 0 0 0-23.2 0H37a12 12 0 0 0-23.2 0h-2.2c-2.6 0-4.3-2.2-3.8-4.6l2.2-6.1Z" fill="currentColor"/><path d="m32 23.3 8.7-9.1c1-1 2.5-1.6 4-1.6h12.8c2 0 3.8.9 5.1 2.4l6.8 8.3H32Z" fill="#E8E3FF" fill-opacity=".82"/><circle cx="25.5" cy="42" r="6.2" fill="#101722" stroke="#F1EEFF" stroke-width="2.6"/><circle cx="68.4" cy="42" r="6.2" fill="#101722" stroke="#F1EEFF" stroke-width="2.6"/></svg></div></div></div>
            <div id="phaseOverlay" class="phase-overlay" hidden><div class="countdown-mark"><div id="countdownCaption" class="countdown-caption">الاستعداد</div><div id="countdownValue" class="countdown-value">3</div></div></div>
            <div id="winnerMoment" class="winner-spotlight" hidden><div class="winner-cup" aria-hidden="true">♛</div><div class="winner-copy"><div class="winner-overline">بطل الجولة</div><div id="winnerName" class="winner-name" dir="auto">—</div><div class="winner-caption">فاز بالجولة 🏁</div></div></div>
            <div id="packNote" class="pack-note" hidden></div>
            <div id="eventToast" class="event-toast" aria-live="polite" role="status"></div>
          </div>
        </section>
        <aside class="side-board" aria-label="أفضل خمسة متسابقين">
          <div class="board-heading"><div class="board-title"><svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="m5 4 4.2 3 2.8-4 2.8 4L19 4l-1.5 9H6.5L5 4Z" fill="currentColor"/><path d="M7 16h10M8 20h8" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>الترتيب</div><span class="board-label">أفضل 5</span></div>
          <div id="leaderboardSide" class="leaderboard"></div>
          <div class="interaction-key"><div class="interaction-title">كيف تدخل وتدعم سيارتك؟</div><div class="interaction-line"><span class="interaction-icon join">↳</span><span>ادخل البث لتدخل السباق تلقائيًا</span></div><div class="interaction-line"><span class="interaction-icon like">♥</span><span>كل لايك يعطي دفعة خفيفة لسيارتك</span></div><div class="interaction-line"><span class="interaction-icon follow">↗</span><span>المتابعة تمنح سيارتك دفعة متوسطة</span></div><div class="interaction-line"><span class="interaction-icon gift">✦</span><span>الهدية تمنح Boost أقوى حسب قيمتها</span></div></div>
        </aside>
      </main>
      <section class="footer-deck">
        <div id="mobile-board" class="mobile-board"><div class="board-heading"><div class="board-title"><svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="m5 4 4.2 3 2.8-4 2.8 4L19 4l-1.5 9H6.5L5 4Z" fill="currentColor"/><path d="M7 16h10M8 20h8" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>أفضل 5</div><span class="board-label">نقاط الموسم</span></div><div id="leaderboardMobile" class="leaderboard"></div></div>
        <div id="resultsDeck" class="results-deck"><div class="results-heading"><div class="results-title">🏆 منصة الفائزين</div><div id="nextRound" class="next-round">الجولة التالية قريبًا</div></div><div id="podium" class="podium"></div></div>
        <section class="viewer-guide" aria-label="طريقة المشاركة وتأثير التفاعلات">
          <div class="guide-head"><div class="guide-title"><span class="guide-title-mark" aria-hidden="true">⚡</span><span>كيف تساعد سيارتك؟</span></div><span class="guide-entry">ادخل البث = تدخل السباق تلقائيًا</span></div>
          <div class="guide-actions">
            <div class="guide-action like"><span class="guide-icon" aria-hidden="true">♥</span><span class="guide-name">لايك</span><span class="guide-effect">دفعة خفيفة</span><span class="guide-meter" aria-label="قوة خفيفة"><i class="on"></i><i></i><i></i></span></div>
            <div class="guide-action follow"><span class="guide-icon" aria-hidden="true">↗</span><span class="guide-name">متابعة</span><span class="guide-effect">دفعة متوسطة</span><span class="guide-meter" aria-label="قوة متوسطة"><i class="on"></i><i class="on"></i><i></i></span></div>
            <div class="guide-action gift"><span class="guide-icon" aria-hidden="true">✦</span><span class="guide-name">هدية</span><span class="guide-effect">أقوى حسب قيمتها</span><span class="guide-meter" aria-label="قوة أكبر"><i class="on"></i><i class="on"></i><i class="on"></i></span></div>
          </div>
        </section>
        <div class="tip-line"><div class="tip-copy"><span id="tipSymbol" class="tip-symbol">♥</span><span id="tipText" class="tip-text">اللايك يعطي دفعة خفيفة</span></div><div id="queue" class="queue-indicator" hidden><span>ينتظرون التالية</span><strong id="queueCount">0</strong></div></div>
      </section>
    </div>
  </div>
</div>
<script>
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const lanesEl = $('lanes');
  const liveEl = $('live');
  const toastEl = $('eventToast');
  const state = { previous: null, nodes: new Map(), lastLeader: null, lastPhase: null, lastCountdown: null, tipKey: '', toastTimer: 0, finishKey: '' };
  const palette = ['#ff6478','#50c7f2','#ffd36c','#73d69b','#a78bfa','#ff9559','#f783ac','#63e6be','#74c0fc','#e599f7','#ff8787','#8ce99a'];
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const safeColor = (value, index) => /^#[0-9a-f]{6}$/i.test(String(value || '')) ? value : palette[index % palette.length];
  const playerKey = (player) => String(player.username || player.name || 'player');
  const displayName = (player) => String(player.name || player.username || 'متسابق');
  const carSvg = '<svg viewBox="0 0 96 54" fill="none" aria-hidden="true"><path d="M10 34.5c1.2-4.2 4.3-6.8 9-7.7l8.6-2 10.4-11.2c1.8-1.9 4.3-3 7-3h13.1c3.8 0 6.7 1.7 9.1 4.6l8.7 10.6 8.5 2.2c4.4 1.1 7.2 4.1 8.1 8.5l.8 4.1c.5 2.4-1.3 4.6-3.8 4.6h-6.6a12 12 0 0 0-23.2 0H37a12 12 0 0 0-23.2 0h-2.2c-2.6 0-4.3-2.2-3.8-4.6l2.2-6.1Z" fill="currentColor"/><path d="m32 23.3 8.7-9.1c1-1 2.5-1.6 4-1.6h12.8c2 0 3.8.9 5.1 2.4l6.8 8.3H32Z" fill="#DAF8FC" fill-opacity=".9"/><circle cx="25.5" cy="42" r="6.2" fill="#101722" stroke="#F0FAFC" stroke-width="2.6"/><circle cx="68.4" cy="42" r="6.2" fill="#101722" stroke="#F0FAFC" stroke-width="2.6"/><path d="M81 30h8" stroke="#FFF" stroke-opacity=".65" stroke-width="2" stroke-linecap="round"/></svg>';

  function createLane(player, index) {
    const lane = document.createElement('article');
    lane.className = 'lane';
    lane.dataset.player = playerKey(player);
    lane.innerHTML = '<div class="rank-badge"></div><div class="player-info"><span class="player-crown" aria-hidden="true">♛</span><span class="player-name" dir="auto"></span></div><div class="race-rail"><div class="rail-fill"></div><div class="finish-mark" aria-hidden="true"></div><div class="vehicle">' + carSvg + '</div></div><span class="near-label">قربت!</span><span class="boost-flare">BOOST!</span>';
    lane.style.setProperty('--player-color', safeColor(player.color, index));
    return lane;
  }

  function showToast(message) {
    toastEl.textContent = message;
    toastEl.classList.remove('show');
    void toastEl.offsetWidth;
    toastEl.classList.add('show');
    clearTimeout(state.toastTimer);
    state.toastTimer = setTimeout(() => toastEl.classList.remove('show'), 1400);
  }

  function renderPlayers(snapshot) {
    let players = Array.isArray(snapshot.players) ? snapshot.players.slice() : [];
    if (snapshot.phase === 'finished') players.sort((a, b) => (Number(a.rank) || 99) - (Number(b.rank) || 99));
    const visibleLimit = 12;
    const visible = players.slice(0, visibleLimit);
    const before = new Map();
    for (const node of lanesEl.children) before.set(node.dataset.player, node.getBoundingClientRect());
    const nextNodes = new Map();
    const previousPlayers = state.previous ? new Map((state.previous.players || []).map((p) => [playerKey(p), p])) : null;
    const previousKeys = state.previous ? new Set((state.previous.players || []).map(playerKey)) : null;
    let boostToast = '';

    visible.forEach((player, index) => {
      const key = playerKey(player);
      const prior = previousPlayers && previousPlayers.get(key);
      let node = state.nodes.get(key);
      if (!node) {
        node = createLane(player, index);
        node.classList.add('lane-enter');
        requestAnimationFrame(() => node.classList.remove('lane-enter'));
        if (state.previous && !previousKeys.has(key)) showToast(displayName(player) + ' دخل السباق');
      }
      const rank = Number(player.rank) || index + 1;
      const progress = Math.max(0, Math.min(1, Number(player.progress) || 0));
      const boost = Math.max(0, Number(player.boost) || 0);
      const color = safeColor(player.color, index);
      node.dataset.rank = String(rank);
      node.style.setProperty('--player-color', color);
      node.classList.toggle('is-leader', index === 0 && snapshot.phase === 'racing');
      node.classList.toggle('is-winner', snapshot.phase === 'finished' && rank <= 3);
      node.classList.toggle('is-near', snapshot.phase === 'racing' && index === 0 && progress >= .95);
      node.classList.toggle('near-finish', snapshot.phase === 'racing' && index === 0 && progress >= .85);
      node.querySelector('.rank-badge').textContent = '#' + String(rank);
      node.querySelector('.player-name').textContent = displayName(player);
      node.querySelector('.rail-fill').style.width = (progress * 100) + '%';
      node.querySelector('.vehicle').style.left = (progress * 96) + '%';
      const boostDelta = prior ? boost - (Number(prior.boost) || 0) : 0;
      if (boostDelta > .012 && snapshot.phase === 'racing') {
        node.classList.remove('boost-event','boost-major');
        void node.offsetWidth;
        node.classList.add('boost-event');
        node.classList.toggle('boost-major', boostDelta >= .1);
        node.querySelector('.boost-flare').textContent = boostDelta >= .14 ? 'SUPER BOOST' : boostDelta >= .065 ? 'BOOST!' : '+ دفعة';
        setTimeout(() => node.classList.remove('boost-event','boost-major'), 900);
        if (!boostToast) boostToast = displayName(player) + (boostDelta >= .14 ? ' · SUPER BOOST!' : ' · BOOST!');
      }
      lanesEl.appendChild(node);
      nextNodes.set(key, node);
    });
    state.nodes = nextNodes;
    // Remove stale nodes that were not included in the visible 12.
    for (const node of [...lanesEl.children]) if (!nextNodes.has(node.dataset.player)) node.remove();

    for (const node of lanesEl.children) {
      const oldRect = before.get(node.dataset.player);
      if (!oldRect) continue;
      const newRect = node.getBoundingClientRect();
      const deltaY = oldRect.top - newRect.top;
      if (Math.abs(deltaY) > 2) {
        node.style.transition = 'none';
        node.style.transform = 'translate3d(0,' + deltaY + 'px,0)';
        requestAnimationFrame(() => requestAnimationFrame(() => {
          node.style.transition = '';
          node.style.transform = '';
        }));
      }
    }
    const hiddenCount = Math.max(0, (Number(snapshot.player_count) || players.length) - visible.length);
    const packNote = $('packNote');
    packNote.hidden = hiddenCount < 1;
    if (hiddenCount > 0) packNote.textContent = '+ ' + hiddenCount + ' متسابق';
    $('emptyRace').hidden = players.length > 0;

    const leaderKey = visible[0] ? playerKey(visible[0]) : null;
    if (state.previous && state.previous.phase === 'racing' && state.lastLeader && leaderKey && leaderKey !== state.lastLeader && snapshot.phase === 'racing') {
      showToast('صدارة جديدة · ' + displayName(visible[0]));
    } else if (boostToast && snapshot.phase === 'racing') {
      showToast(boostToast);
    }
    state.lastLeader = leaderKey;
  }

  function renderLeaderboard(rows) {
    const safeRows = (Array.isArray(rows) ? rows : []).slice(0, 5);
    const html = safeRows.length ? safeRows.map((row, index) => '<div class="lbrow"><span class="lb-rank">' + (index + 1) + '</span><span class="lb-name" dir="auto" title="' + esc(row.name || '') + '">' + esc(row.name || 'متسابق') + '</span><span class="lb-points">' + (Number(row.points) || 0) + ' نقطة</span></div>').join('') : '<div class="empty-board">السباق الأول يكتب الترتيب</div>';
    $('leaderboardSide').innerHTML = html;
    $('leaderboardMobile').innerHTML = html;
  }

  function renderPodium(winners) {
    const podium = $('podium');
    const order = [{rank:2, medal:'🥈', cls:'second'}, {rank:1, medal:'🥇', cls:'first'}, {rank:3, medal:'🥉', cls:'third'}];
    const winnerMap = new Map((Array.isArray(winners) ? winners : []).map((winner) => [Number(winner.rank), winner]));
    podium.innerHTML = order.filter((item) => winnerMap.has(item.rank)).map((item) => {
      const winner = winnerMap.get(item.rank);
      return '<div class="pod ' + item.cls + '"><span class="pod-medal">' + item.medal + '</span><span class="pod-rank">المركز ' + item.rank + '</span><span class="pod-name" dir="auto" title="' + esc(winner.name || '') + '">' + esc(winner.name || 'متسابق') + '</span></div>';
    }).join('');
  }

  function renderCountdown(snapshot) {
    const overlay = $('phaseOverlay');
    const active = snapshot.phase === 'countdown';
    overlay.hidden = !active;
    if (!active) { state.lastCountdown = null; return; }
    const remaining = Number(snapshot.remaining);
    const token = remaining <= 1 ? 'انطلق!' : String(Math.min(3, Math.max(1, remaining)));
    $('countdownCaption').textContent = token === 'انطلق!' ? 'اضغطوا على السرعة' : (remaining > 3 ? 'استعدوا للانطلاق' : 'استعدوا');
    const value = $('countdownValue');
    value.classList.toggle('go', token === 'انطلق!');
    if (token !== state.lastCountdown) {
      value.textContent = token;
      value.classList.remove('count-pop');
      void value.offsetWidth;
      value.classList.add('count-pop');
      state.lastCountdown = token;
    }
  }

  function renderTips(snapshot, leader) {
    const queueCount = Number(snapshot.waiting_next) || 0;
    $('queue').hidden = queueCount < 1;
    $('queueCount').textContent = String(queueCount);
    let symbol = '♥';
    let message;
    if (snapshot.phase === 'finished') {
      symbol = '↻'; message = 'الجولة التالية تبدأ بعد ' + (Number(snapshot.remaining) || 1) + ' ث';
    } else if (snapshot.phase === 'countdown') {
      symbol = '↗'; message = 'استعدوا… الانطلاقة قربت';
    } else if (snapshot.phase === 'waiting' && Number(snapshot.player_count) < 2) {
      symbol = '🏁'; message = Number(snapshot.player_count) === 1 ? 'متسابق آخر وتبدأ الجولة' : 'ادخل البث وتدخل السباق تلقائيًا';
    } else if (snapshot.phase === 'racing' && queueCount > 0) {
      symbol = '↻'; message = 'دخلت أثناء السباق؟ سيارتك تنتظر الجولة التالية';
    } else if (snapshot.phase === 'racing' && leader && Number(leader.progress) >= .85) {
      symbol = '⚡'; message = 'الحسم اقترب — مين يوصل أول؟';
    } else if (snapshot.phase === 'waiting') {
      symbol = '◷'; message = 'الانطلاق بعد ' + (Number(snapshot.remaining) || 0) + ' ث';
    } else {
      const tips = [
        {symbol:'♥',text:'اللايك يعطي سيارتك دفعة خفيفة'},
        {symbol:'↗',text:'المتابعة تعطي Boost للمتسابق'},
        {symbol:'✦',text:'الهدية تعطي دفعة أقوى'},
      {symbol:'🏁',text:'الأول 3 نقاط · الثاني 2 · الثالث 1'}
      ];
      const tip = tips[Math.floor(Date.now() / 8000) % tips.length];
      symbol = tip.symbol; message = tip.text;
    }
    const key = symbol + '|' + message;
    if (key !== state.tipKey) {
      $('tipSymbol').textContent = symbol;
      $('tipText').textContent = message;
      state.tipKey = key;
    }
  }

  function celebrateWinner(winnerKey) {
    const field = $('trackField');
    field.classList.add('winner-freeze');
    setTimeout(() => field.classList.remove('winner-freeze'), 260);
    field.querySelectorAll('.confetti').forEach((bit) => bit.remove());
    const colors = ['#ffd36c','#55d9f2','#a78bfa','#f58c7c','#7bd9a5'];
    for (let i = 0; i < 14; i++) {
      const bit = document.createElement('span');
      bit.className = 'confetti';
      bit.style.setProperty('--confetti', colors[i % colors.length]);
      bit.style.setProperty('--delay', (i * 23) + 'ms');
      bit.style.setProperty('--dx', ((i % 7) - 3) * 14 + 'px');
      bit.style.left = (38 + (i % 8) * 4) + '%';
      field.appendChild(bit);
      setTimeout(() => bit.remove(), 1700);
    }
    state.finishKey = winnerKey;
  }

  function draw(snapshot) {
    $('round').textContent = 'الجولة ' + (Number(snapshot.round) || 1);
    $('count').textContent = (Number(snapshot.player_count) || 0) + ' / ' + (Number(snapshot.max_players) || 24);
    const phaseNames = {waiting:'المضمار جاهز',countdown:'استعدوا للانطلاق',racing:'السباق مباشر',finished:'نتيجة الجولة'};
    $('phaseText').textContent = phaseNames[snapshot.phase] || 'السباق مباشر';
    $('phaseLabel').dataset.phase = snapshot.phase || 'waiting';
    const players = Array.isArray(snapshot.players) ? snapshot.players : [];
    const leader = snapshot.phase === 'finished' && Array.isArray(snapshot.winners) && snapshot.winners.length ? snapshot.winners[0] : players[0];
    if (snapshot.phase === 'waiting') $('phaseDetail').textContent = Number(snapshot.player_count) < 2 ? 'السباق يبدأ تلقائيًا عند اكتمال المتسابقين' : 'الانطلاق بعد ' + (Number(snapshot.remaining) || 0) + ' ث';
    else if (snapshot.phase === 'countdown') $('phaseDetail').textContent = 'الجولة ' + (Number(snapshot.round) || 1) + ' · ' + (Number(snapshot.player_count) || 0) + ' متسابق';
    else if (snapshot.phase === 'racing') $('phaseDetail').textContent = leader ? 'المتصدر: ' + displayName(leader) : 'كل متر يفرق';
    else $('phaseDetail').textContent = 'الجولة التالية بعد ' + (Number(snapshot.remaining) || 1) + ' ث';

    const live = String(snapshot.live || 'RECONNECTING').toUpperCase();
    const isLive = live === 'LIVE' || Boolean(snapshot.room_id);
    const connecting = live === 'CONNECTING';
    const offline = live === 'OFFLINE';
    liveEl.dataset.status = isLive ? 'connected' : connecting ? 'connecting' : offline ? 'offline' : 'reconnecting';
    liveEl.querySelector('.live-word').textContent = isLive ? 'LIVE' : connecting ? 'اتصال' : offline ? 'غير متصل' : 'إعادة اتصال';
    liveEl.title = snapshot.last_error || (isLive ? 'TikTok LIVE متصل' : 'حالة الاتصال: ' + live);
    $('subtitle').textContent = snapshot.last_error ? 'جاري استعادة الاتصال بالبث' : 'ادخل البث… تدخل السباق تلقائيًا';

    renderPlayers(snapshot);
    renderLeaderboard(snapshot.leaderboard);
    renderCountdown(snapshot);
    renderTips(snapshot, leader);
    const results = snapshot.phase === 'finished';
    $('resultsDeck').classList.toggle('active', results);
    $('mobile-board').hidden = results;
    $('winnerMoment').hidden = !results || !(snapshot.winners || []).length;
    if (results && (snapshot.winners || []).length) {
      const winner = snapshot.winners[0];
      $('winnerName').textContent = winner.name || winner.username || 'متسابق';
      renderPodium(snapshot.winners);
      $('nextRound').textContent = 'الجولة التالية بعد ' + (Number(snapshot.remaining) || 1) + ' ث';
      const winnerKey = String(snapshot.round) + ':' + String(winner.username || winner.name || 'winner');
      if (state.finishKey !== winnerKey) celebrateWinner(winnerKey);
    } else {
      $('mobile-board').hidden = false;
      $('resultsDeck').classList.remove('active');
      state.finishKey = '';
    }

    if (state.previous && (Number(snapshot.waiting_next) || 0) > (Number(state.previous.waiting_next) || 0) && snapshot.phase === 'racing') {
      showToast('متسابق جديد ينتظر الجولة التالية');
    }
    if (state.lastPhase !== snapshot.phase && snapshot.phase === 'racing') {
      $('phaseLabel').dataset.phase = 'racing';
    }
    state.lastPhase = snapshot.phase;
    state.previous = snapshot;
  }

  async function poll() {
    try {
      const response = await fetch('/api/state', {cache:'no-store',headers:{'Accept':'application/json'}});
      if (!response.ok) throw new Error('state unavailable');
      draw(await response.json());
    } catch (_) {
      liveEl.dataset.status = 'reconnecting';
      liveEl.querySelector('.live-word').textContent = 'إعادة اتصال';
      liveEl.title = 'تعذر الوصول إلى خادم اللعبة';
      $('subtitle').textContent = 'جاري استعادة الاتصال باللعبة';
    }
    setTimeout(poll, 450);
  }
  poll();
})();
</script>
</body>
</html>"""

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
