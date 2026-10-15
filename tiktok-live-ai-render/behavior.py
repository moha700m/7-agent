import time
from collections import deque
from datetime import datetime, timezone

MOODS = ("calm","normal","social","excited","hype","funny","focused","grateful","busy","crowded","late_night")

PROFILES = {
    "cod_snd": dict(label="Call of Duty Search & Destroy", context="كود سيرش: راوندات وتكتيك، لا تفترض نتيجة أو مكان خصم", recommended="cyber_gamer", topics=["وش سلاحكم المفضل بالسيرش؟","تلعبون هجومي ولا تمسكون زاوية؟"], cooldown=1.1),
    "warzone": dict(label="Warzone", context="وارزون: باتل رويال ولوداوت وتعاون الفريق", recommended="tactical_commander", topics=["وش أفضل لوداوت تجربونه؟","سكواد ولا سولو؟"], cooldown=1),
    "valorant": dict(label="Valorant", context="فالورانت: قدرات ووكلاء واقتصاد الفريق", recommended="coach", topics=["وش المين حقكم بفالورانت؟","مين تحبون تلعبون سموك؟"], cooldown=1.2),
    "fortnite": dict(label="Fortnite", context="فورتنايت: بناء وزيرو بيلد", recommended="female_gamer", topics=["بناء ولا زيرو بيلد؟","وش مودكم المفضل؟"], cooldown=1),
    "chatting": dict(label="Just Chatting", context="سوالف مع الجمهور بدون افتراض أحداث خارج التعليقات", recommended="majlis_friend", topics=["وش آخر لعبة عجبتكم؟","وش ودكم نسولف عنه؟","قهوة ولا شاهي الليلة؟"], cooldown=.8),
    "late_night": dict(label="Late Night", context="جلسة ليلية رايقة وكلام خفيف", recommended="late_night", topics=["وش جوكم الليلة؟","وش لعبة تروقون عليها؟"], cooldown=1.4),
    "reaction": dict(label="Reaction", context="ردود فعل بناء على وصف الجمهور فقط، لا تدعي رؤية الشاشة", recommended="abu_abed", topics=["وش اقتراحاتكم لموضوع السالفة؟","وش أكثر موقف ضحككم بقيم؟"], cooldown=1),
    "competitive": dict(label="Competitive", context="بث تنافسي: اختصر الكلام ولا تقاطع تركيز اللاعب", recommended="esports_commentator", topics=["وش أهم نصيحة للفريق؟","تفضلون سولو ولا فريق ثابت؟"], cooldown=1.5),
}


class CrowdEngine:
    def evaluate(self, metrics, queue_size, enabled=True, profile="cod_snd"):
        load = metrics["comments_per_min"] + metrics["joins_per_min"]*.3 + metrics["gifts_per_min"]*2 + queue_size*4
        state = ("Packed" if load>=120 else "Busy" if load>=50 else "Normal" if load>=12 else "Quiet") if enabled else "Normal"
        return dict(state=state, cooldown={"Quiet":.8,"Normal":1,"Busy":1.7,"Packed":2.5}[state]*PROFILES.get(profile,PROFILES["cod_snd"])["cooldown"],
            max_words={"Quiet":22,"Normal":16,"Busy":10,"Packed":7}[state],
            min_priority={"Quiet":0,"Normal":0,"Busy":430,"Packed":600}[state])


class MoodEngine:
    def __init__(self):
        self.current, self.until = "normal", 0

    def update(self, event, crowd, cfg, idle=0, now=None):
        now = now or time.time()
        override = cfg.get("mood_override", "auto")
        if override in MOODS:
            self.current = override
        elif event and event.get("type")=="gift":
            self.current = "excited" if event.get("gift_value",0)>=100 else "grateful"
            self.until = now+18
        elif now < self.until:
            pass
        elif crowd["state"] == "Packed":
            self.current = "crowded"
        elif crowd["state"] == "Busy":
            self.current = "busy"
        elif idle > 45:
            self.current = "social"
        elif event and event.get("classification",{}).get("category")=="joke":
            self.current = "funny"
        else:
            self.current = cfg.get("mood_default", "normal")
        return self.current


class VoiceStyleEngine:
    def build_tts_style(self, event, mood, character):
        emotion = "natural"
        if event.get("type")=="gift":
            emotion = "very excited" if event.get("gift_value",0)>=100 else "grateful"
        elif event.get("classification",{}).get("category")=="joke":
            emotion = "playful"
        elif mood in ("calm","late_night","focused"):
            emotion = "calm"
        elif mood in ("hype","excited"):
            emotion = "excited"
        speed = character.get("voice_speed",1) * (1.06 if emotion in ("excited","very excited") else .94 if emotion=="calm" else 1)
        return dict(voice=character.get("tts_voice"), gender=character.get("voice_gender"), emotion=emotion,
            energy=character.get("persona",{}).get("energy",6), speed=round(max(.7,min(1.2,speed)),2),
            stability=.3 if emotion in ("playful","excited","very excited") else .7 if emotion=="calm" else .5,
            instructions=f"Speak Saudi Arabic, {emotion}, with natural pacing.", tags="")


class AutoPersonality:
    def __init__(self, clock=time.time):
        self.clock, self.last_switch = clock, 0

    def choose(self, cfg, mood, crowd):
        if cfg.get("personality_mode") != "auto" or self.clock()-self.last_switch < cfg.get("persona_min_duration",900):
            return None
        profile = cfg.get("topic_profile","cod_snd")
        target = PROFILES.get(profile,PROFILES["cod_snd"])["recommended"]
        if profile not in ("late_night","competitive"):
            if mood in ("hype","excited") and crowd["state"] in ("Busy","Packed"):
                target = "hype_girl"
            elif datetime.now(timezone.utc).hour in range(0,5) and profile=="chatting":
                target = "late_night"
        if target == cfg.get("preset_id"):
            return None
        self.last_switch = self.clock()
        return target


class ProactiveHost:
    def __init__(self, clock=time.time):
        self.clock = clock
        self.recent = deque()
        self.index = 0
        self.last_line = ""

    def next_line(self, cfg, idle, player_idle):
        now = self.clock()
        while self.recent and now-self.recent[0] > 600:
            self.recent.popleft()
        if not cfg.get("proactive_enabled") or not player_idle or idle < cfg.get("idle_threshold",75) or len(self.recent)>=cfg.get("max_proactive_per_10min",3):
            return None
        topics=PROFILES.get(cfg.get("topic_profile"),PROFILES["cod_snd"])["topics"]
        choices=[t for t in topics if t != self.last_line]
        line=choices[self.index%len(choices)]
        self.index+=1
        self.last_line=line
        self.recent.append(now)
        return line
