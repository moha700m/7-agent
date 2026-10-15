import os
import time
import math
import random
import asyncio
import threading
from flask import Flask, jsonify, request
from TikTokLive import TikTokLiveClient
from TikTokLive.events import ConnectEvent, DisconnectEvent, CommentEvent, JoinEvent, GiftEvent, FollowEvent, LikeEvent

app = Flask(__name__)

USERNAME = os.getenv("TIKTOK_USERNAME", "mgz7o2").lstrip("@")
CONTROL_TOKEN = os.getenv("CONTROL_TOKEN", "")
MAX_PLAYERS = int(os.getenv("MAX_PLAYERS", "24"))
WAIT_SECONDS = float(os.getenv("WAIT_SECONDS", "18"))
COUNTDOWN_SECONDS = float(os.getenv("COUNTDOWN_SECONDS", "5"))
RACE_SECONDS = float(os.getenv("RACE_SECONDS", "22"))
RESULT_SECONDS = float(os.getenv("RESULT_SECONDS", "8"))

COLORS = ["#ff5a5f","#4dabf7","#ffd43b","#69db7c","#b197fc","#ffa94d","#f06595","#63e6be","#74c0fc","#e599f7","#ff8787","#8ce99a"]

class RaceEngine:
    def __init__(self):
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.players = {}
        self.waiting_next = {}
        self.phase = "waiting"
        self.round = 1
        self.phase_started = time.time()
        self.live = "CONNECTING"
        self.room_id = None
        self.last_error = ""
        self.reconnects = 0
        self.event_count = 0
        self.winners = []
        self.client = None
        self.client_loop = None
        threading.Thread(target=self._game_loop, daemon=True).start()
        threading.Thread(target=self._tiktok_loop, daemon=True).start()

    def _now(self):
        return time.time()

    def _clean_name(self, name, username):
        name = str(name or username or "لاعب").strip()
        return name[:24] or "لاعب"

    def _new_player(self, username, name):
        seed = hash((username, self.round)) & 0xffffffff
        rng = random.Random(seed)
        return {
            "username": username,
            "name": self._clean_name(name, username),
            "progress": 0.0,
            "speed": rng.uniform(.92, 1.08),
            "boost": 0.0,
            "color": COLORS[len(self.players) % len(COLORS)],
            "joined_at": self._now(),
            "finished_at": None,
            "rank": None,
            "events": 0,
        }

    def join(self, username, name):
        username = str(username or "").strip()[:80] or f"guest-{self.event_count}"
        with self.lock:
            self.event_count += 1
            if username in self.players:
                self.players[username]["name"] = self._clean_name(name, username)
                return
            if username in self.waiting_next:
                return
            if self.phase in ("waiting", "countdown") and len(self.players) < MAX_PLAYERS:
                self.players[username] = self._new_player(username, name)
            else:
                self.waiting_next[username] = self._clean_name(name, username)

    def boost(self, username, amount):
        with self.lock:
            p = self.players.get(str(username))
            if not p:
                return
            p["boost"] = min(.32, p["boost"] + max(0.0, float(amount)))
            p["events"] += 1

    def _start_countdown(self):
        if len(self.players) < 2:
            return
        self.phase = "countdown"
        self.phase_started = self._now()

    def force_start(self):
        with self.lock:
            if len(self.players) >= 2:
                self._start_countdown()
                return True
            return False

    def reset(self):
        with self.lock:
            self.players.clear()
            self.waiting_next.clear()
            self.winners = []
            self.phase = "waiting"
            self.phase_started = self._now()
            self.round += 1

    def _begin_race(self):
        self.phase = "racing"
        self.phase_started = self._now()
        self.winners = []
        for i, p in enumerate(self.players.values()):
            p["progress"] = 0.0
            p["finished_at"] = None
            p["rank"] = None
            p["color"] = COLORS[i % len(COLORS)]

    def _finish_race(self):
        ranked = sorted(self.players.values(), key=lambda p: (p["finished_at"] is not None, p["progress"]), reverse=True)
        for i, p in enumerate(ranked, 1):
            p["rank"] = i
        self.winners = [{"rank": p["rank"], "name": p["name"], "username": p["username"]} for p in ranked[:3]]
        self.phase = "finished"
        self.phase_started = self._now()

    def _next_round(self):
        queued = list(self.waiting_next.items())
        self.players = {}
        self.waiting_next = {}
        self.winners = []
        self.round += 1
        self.phase = "waiting"
        self.phase_started = self._now()
        for username, name in queued[:MAX_PLAYERS]:
            self.players[username] = self._new_player(username, name)

    def _tick(self, dt):
        now = self._now()
        if self.phase == "waiting":
            if len(self.players) >= 2 and now - self.phase_started >= WAIT_SECONDS:
                self._start_countdown()
            return
        if self.phase == "countdown":
            if now - self.phase_started >= COUNTDOWN_SECONDS:
                self._begin_race()
            return
        if self.phase == "racing":
            for p in self.players.values():
                if p["finished_at"] is not None:
                    continue
                jitter = random.uniform(.985, 1.015)
                boost = p["boost"]
                p["boost"] = max(0.0, boost - dt * .055)
                step = dt / RACE_SECONDS * p["speed"] * jitter * (1.0 + boost)
                p["progress"] = min(1.0, p["progress"] + step)
                if p["progress"] >= 1.0:
                    p["finished_at"] = now
            finished = sum(1 for p in self.players.values() if p["finished_at"] is not None)
            if finished == len(self.players) or now - self.phase_started > RACE_SECONDS * 1.35:
                self._finish_race()
            return
        if self.phase == "finished" and now - self.phase_started >= RESULT_SECONDS:
            self._next_round()

    def _game_loop(self):
        last = self._now()
        while not self.stop.wait(.1):
            now = self._now()
            dt = min(.25, now - last)
            last = now
            with self.lock:
                self._tick(dt)

    def state(self):
        with self.lock:
            now = self._now()
            players = sorted(self.players.values(), key=lambda p: (p["progress"], -p["joined_at"]), reverse=True)
            if self.phase == "countdown":
                remaining = max(0, math.ceil(COUNTDOWN_SECONDS - (now - self.phase_started)))
            elif self.phase == "waiting":
                remaining = max(0, math.ceil(WAIT_SECONDS - (now - self.phase_started))) if len(players) >= 2 else None
            elif self.phase == "racing":
                remaining = max(0, math.ceil(RACE_SECONDS * 1.35 - (now - self.phase_started)))
            else:
                remaining = max(0, math.ceil(RESULT_SECONDS - (now - self.phase_started)))
            return {
                "ok": True,
                "username": USERNAME,
                "live": self.live,
                "room_id": self.room_id,
                "phase": self.phase,
                "round": self.round,
                "remaining": remaining,
                "players": players,
                "player_count": len(players),
                "waiting_next": len(self.waiting_next),
                "winners": self.winners,
                "max_players": MAX_PLAYERS,
                "last_error": self.last_error,
                "reconnects": self.reconnects,
                "event_count": self.event_count,
                "server_time": now,
            }

    def _tiktok_loop(self):
        attempt = 0
        while not self.stop.is_set():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            client = None
            try:
                self.live = "CONNECTING"
                client = TikTokLiveClient(unique_id=f"@{USERNAME}")
                self.client, self.client_loop = client, loop

                def fields(event):
                    user = getattr(event, "user", None)
                    username = str(getattr(user, "unique_id", "") or getattr(user, "id", "") or "anonymous")
                    name = str(getattr(user, "nickname", "") or username)
                    return username, name

                @client.on(ConnectEvent)
                async def connected(_):
                    nonlocal attempt
                    attempt = 0
                    with self.lock:
                        self.live = "LIVE"
                        self.room_id = client.room_id
                        self.last_error = ""

                @client.on(DisconnectEvent)
                async def disconnected(_):
                    with self.lock:
                        self.live = "RECONNECTING"

                @client.on(JoinEvent)
                async def joined(event):
                    username, name = fields(event)
                    self.join(username, name)

                @client.on(CommentEvent)
                async def commented(event):
                    username, name = fields(event)
                    text = str(getattr(event, "comment", "") or "").strip().lower()
                    if text in {"دخول","ادخل","لعب","العب","join","play"}:
                        self.join(username, name)

                @client.on(FollowEvent)
                async def followed(event):
                    username, name = fields(event)
                    self.join(username, name)
                    self.boost(username, .10)

                @client.on(LikeEvent)
                async def liked(event):
                    username, name = fields(event)
                    self.join(username, name)
                    count = max(1, int(getattr(event, "count", 1) or 1))
                    self.boost(username, min(.06, count * .002))

                @client.on(GiftEvent)
                async def gifted(event):
                    gift = getattr(event, "gift", None)
                    if getattr(gift, "streakable", False) and getattr(event, "streaking", False):
                        return
                    username, name = fields(event)
                    self.join(username, name)
                    count = max(1, int(getattr(event, "repeat_count", 1) or 1))
                    diamonds = max(0, int(getattr(gift, "diamond_count", 0) or 0)) * count
                    self.boost(username, min(.24, .04 + math.log10(max(1, diamonds)) * .055))

                async def run():
                    socket_task = await client.start(process_connect_events=False)
                    await socket_task

                loop.run_until_complete(run())
            except asyncio.CancelledError:
                pass
            except Exception:
                with self.lock:
                    self.last_error = "تعذر اتصال TikTok؛ إعادة المحاولة تلقائيًا"
                    self.live = "RECONNECTING"
                    self.reconnects += 1
            finally:
                if client:
                    try:
                        loop.run_until_complete(asyncio.wait_for(client.disconnect(close_client=True), 3))
                    except Exception:
                        pass
                for task in asyncio.all_tasks(loop):
                    task.cancel()
                try:
                    loop.run_until_complete(asyncio.gather(*asyncio.all_tasks(loop), return_exceptions=True))
                except Exception:
                    pass
                loop.close()
                self.client = self.client_loop = None
                self.room_id = None
            if self.stop.is_set():
                break
            attempt += 1
            self.stop.wait(min(30, 2 ** min(attempt, 5)))
        self.live = "OFFLINE"

ENGINE = RaceEngine()

def authorized():
    if not CONTROL_TOKEN:
        return True
    return request.headers.get("X-Control-Token", "") == CONTROL_TOKEN

@app.get("/")
def root():
    return jsonify(ok=True, service="TikTok Race Game", username=USERNAME)

@app.get("/health")
def health():
    state = ENGINE.state()
    return jsonify(ok=True, live=state["live"], phase=state["phase"], players=state["player_count"], last_error=state["last_error"])

@app.get("/api/state")
def state():
    return jsonify(ENGINE.state())

@app.post("/api/start")
def start_race():
    if not authorized():
        return jsonify(error="unauthorized"), 401
    return jsonify(ok=ENGINE.force_start(), **ENGINE.state())

@app.post("/api/reset")
def reset_race():
    if not authorized():
        return jsonify(error="unauthorized"), 401
    ENGINE.reset()
    return jsonify(ENGINE.state())

@app.post("/api/simulate")
def simulate():
    if not authorized():
        return jsonify(error="unauthorized"), 401
    body = request.get_json(silent=True) or {}
    count = max(1, min(24, int(body.get("count", 8))))
    for i in range(count):
        ENGINE.join(f"demo{i+1}", f"لاعب {i+1}")
    return jsonify(ENGINE.state())

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
