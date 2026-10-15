import asyncio
import copy
import io
import base64
import secrets
import threading
import time
import uuid
import wave
from collections import deque

from config import build_config
from memory import ViewerMemoryManager, safe_text
from metrics import MetricsManager
from intelligence import CommentClassifier, SemanticDeduplicator, PriorityEngine, normalize
from queue_manager import EventQueue
from behavior import CrowdEngine, MoodEngine, VoiceStyleEngine, AutoPersonality, ProactiveHost, PROFILES
from providers import ProviderManager
from tiktok_client import run_client


class HostRuntime:
    def __init__(self):
        self.lock = threading.RLock()
        self.lifecycle = threading.RLock()
        self.cfg = build_config({})
        self.config_version = 0
        self.generation = 0
        self.audio_epoch = 0
        self.boot_id = uuid.uuid4().hex
        self.running = False
        self.simulation = False
        self.fixture = False
        self.paused = self.muted = False
        self.volume = 1
        self.tiktok = "OFFLINE"
        self.room_id = None
        self.username = ""
        self.last_error = ""
        self.last_event = self.last_spoken = time.time()
        self.last_join = 0
        self.like_bucket = 0
        self.reconnect_attempts = 0
        self.events = deque(maxlen=600)
        self.audio = deque(maxlen=12)
        self.event_seq = 0
        self.queue = EventQueue()
        self.memory = ViewerMemoryManager()
        self.metrics = MetricsManager()
        self.classifier = CommentClassifier()
        self.dedupe = SemanticDeduplicator()
        self.priority = PriorityEngine()
        self.crowd_engine, self.mood = CrowdEngine(), MoodEngine()
        self.voice = VoiceStyleEngine()
        self.auto, self.proactive = AutoPersonality(), ProactiveHost()
        self.providers = ProviderManager(self.metrics)
        self.stop_event = threading.Event()
        self.responder_thread = self.tiktok_thread = None
        self.client = self.client_loop = self.connection_task = None
        self.responder_heartbeat = 0
        self.player = dict(id="", heartbeat=0, busy=False, queue_size=0, current_id=None)
        self.pending_audio = None
        self.recent_replies = deque(maxlen=8)
        self.viewer_rate = {}

    def publish(self, kind, message, **fields):
        with self.lock:
            self.event_seq += 1
            item = dict(id=self.event_seq, ts=time.strftime("%H:%M:%S"), timestamp=time.time(), kind=kind,
                message=safe_text(message), **fields)
            self.events.append(item)
            return item

    def decision(self, event, decision, reason="", reply="", latency=None):
        return self.publish("inspector", event.get("text", ""), event_id=event["event_id"],
            event_type=event["type"], username=event.get("username",""), name=event.get("name",""),
            text=safe_text(event.get("text","")), classification=event.get("classification",{}),
            priority=event.get("priority_score",0), decision=decision, ignored_reason=reason,
            ai_reply=safe_text(reply), latency=latency, timings=dict(event.get("timings",{})))

    def crowd(self):
        return self.crowd_engine.evaluate(self.metrics.snapshot(),self.queue.size(),self.cfg["crowd_enabled"],self.cfg["topic_profile"])

    def configure(self, data):
        with self.lock:
            merged = copy.deepcopy(self.cfg)
            changed = data.get("preset_id") and data["preset_id"] != merged["preset_id"]
            if changed:
                for key in ("persona","behavior","tts_voice","voice_gender","tts_prefix"):
                    merged.pop(key,None)
            for key, value in data.items():
                if key in ("persona","behavior") and isinstance(value,dict):
                    merged[key] = {**merged.get(key,{}),**value}
                elif key not in ("openrouter_key","vibi_key") or value:
                    merged[key] = value
            self.cfg = build_config(merged)
            self.memory.ttl = self.cfg["memory_ttl_hours"]*3600
            self.config_version += 1
            self.audio_epoch += 1
            self.audio.clear()
            self.pending_audio = None
            if changed:
                self.auto.last_switch = time.time()
            if self.cfg.get("allow_profile_persona") and self.cfg["personality_mode"]=="manual" and "topic_profile" in data and not data.get("preset_id"):
                self.cfg = build_config({**self.cfg,"preset_id":PROFILES[self.cfg["topic_profile"]]["recommended"],"persona":{},"behavior":{},"tts_voice":""})
            self.publish("status",f'الشخصية: {self.cfg["persona"]["name"]} | الصوت: {self.cfg["tts_voice"]}')
            return self.status()

    def ingest(self, data):
        with self.lock:
            if not self.running:
                raise ValueError("شغّل الوكيل أو المحاكاة أولًا")
            now = time.time()
            event = dict(type=data.get("type","comment"), username=str(data.get("username") or data.get("name") or "sim_viewer")[:80],
                name=safe_text(data.get("name") or data.get("username") or "مشاهد")[:80], text=safe_text(data.get("text","")),
                received_at=now,event_id=uuid.uuid4().hex, timings={"event_received":now})
            for key in ("gift_count","gift_value","count"):
                event[key] = max(0,min(100000,int(data.get(key,1 if key!="gift_value" else 0))))
            event["streak_finished"] = bool(data.get("streak_finished"))
            kind, user = event["type"], event["username"]
            if kind not in (*EventQueue.TTL.keys(),):
                raise ValueError("نوع حدث غير مدعوم")
            self.last_event = now
            counter = dict(comment="comments_received",join="joins_received",gift="gifts_received",follow="follows",share="shares",like="likes").get(kind)
            if counter:
                self.metrics.inc(counter,event["count"] if kind=="like" else 1)
            context = self.memory.get_user_context(user)
            event["context"] = context
            if kind=="comment":
                event["classification"] = self.classifier.classify(str(data.get("text", ""))[:1000],context,self.cfg["persona"].get("name"),self.cfg["persona"].get("owner_name"))
                recent=self.viewer_rate.setdefault(user,deque(maxlen=12))
                recent.append(now)
                while recent and now-recent[0]>10: recent.popleft()
                if len(recent)>8:
                    event["classification"].update(category="spam",should_reply=False,priority=0,reason="viewer rate limit")
                if len(self.viewer_rate)>5000:
                    self.viewer_rate={k:v for k,v in self.viewer_rate.items() if v and now-v[-1]<60}
                event["direct_mention"]=normalize(self.cfg["persona"].get("name","")) in normalize(event["text"])
                self.memory.add_user_message(user,event["text"],event["name"],self.cfg["persona"].get("game_or_topic",""))
            else:
                event["classification"] = dict(category=kind,priority=50,should_reply=True,needs_context=False,tone="friendly",reason="live interaction")
            if kind=="join":
                self.memory.mark_join(user,event["name"])
            elif kind=="gift":
                self.memory.mark_gift(user,event["name"],event["gift_count"])
            event["priority_score"] = self.priority.score(event)
            event["timings"]["classification_done"] = time.time()
            decision, reason = self.dedupe.decide(event["text"],kind,user,data.get("source_id"))
            if decision!="allow":
                self.metrics.inc("deduped_messages")
                if kind=="comment":
                    event["classification"]["category"] = "repeated_message"
                self.skip(event,reason, "MERGED" if decision=="merge" else "SUPPRESSED")
                return event
            if not event["classification"]["should_reply"]:
                if event["classification"]["category"] in ("spam","toxic","troll"):
                    self.metrics.inc("suppressed_spam")
                self.skip(event,event["classification"]["reason"])
                return event
            if kind=="like":
                self.like_bucket += event["count"]
                if self.like_bucket < self.cfg["behavior"]["like_threshold"]:
                    self.skip(event,"like aggregation")
                    return event
                event["text"] = str(self.like_bucket)
                self.like_bucket=0
            self.mood.update(event,self.crowd(),self.cfg)
            reason = self.reply_policy(event)
            if reason:
                self.skip(event,reason)
                return event
            dropped = self.queue.put(event)
            if dropped:
                self.metrics.inc("queue_dropped")
                self.skip(dropped,"queue selection: higher value interaction retained")
            if dropped is not event:
                self.decision(event,"QUEUED")
                self.metrics.peak(self.queue.size())
            return event

    def skip(self, event, reason, decision="SUPPRESSED"):
        if event["type"]=="comment":
            self.metrics.inc("comments_skipped")
        if reason=="expired":
            self.metrics.inc("expired")
        self.decision(event,decision,reason)

    def reply_policy(self, event):
        kind=event["type"]
        b=self.cfg["behavior"]
        if kind not in ("manual","proactive") and not b.get("respond_"+dict(comment="comments",join="joins",gift="gifts",follow="follows",share="shares",like="likes")[kind],True):
            return "event disabled"
        crowd=self.crowd()
        if kind=="join":
            if crowd["state"] in ("Busy","Packed"):
                return "crowd mode: skip join"
            if event.get("context",{}).get("welcomed_recently") or time.time()-self.last_join < b["join_cooldown"]:
                return "welcome cooldown"
        if kind=="comment" and event["priority_score"] < crowd["min_priority"]:
            return "crowd mode: selected higher priority interaction"
        return ""

    def player_idle(self):
        return (time.time()-self.player["heartbeat"] < 10 and not self.player["busy"] and not self.player["queue_size"] and self.pending_audio is None)

    def worker(self, stop, generation):
        while not stop.wait(.15):
            with self.lock:
                self.responder_heartbeat=time.time()
                self.memory.cleanup_expired()
                self.mood.update(None,self.crowd(),self.cfg,idle=time.time()-self.last_event)
                if self.paused or self.muted:
                    continue
                if self.pending_audio and time.time()-self.pending_audio[1] > 65:
                    self.metrics.inc("audio_failed")
                    self.pending_audio=None
                # Only generate one outstanding clip; the player is the sole audio sink.
                if self.pending_audio or self.player["busy"] and time.time()-self.player["heartbeat"]<10:
                    continue
                target=self.auto.choose(self.cfg,self.mood.current,self.crowd())
                if target:
                    self.configure({"preset_id":target})
                cooldown=self.cfg["behavior"]["global_cooldown"]*self.crowd()["cooldown"]
                if time.time()-self.last_spoken < cooldown:
                    continue
                event, expired=self.queue.take()
                for old in expired:
                    self.skip(old,"expired","EXPIRED")
                if event is None:
                    line=self.proactive.next_line(self.cfg,time.time()-max(self.last_event,self.last_spoken),self.player_idle())
                    if line:
                        self.ingest(dict(type="proactive",username="host",text=line))
                    continue
                reason=self.reply_policy(event)
                if reason:
                    self.skip(event,reason)
                    continue
                cfg=copy.deepcopy(self.cfg)
                version=self.config_version
                cfg["persona"]["max_words"]=min(cfg["persona"]["max_words"],self.crowd()["max_words"])
                cfg["current_mood"]=self.mood.current
                cfg["voice_style"]=self.voice.build_tts_style(event,self.mood.current,cfg)
                event["recent_replies"]=list(self.recent_replies)
                if event["classification"].get("needs_context"):
                    event["context"]=self.memory.get_user_context(event["username"])
            try:
                event["timings"]["ai_started"]=time.time()
                if event["type"]=="join":
                    reply=("هلا {name} رجعت نورت" if event["context"].get("joins",0)>0 else "هلا والله يا {name} نورت").format(name=event["name"])
                elif event["type"] in ("proactive","manual"):
                    reply=event["text"]
                elif self.fixture:
                    reply="أنا ذكاء اصطناعي مع البث" if event["classification"]["category"]=="personal_question" else "يا هلا، وش رأيكم نكمل السالفة؟"
                else:
                    reply=self.providers.reply(event,cfg)
                event["timings"]["ai_finished"]=time.time()
                with self.lock:
                    if stop.is_set() or generation!=self.generation or version!=self.config_version or self.paused or self.muted or self.queue.expired(event):
                        self.skip(event,"cancelled or configuration changed before speech")
                        continue
                    if normalize(reply) in self.recent_replies:
                        self.skip(event,"repeated AI reply")
                        continue
                if self.fixture:
                    output=io.BytesIO()
                    with wave.open(output,"wb") as w:
                        w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000); w.writeframes(b"\0\0"*800)
                    b64,mime=base64.b64encode(output.getvalue()).decode(),"audio/wav"
                else:
                    b64,mime=self.providers.speak(reply,cfg)
                event["timings"]["tts_finished"]=time.time()
                with self.lock:
                    if stop.is_set() or generation!=self.generation or version!=self.config_version or self.paused or self.muted or self.queue.expired(event):
                        self.skip(event,"cancelled, expired or configuration changed during generation")
                        continue
                    event["timings"]["audio_ready"]=time.time()
                    latency=time.time()-event["received_at"]
                    self.metrics.latency(latency)
                    self.metrics.inc("replies")
                    if event["type"]=="comment": self.metrics.inc("comments_answered")
                    if event["type"]=="join":
                        self.metrics.inc("joins_spoken")
                        self.last_join=time.time()
                    self.memory.add_ai_reply(event["username"],reply,event["type"])
                    self.recent_replies.append(normalize(reply))
                    self.last_spoken=time.time()
                    self.decision(event,"REPLY",reply=reply,latency=round(latency,3))
                    row=self.publish("ai",reply,name=event["name"],character=cfg["persona"]["name"],voice=cfg["tts_voice"],voice_style=cfg["voice_style"])
                    self.audio.append(dict(id=row["id"],created_at=time.time(),audio_b64=b64,mime=mime,character=cfg["persona"]["name"],epoch=self.audio_epoch))
                    self.pending_audio=(row["id"],time.time())
            except Exception:
                with self.lock:
                    self.last_error="فشل توليد الرد أو الصوت؛ راجع حالة المزودين"
                    self.skip(event,self.last_error,"ERROR")
        self.responder_heartbeat=0

    def start(self, data, simulation=False, fixture=False):
        with self.lifecycle, self.lock:
            if self.running or any(t and t.is_alive() for t in (self.responder_thread,self.tiktok_thread)):
                raise ValueError("الوكيل يعمل أو ما زال يتوقف؛ لن يبدأ اتصال ثانٍ")
            self.configure(data)
            if not fixture and not self.cfg["openrouter_key"]:
                raise ValueError("مفتاح OpenRouter مطلوب")
            self.running=True
            self.simulation,self.fixture=simulation,fixture
            self.username=str(data.get("tiktok", ""))
            self.generation+=1
            self.audio_epoch+=1
            self.queue.clear(); self.audio.clear()
            self.dedupe=SemanticDeduplicator()
            self.recent_replies.clear()
            self.metrics.reset()
            self.last_event=self.last_spoken=time.time()
            self.last_join=0; self.like_bucket=0
            self.pending_audio=None
            self.paused=False; self.last_error=""; self.reconnect_attempts=0
            self.stop_event=threading.Event()
            self.tiktok="SIMULATION" if simulation else "CONNECTING"
            self.responder_thread=threading.Thread(target=self.worker,args=(self.stop_event,self.generation),daemon=True,name="responder")
            self.responder_thread.start()
            self.tiktok_thread=None
            if not simulation:
                self.tiktok_thread=threading.Thread(target=run_client,args=(self,self.username,self.stop_event),daemon=True,name="tiktok")
                self.tiktok_thread.start()
            return self.status()

    def stop(self):
        with self.lifecycle:
            with self.lock:
                self.running=False
                self.stop_event.set()
                self.generation+=1; self.audio_epoch+=1
                for e in self.queue.clear(): self.skip(e,"stopped")
                self.audio.clear(); self.pending_audio=None
                loop,task=self.client_loop,self.connection_task
                if loop and loop.is_running() and task:
                    loop.call_soon_threadsafe(task.cancel)
                threads=(self.tiktok_thread,self.responder_thread)
                self.tiktok="STOPPING"
            for thread in threads:
                if thread and thread is not threading.current_thread(): thread.join(timeout=4)
            with self.lock:
                if not any(t and t.is_alive() for t in threads): self.tiktok="OFFLINE"
                self.room_id=None
                return self.status()

    def heartbeat(self, data):
        with self.lock:
            now=time.time(); pid=str(data.get("player_id", ""))[:80]
            if not pid: raise ValueError("player_id مطلوب")
            owner=self.player["id"]
            if owner and owner!=pid and now-self.player["heartbeat"]<10:
                return dict(ok=True,active=False,epoch=self.audio_epoch,boot_id=self.boot_id)
            if "volume" in data: self.volume=max(0,min(1,float(data["volume"])))
            if "muted" in data: self.muted=bool(data["muted"])
            self.player=dict(id=pid,heartbeat=now,busy=bool(data.get("busy")),queue_size=max(0,min(20,int(data.get("queue_size",0)))),current_id=data.get("current_id"))
            if self.pending_audio and data.get("finished_id")==self.pending_audio[0] and data.get("boot_id",self.boot_id)==self.boot_id:
                if data.get("failed"): self.metrics.inc("audio_failed")
                self.audio=deque((row for row in self.audio if row["id"]!=self.pending_audio[0]),maxlen=12)
                self.pending_audio=None
            return dict(ok=True,active=True,epoch=self.audio_epoch,boot_id=self.boot_id,muted=self.muted,volume=self.volume,running=self.running)

    def status(self):
        with self.lock:
            metrics=self.metrics.snapshot()
            return dict(ok=True,running=self.running,tiktok=self.tiktok,room_id=self.room_id,username=self.username,
                agent_name=self.cfg["persona"]["name"],preset_id=self.cfg["preset_id"],preset_label=self.cfg["preset_label"],
                tts_voice=self.cfg["tts_voice"],voice_gender=self.cfg["voice_gender"],last_error=self.last_error,
                mood=self.mood.current,crowd_state=self.crowd()["state"],queue_size=self.queue.size(),
                current_ai_model=self.providers.health["current_ai_model"] or self.cfg["model"],
                current_tts_provider=self.providers.health["current_tts_provider"] or self.cfg["tts_provider"],
                reconnect_attempts=self.reconnect_attempts,metrics=metrics,muted=self.muted,paused=self.paused,
                personality_mode=self.cfg["personality_mode"],proactive_enabled=self.cfg["proactive_enabled"],
                crowd_enabled=self.cfg["crowd_enabled"],simulation=self.simulation,fixture=self.fixture,
                audio_epoch=self.audio_epoch,boot_id=self.boot_id,player_connected=time.time()-self.player["heartbeat"]<10,
                current_playing=self.player["current_id"],memory_users=len(self.memory.users))

    def health(self):
        with self.lock:
            alive=lambda t:bool(t and t.is_alive())
            return dict(ok=not self.running or alive(self.responder_thread),tiktok=self.tiktok.lower(),
                queue_size=self.queue.size(),queue="ok" if self.queue.size()<self.queue.maxsize else "full",
                responder_thread="ok" if alive(self.responder_thread) else "stopped",tiktok_thread="ok" if alive(self.tiktok_thread) else "stopped",
                responder_heartbeat_age=round(time.time()-self.responder_heartbeat,2) if self.responder_heartbeat else None,
                last_event_age=round(time.time()-self.last_event,2),reconnect_attempts=self.reconnect_attempts,
                player_connected=time.time()-self.player["heartbeat"]<10,**{**self.providers.health,"last_error":self.last_error or self.providers.health["last_error"]})
