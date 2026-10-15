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
from self_host import SelfHostEngine
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
        self.audio_streams = {}
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
        self.self_host = SelfHostEngine()
        self.providers = ProviderManager(self.metrics)
        self.stop_event = threading.Event()
        self.responder_thread = self.tiktok_thread = None
        self.client = self.client_loop = self.connection_task = None
        self.responder_heartbeat = 0
        self.player = dict(id="", heartbeat=0, busy=False, queue_size=0, current_id=None, current_since=0, pcm_streaming=False)
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
            previous_profile = merged["topic_profile"]
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
            self.audio_streams.clear()
            self.pending_audio = None
            if changed:
                self.auto.last_switch = time.time()
            if self.cfg.get("allow_profile_persona") and self.cfg["personality_mode"]=="manual" and self.cfg["topic_profile"]!=previous_profile and not changed:
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
            for key in ("self_host_action","self_host_topic_id","self_host_topic_title"):
                if key in data: event[key]=safe_text(data.get(key,""))[:160]
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
            linked=self.self_host.note_event(event,self.cfg) if kind not in ("self_host","proactive","manual") else False
            if linked:self.metrics.inc("viewer_responses_to_topics")
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
            # A real viewer interaction always outranks autonomous/proactive chatter.
            if kind in ("gift","follow","share","comment"):
                for old in self.queue.drop_types({"proactive","self_host"}):
                    self.skip(old,"interrupted by live viewer interaction","INTERRUPTED")
                    if old.get("type")=="self_host":self.metrics.inc("self_host_interruptions")
                self.self_host.interrupt()
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
        if kind not in ("manual","proactive","self_host") and not b.get("respond_"+dict(comment="comments",join="joins",gift="gifts",follow="follows",share="shares",like="likes")[kind],True):
            return "event disabled"
        crowd=self.crowd()
        if kind=="join":
            if crowd["state"] in ("Busy","Packed"):
                return "crowd mode: skip join"
            # Welcome new viewers quickly when the room is quiet/normal.
            # Keep the per-viewer memory guard so reconnect/rejoin spam is not repeated.
            fast_cap = 8 if crowd["state"]=="Quiet" else 14
            effective_join_cooldown = min(float(b["join_cooldown"]), fast_cap)
            if event.get("context",{}).get("welcomed_recently") or time.time()-self.last_join < effective_join_cooldown:
                return "welcome cooldown"
        if kind=="comment" and event["priority_score"] < crowd["min_priority"]:
            return "crowd mode: selected higher priority interaction"
        return ""

    def player_idle(self):
        return (time.time()-self.player["heartbeat"] < 10 and not self.player["busy"] and not self.player["queue_size"] and self.pending_audio is None)

    def fast_interaction_reply(self, event):
        """Human-sounding fast path for events that should never wait on an LLM."""
        name=event.get("name") or "يا الغالي"
        kind=event.get("type")
        if kind=="follow":
            return secrets.choice((
                f"كفو يا {name}، تسلم على المتابعة",
                f"يا هلا {name}، شكراً على المتابعة",
                f"نورت يا {name}، يعطيك العافية على المتابعة",
                f"حياك {name}، كفو على المتابعة",
            ))
        if kind=="gift":
            gift=(event.get("text") or "الهدية").strip()
            return secrets.choice((
                f"يا بعدي {name}، تسلم على {gift}",
                f"كفو يا {name}، شكراً على {gift}",
                f"يا سلام يا {name}، يعطيك العافية على {gift}",
                f"بيض الله وجهك يا {name}، تسلم على {gift}",
            ))
        if kind=="share":
            return secrets.choice((
                f"تسلم يا {name} على الشير",
                f"كفو يا {name}، يعطيك العافية على المشاركة",
                f"يا هلا {name}، شكراً على الشير",
            ))
        category=event.get("classification",{}).get("category")
        if kind=="comment" and category=="greeting":
            return secrets.choice((
                f"وعليكم السلام يا {name}",
                f"يا هلا والله يا {name}",
                f"هلا وغلا يا {name}",
                f"حياك الله يا {name}",
            ))
        if kind=="comment" and category=="compliment":
            return secrets.choice((
                f"كفو يا {name}، من ذوقك",
                f"تسلم يا {name}، ما عليك زود",
                f"يا بعدي يا {name}، كفو",
            ))
        return None

    def worker(self, stop, generation):
        while not stop.wait(.15):
            with self.lock:
                self.responder_heartbeat=time.time()
                self.memory.cleanup_expired()
                self.mood.update(None,self.crowd(),self.cfg,idle=time.time()-self.last_event)
                for old in self.queue.prune_expired():
                    self.skip(old,"expired while audio busy","EXPIRED")
                if self.paused or self.muted:
                    continue
                if self.pending_audio:
                    pending_id=self.pending_audio[0]
                    session=self.audio_streams.get(pending_id)
                    # Streaming playback is bounded by the actual PCM duration. If the browser
                    # never acknowledges finish (WebAudio/autoplay edge case), release it shortly
                    # after the speech should have ended and bump epoch so the player cuts stale audio.
                    stream_deadline=0
                    if session and session.get("done") and session.get("published_at") and session.get("duration_sec"):
                        stream_deadline=session["published_at"]+session["duration_sec"]+2.5
                    stale_by_duration=bool(stream_deadline and time.time()>stream_deadline)
                    stale_by_absolute=time.time()-self.pending_audio[1] > 35
                    if stale_by_duration or stale_by_absolute:
                        self.metrics.inc("audio_failed")
                        stale_id=self.pending_audio[0]
                        self.audio_streams.pop(stale_id,None)
                        self.audio=deque((row for row in self.audio if row["id"]!=stale_id),maxlen=12)
                        self.pending_audio=None
                        self.audio_epoch+=1
                        self.player["busy"]=False
                        self.player["current_id"]=None
                        self.player["current_since"]=0
                        self.publish("error","تم تحرير مقطع صوت بعد مدة النطق المتوقعة واستكمال الطابور",audio_id=stale_id)
                # Only generate one outstanding clip; the player is the sole audio sink.
                if self.pending_audio or self.player["busy"] and time.time()-self.player["heartbeat"]<10:
                    continue
                target=self.auto.choose(self.cfg,self.mood.current,self.crowd())
                if target:
                    self.configure({"preset_id":target})
                cooldown=self.cfg["behavior"]["global_cooldown"]*self.crowd()["cooldown"]
                top_priority=self.queue.max_priority()
                # Gifts/follows/direct questions should feel real-time, not wait behind a long global cooldown.
                if top_priority>=800:
                    cooldown=min(cooldown,.35)
                elif top_priority>=560:
                    cooldown=min(cooldown,.85)
                if time.time()-self.last_spoken < cooldown:
                    continue
                event, expired=self.queue.take()
                for old in expired:
                    self.skip(old,"expired","EXPIRED")
                if event is None:
                    action=self.self_host.next_action(self.cfg,self.crowd(),self.player_idle(),self.queue.size(),self.mood.current)
                    if action:
                        self.ingest(action)
                        continue
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
                elif event["type"]=="self_host" and self.fixture:
                    reply="سؤال سريع، سنايبر ولا شتقن؟"
                elif event["type"]=="self_host":
                    reply=self.providers.reply(event,cfg)
                else:
                    fast_reply=self.fast_interaction_reply(event)
                    if fast_reply:
                        reply=fast_reply
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
                streamed=False; row=None; b64=mime=None
                if self.fixture:
                    output=io.BytesIO()
                    with wave.open(output,"wb") as w:
                        w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000); w.writeframes(b"\0\0"*800)
                    b64,mime=base64.b64encode(output.getvalue()).decode(),"audio/wav"
                elif cfg.get("tts_streaming") and cfg.get("tts_provider")=="openrouter" and self.player.get("pcm_streaming"):
                    with self.lock:
                        row=self.publish("ai",reply,name=event["name"],character=cfg["persona"]["name"],
                            voice=cfg["tts_voice"],voice_style=cfg["voice_style"],streaming=True)
                        audio_id=row["id"]
                        self.audio_streams[audio_id]=dict(id=audio_id,seq=0,chunks=deque(maxlen=300),done=False,failed=False,
                            created_at=time.time(),sample_rate=24000,published=False,published_at=0,duration_sec=0)
                        self.pending_audio=(audio_id,time.time())
                    def on_chunk(raw):
                        with self.lock:
                            if stop.is_set() or generation!=self.generation or version!=self.config_version or self.muted:
                                raise RuntimeError("stream cancelled")
                            session=self.audio_streams.get(audio_id)
                            if not session: raise RuntimeError("stream cancelled")
                            session["seq"]+=1
                            session["chunks"].append(dict(seq=session["seq"],audio_b64=base64.b64encode(raw).decode()))
                            if not session["published"]:
                                session["published"]=True
                                session["published_at"]=time.time()
                                event["timings"]["audio_ready"]=session["published_at"]
                                self.audio.append(dict(id=audio_id,created_at=time.time(),audio_b64="",mime="audio/pcm",
                                    character=cfg["persona"]["name"],epoch=self.audio_epoch,event_type=event["type"],
                                    stream=True,sample_rate=24000))
                    try:
                        meta=self.providers.speak_streaming(reply,cfg,on_chunk)
                        with self.lock:
                            session=self.audio_streams.get(audio_id)
                            if session:
                                session["done"]=True
                                session["duration_sec"]=max(.1,float(meta.get("bytes",0))/(24000*2))
                        streamed=True
                    except Exception:
                        with self.lock:
                            session=self.audio_streams.get(audio_id)
                            partial=bool(session and session["seq"])
                            if session:
                                session["done"]=True;session["failed"]=partial
                            if not partial:
                                self.audio_streams.pop(audio_id,None)
                                self.pending_audio=None
                        if partial:
                            raise
                        b64,mime=self.providers.speak(reply,cfg)
                else:
                    b64,mime=self.providers.speak(reply,cfg)
                event["timings"]["tts_finished"]=time.time()
                with self.lock:
                    if stop.is_set() or generation!=self.generation or version!=self.config_version or self.paused or self.muted or self.queue.expired(event):
                        if row and row["id"] in self.audio_streams:
                            self.audio_streams[row["id"]]["done"]=True;self.audio_streams[row["id"]]["failed"]=True
                        self.skip(event,"cancelled, expired or configuration changed during generation")
                        continue
                    event["timings"].setdefault("audio_ready",time.time())
                    latency=event["timings"]["audio_ready"]-event["received_at"]
                    self.metrics.latency(latency)
                    self.metrics.inc("replies")
                    if event["type"]=="comment": self.metrics.inc("comments_answered")
                    if event["type"]=="join":
                        self.metrics.inc("joins_spoken")
                        self.last_join=time.time()
                    elif event["type"]=="gift":
                        self.metrics.inc("gifts_spoken")
                    elif event["type"]=="follow":
                        self.metrics.inc("follows_spoken")
                    elif event["type"]=="share":
                        self.metrics.inc("shares_spoken")
                    elif event["type"]=="self_host":
                        self.metrics.inc("self_host_lines")
                        if event.get("self_host_action")=="opening":self.metrics.inc("topics_started")
                        self.self_host.record_host_line(event,reply,cfg)
                    if event.get("self_host_topic_reply"):
                        self.self_host.record_ai_reply(event,cfg)
                    self.memory.add_ai_reply(event["username"],reply,event["type"])
                    self.recent_replies.append(normalize(reply))
                    self.last_spoken=time.time()
                    self.decision(event,"REPLY",reply=reply,latency=round(latency,3))
                    if row is None:
                        row=self.publish("ai",reply,name=event["name"],character=cfg["persona"]["name"],
                            voice=cfg["tts_voice"],voice_style=cfg["voice_style"],streaming=False)
                    if not streamed:
                        self.audio.append(dict(id=row["id"],created_at=time.time(),audio_b64=b64,mime=mime,
                            character=cfg["persona"]["name"],epoch=self.audio_epoch,event_type=event["type"],stream=False))
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
            self.queue.clear(); self.audio.clear(); self.audio_streams.clear()
            self.dedupe=SemanticDeduplicator()
            self.recent_replies.clear()
            self.metrics.reset()
            self.self_host.reset()
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
                self.audio.clear(); self.audio_streams.clear(); self.pending_audio=None
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

            finished_id=data.get("finished_id")
            if self.pending_audio and finished_id==self.pending_audio[0] and data.get("boot_id",self.boot_id)==self.boot_id:
                finished=self.pending_audio[0]
                if data.get("failed"): self.metrics.inc("audio_failed")
                self.audio=deque((row for row in self.audio if row["id"]!=finished),maxlen=12)
                self.audio_streams.pop(finished,None)
                self.pending_audio=None

            incoming_busy=bool(data.get("busy"))
            incoming_current=data.get("current_id")
            previous_current=self.player.get("current_id")
            current_since=self.player.get("current_since",0) if incoming_current and incoming_current==previous_current else (now if incoming_current else 0)
            force_reset=False
            if incoming_busy:
                pending_id=self.pending_audio[0] if self.pending_audio else None
                playback_age=now-current_since if current_since else 0
                if not pending_id or incoming_current!=pending_id or playback_age>25:
                    stale=incoming_current
                    if pending_id:
                        self.audio=deque((row for row in self.audio if row["id"]!=pending_id),maxlen=12)
                        self.audio_streams.pop(pending_id,None)
                        self.pending_audio=None
                    if stale is not None:self.metrics.inc("audio_failed")
                    self.audio_epoch+=1
                    incoming_busy=False;incoming_current=None;current_since=0;force_reset=True
                    self.publish("error","تم تحرير مشغل صوت عالق بدون إعادة المقطع",audio_id=stale)

            self.player=dict(id=pid,heartbeat=now,busy=incoming_busy,queue_size=max(0,min(20,int(data.get("queue_size",0)))),
                current_id=incoming_current,current_since=current_since,pcm_streaming=bool(data.get("pcm_streaming")))
            return dict(ok=True,active=True,epoch=self.audio_epoch,boot_id=self.boot_id,muted=self.muted,volume=self.volume,running=self.running,
                force_reset=force_reset,streaming=bool(self.cfg.get("tts_streaming") and self.player.get("pcm_streaming")),
                stream_audio_active=bool(self.pending_audio and self.pending_audio[0] in self.audio_streams))

    def audio_chunks(self, audio_id, since, player_id):
        with self.lock:
            now=time.time()
            active=str(player_id)==self.player["id"] and now-self.player["heartbeat"]<10
            try: aid=int(audio_id); since=int(since)
            except Exception: return dict(ok=False,active=active,chunks=[],done=True,failed=True,latest_seq=0,sample_rate=24000)
            session=self.audio_streams.get(aid)
            valid=active and self.running and not self.muted and self.pending_audio and self.pending_audio[0]==aid and session
            if not valid:
                return dict(ok=True,active=active,chunks=[],done=bool(session and session.get("done")),failed=bool(session and session.get("failed")),
                    latest_seq=session.get("seq",0) if session else 0,sample_rate=session.get("sample_rate",24000) if session else 24000)
            chunks=[x for x in session["chunks"] if x["seq"]>since][:12]
            return dict(ok=True,active=True,chunks=chunks,done=bool(session["done"]),failed=bool(session["failed"]),
                latest_seq=session["seq"],sample_rate=session["sample_rate"],audio_id=aid)

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
                current_playing=self.player["current_id"],current_playing_age=round(time.time()-self.player.get("current_since",0),2) if self.player.get("current_since") else 0,
                memory_users=len(self.memory.users),
                tts_streaming=bool(self.cfg.get("tts_streaming")),player_streaming_capable=bool(self.player.get("pcm_streaming")),
                tts_voice_label=self.cfg.get("tts_voice_label",self.cfg["tts_voice"]),
                stream_audio_active=bool(self.pending_audio and self.pending_audio[0] in self.audio_streams),**self.self_host.status(self.cfg))

    def self_host_status(self):
        with self.lock:return dict(ok=True,**self.self_host.status(self.cfg),metrics=self.self_host.metric_snapshot())

    def self_host_configure(self, data):
        allowed={"self_host_enabled","self_host_mode","self_host_activity","self_host_idle_before_start","self_host_min_gap",
            "self_host_max_gap","self_host_max_consecutive","self_host_topic_timeout","self_host_topic_repeat_cooldown",
            "self_host_banter_level","self_host_question_frequency","self_host_allow_stories","self_host_allow_debates",
            "self_host_allow_banter","self_host_allow_challenges","self_host_allow_polls","self_host_major_question_min","self_host_major_question_max"}
        return self.configure({k:v for k,v in data.items() if k in allowed})

    def self_host_action(self, action, data=None):
        data=data or {}
        with self.lock:
            if action=="speak-now":self.self_host.speak_now()
            elif action=="new-topic":self.self_host.request_new_topic()
            elif action=="stop-topic":
                self.self_host.stop_topic();self.metrics.inc("topics_abandoned")
            elif action=="pause":self.self_host.pause()
            elif action=="resume":self.self_host.resume(self.cfg)
            elif action=="owner-talking":self.self_host.set_owner_talking(bool(data.get("value",True)),self.cfg)
            else:raise ValueError("إجراء Self Host غير مدعوم")
            return self.self_host_status()

    def self_host_metrics(self):
        return {**self.metrics.snapshot(),**self.self_host.metric_snapshot()}

    def health(self):
        with self.lock:
            alive=lambda t:bool(t and t.is_alive())
            return dict(ok=not self.running or alive(self.responder_thread),tiktok=self.tiktok.lower(),
                queue_size=self.queue.size(),queue="ok" if self.queue.size()<self.queue.maxsize else "full",
                responder_thread="ok" if alive(self.responder_thread) else "stopped",tiktok_thread="ok" if alive(self.tiktok_thread) else "stopped",
                responder_heartbeat_age=round(time.time()-self.responder_heartbeat,2) if self.responder_heartbeat else None,
                last_event_age=round(time.time()-self.last_event,2),reconnect_attempts=self.reconnect_attempts,
                player_connected=time.time()-self.player["heartbeat"]<10,**{**self.providers.health,"last_error":self.last_error or self.providers.health["last_error"]})
