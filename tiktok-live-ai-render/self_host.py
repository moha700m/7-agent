"""Autonomous LIVE host: decisions are local; the LLM is used only to word speech."""
import random
import re
import time
from collections import deque

STATES=("IDLE","OPENING_TOPIC","WAITING_FOR_RESPONSE","CONTINUING_TOPIC",
        "ASKING_QUESTION","CHANGING_TOPIC","COOLDOWN","CROWD_PAUSE","PAUSED")

PERSONA_STYLES={
    "cyber_gamer":dict(style="قيمر سعودي سريع وحماسي",preferred=("challenge","gaming_hot_take","this_or_that","audience_question"),banter=6),
    "nora_live":dict(style="مضيفة سعودية مرحة واجتماعية",preferred=("quick_poll","this_or_that","audience_question","funny_question"),banter=5),
    "layan_fun":dict(style="عفوية وخفيفة دم",preferred=("funny_question","light_banter","quick_poll","would_you_rather"),banter=7),
    "sara_calm":dict(style="هادية ورايقة",preferred=("audience_question","gaming_memories","saudi_social_chat","would_you_rather"),banter=2),
    "abu_abed":dict(style="راعي مجلس وطقطقة سعودية",preferred=("light_banter","funny_question","saudi_social_chat","mini_debate"),banter=8),
    "esports_commentator":dict(style="معلق بطولات سريع",preferred=("competitive_question","ranking","gaming_hot_take","challenge"),banter=4),
    "majlis_friend":dict(style="سوالف مجلس طبيعية",preferred=("saudi_social_chat","audience_question","funny_question","gaming_memories"),banter=6),
    "sniper_focus":dict(style="هادي ومركز",preferred=("competitive_question","ranking","audience_question"),banter=3),
    "hype_girl":dict(style="طاقة وتحديات سريعة",preferred=("challenge","quick_poll","this_or_that","gaming_hot_take"),banter=6),
    "late_night":dict(style="جلسة ليلية هادية",preferred=("gaming_memories","saudi_social_chat","would_you_rather","audience_question"),banter=3),
    "pro_host":dict(style="مضيفة مرتبة وتفاعلية",preferred=("audience_question","quick_poll","ranking"),banter=3),
    "chaos_mode":dict(style="مشاكس وطاقة عالية بدون تجريح",preferred=("light_banter","challenge","this_or_that","funny_question"),banter=8),
}
DEFAULT_STYLE=dict(style="مضيف سعودي طبيعي",preferred=("audience_question","this_or_that","quick_poll"),banter=6)

PROFILE_SUBJECTS={
    "cod_snd":["السنايبر","الشتقن","مابات السيرش","أسلحة السيرش","الكلتش","الرش","التمركز","الاندفاع","اللعب التكتيكي","التيميت","الساوند","الكويك سكوب","الكامبر","الرانكد"],
    "warzone":["اللوداوت","السولو","السكواد","القولاك","أماكن النزول","السنايبر بوارزون","القريب ولا البعيد","الروتيشن","العقود","الرانكد بوارزون"],
    "valorant":["الأيجنت المفضل","المابات","الأيم","الرانكد","الديو","السولو كيو","الأوبريتور","الإيكو","السموك","الدخول على السايت"],
    "fortnite":["البناء","زيرو بيلد","الرانكد","الشوتقن","الهبوط","السولو","السكواد","الروتيشن","الأسلحة","الهاي قراوند"],
    "chatting":["آخر لعبة عجبتكم","أول لعبة أدمنتوها","القهوة ولا الشاهي","اللعب بالليل ولا النهار","PC ولا كونسول","يد ولا كيبورد","أكثر لعبة عصبتكم","لعبة تستاهل ترجع","لعبة الناس ظالمينها","أفضل جزء كود","سوالف القيم مع الشباب","وش تلعبون هالأيام"],
    "late_night":["لعبة تروقون عليها","أفضل ذكرى ألعاب","أول جهاز لعب","قيم مستحيل تنساه","اللعب آخر الليل","أهدى لعبة عندكم","لعبة ترجعكم لأيام زمان","أفضل صوتيات لعبة"],
    "competitive":["السولو ولا فريق ثابت","الأيم ولا القرار","الميكانيكس ولا التكتيك","الضغط في الراوند الأخير","الكلتش","التواصل بالفريق","التصنيف الحقيقي للاعب","اللعب الآمن ولا المخاطرة","السرعة ولا الدقة","أفضل طريقة للتطور"],
    "reaction":["أكثر موقف يضحك بالقيم","أسوأ تيميت ممكن يجيك","أغرب قرار شفته من لاعب","لقطة تخليك تقفل اللعبة","وش الشيء اللي يستفزكم بالألعاب","أغرب تحدي جربتوه","أفضل ردة فعل بعد كلتش","مين يتحمل الخسارة أكثر"],
}
PATTERNS=(
    ("audience_question","وش رايكم في {s}؟","افتح سؤال جمهور قصير عن {s}"),
    ("gaming_hot_take","أبي رأي صريح عن {s}","اطلب رأي صريح وخفيف عن {s}"),
    ("ranking","عطوا {s} تقييم من عشرة","اطلب تقييم سريع لـ {s} من عشرة"),
    ("challenge","لو تغيرون شيء واحد في {s} وش تختارون؟","اعط الجمهور تحدي تغيير شيء واحد في {s}"),
    ("quick_poll","تصويت سريع: {s} يستاهل ولا لا؟","سو تصويت سريع وطبيعي عن {s}"),
)
CHOICES={
    "cod_snd":[("سنايبر","شتقن"),("راش","تمركز"),("كويك سكوب","هارد سكوب"),("سولو","فريق ثابت"),("ساوند","أيم"),("كلتش","لعب جماعي")],
    "warzone":[("سولو","سكواد"),("سنايبر","AR"),("نزول حار","نزول هادي"),("قريب","بعيد")],
    "valorant":[("سولو كيو","ديو"),("Operator","Rifle"),("أيم","قدرات"),("هجوم","دفاع")],
    "fortnite":[("بناء","زيرو بيلد"),("سولو","سكواد"),("رانكد","عادي"),("شوتقن","AR")],
    "chatting":[("PC","كونسول"),("يد","كيبورد"),("قهوة","شاهي"),("ليل","نهار"),("تنافسي","رايق")],
    "late_night":[("سوالف","لعب"),("لعبة قديمة","لعبة جديدة"),("قهوة","شاهي")],
    "competitive":[("أيم","قرار"),("ميكانيكس","تكتيك"),("سرعة","دقة"),("سولو","فريق")],
    "reaction":[("تضحك","تعصب"),("تكمّل","تقفل اللعبة"),("تطقطق","تسكت")],
}
GENERAL=(
    ("gaming_memories","أول جهاز لعبت عليه","اسأل الجمهور عن أول جهاز لعبوا عليه"),
    ("gaming_memories","لعبة ترجعك لأيام زمان","اسأل عن لعبة مرتبطة بذكريات قديمة"),
    ("would_you_rather","محترف ساكت ولا لاعب عادي يسولف","اطرح اختيارا خفيفا بين الاثنين"),
    ("would_you_rather","تفوز بقيم ممل ولا تخسر بقيم أسطوري","اطرح اختيارا بين فوز ممل وخسارة ممتعة"),
    ("saudi_social_chat","وش جو اللعب عندكم الليلة","افتح سالفة قصيرة عن جو اللعب الليلة"),
    ("light_banter","مين يقول آخر قيم ثم يكمل ساعتين","اطقطق بلطف على سالفة آخر قيم"),
    ("light_banter","مين يلوم النت بعد كل خسارة","اطقطق على أعذار النت بدون استهداف شخص"),
    ("stream_question","وش نوع السوالف اللي ودكم نفتحها","اسأل الجمهور عن نوع السوالف المطلوبة"),
    ("gaming_story","تخيل تدخل قيم وكل فريقك نفس السلاح","ابدأ سيناريو ألعاب متخيل وواضح أنه تخيل"),
    ("competitive_question","وش أهم شيء يفرق اللاعب الزين","اسأل عن أهم صفة في اللاعب الجيد"),
)

def norm(text):
    text=str(text or "").lower().replace("ـ","")
    text=re.sub(r"[\u064b-\u065f\u0670]","",text)
    text=text.translate(str.maketrans({"أ":"ا","إ":"ا","آ":"ا","ى":"ي","ة":"ه"}))
    return re.sub(r"\s+"," ",re.sub(r"[^\w\u0600-\u06ff]+"," ",text)).strip()

def slug(text):
    return re.sub(r"[^\w]+","_",norm(text)).strip("_")[:42] or "topic"

def build_topic_bank():
    out=[];seen=set()
    def add(profile,category,title,seed,energy=6):
        base=f"{profile}_{category}_{slug(title)}";tid=base;n=2
        while tid in seen:tid=f"{base}_{n}";n+=1
        seen.add(tid)
        out.append(dict(id=tid,category=category,title=title,prompt_seed=seed,
            suitable_profiles=[profile] if profile!="all" else ["all"],energy=energy,cooldown=1200,
            keywords=[x for x in norm(title).split() if len(x)>2]))
    for profile,subjects in PROFILE_SUBJECTS.items():
        for i,s in enumerate(subjects):
            for j,(cat,tf,sf) in enumerate(PATTERNS):
                add(profile,cat,tf.format(s=s),sf.format(s=s),5+((i+j)%4))
    for profile,pairs in CHOICES.items():
        for a,b in pairs:add(profile,"this_or_that",f"{a} ولا {b}؟",f"اسأل الجمهور يختارون بين {a} و{b} بكلام سعودي قصير",7)
    for cat,title,seed in GENERAL:add("all",cat,title,seed,6)
    return out

TOPIC_BANK=build_topic_bank()

class TopicMemory:
    def __init__(self,clock=time.time):
        self.clock=clock;self.records=deque(maxlen=50);self.last_used={};self.stats={}
    def can_use(self,tid,cooldown):return self.clock()-self.last_used.get(tid,0)>=cooldown
    def started(self,topic):
        self.last_used[topic["id"]]=self.clock()
        self.records.append(dict(topic_id=topic["id"],title=topic["title"],category=topic["category"],
            started_at=self.clock(),ended_at=None,audience_responses=0,unique_participants=0,engagement_score=0,success=False))
    def finished(self,topic,responses,participants,score,success):
        if not topic:return
        for row in reversed(self.records):
            if row["topic_id"]==topic["id"] and row["ended_at"] is None:
                row.update(ended_at=self.clock(),audience_responses=responses,unique_participants=participants,engagement_score=score,success=bool(success));break
        st=self.stats.setdefault(topic["id"],dict(times_used=0,successes=0,failures=0,total_score=0))
        st["times_used"]+=1;st["successes" if success else "failures"]+=1;st["total_score"]+=score
    def bias(self,tid):
        st=self.stats.get(tid)
        if not st:return 1.0
        avg=st["total_score"]/max(1,st["times_used"])
        return max(.35,min(1.7,.7+avg/100-st["failures"]*.08+st["successes"]*.04))
    def best_worst(self):
        rows=[x for x in self.records if x["ended_at"] is not None]
        return (max(rows,key=lambda x:x["engagement_score"]),min(rows,key=lambda x:x["engagement_score"])) if rows else (None,None)

class EngagementEvaluator:
    def score(self,c):
        first=c.get("first_response_delay")
        speed=18 if first is not None and first<=8 else 12 if first is not None and first<=20 else 5 if first is not None else 0
        return max(0,min(100,int(c.get("responses",0)*11+len(c.get("participants",set()))*8+
            c.get("direct_mentions",0)*4+c.get("gifts",0)*7+c.get("shares",0)*5+speed)))

class HookGenerator:
    HOOKS=("سؤال سريع","طيب اسمعوا","خلونا نحسمها","أبي رأي صريح","عندي لكم وحدة","أبي أشوف مين فاهم",
        "وش تقولون","خلونا نختلف شوي","تصويت سريع","أعطوني الزبدة","بدون مجاملة","اختيار واحد بس",
        "لا تفكرون كثير","سؤال قيمرز","خلونا نشوف الأغلبية","طيب هذي مهمة","من جدكم","اسمعوا هذي","ودي أعرف رأيكم","جاوبوا بسرعة")
    def __init__(self,rng):self.rng=rng;self.recent=deque(maxlen=20)
    def next(self):
        choices=[x for x in self.HOOKS if x not in self.recent] or list(self.HOOKS)
        x=self.rng.choice(choices);self.recent.append(x);return x

class TopicEngine:
    def __init__(self,rng):self.rng=rng
    def allowed(self,t,cfg):
        c=t["category"]
        if c=="gaming_story" and not cfg.get("self_host_allow_stories",True):return False
        if c=="gaming_hot_take" and not cfg.get("self_host_allow_debates",True):return False
        if c=="challenge" and not cfg.get("self_host_allow_challenges",True):return False
        if c in ("quick_poll","this_or_that") and not cfg.get("self_host_allow_polls",True):return False
        if c=="light_banter" and not cfg.get("self_host_allow_banter",True):return False
        return True
    def choose(self,cfg,memory,style,mood):
        profile=cfg.get("topic_profile","cod_snd");cool=float(cfg.get("self_host_topic_repeat_cooldown",1200));preferred=set(style.get("preferred",()))
        rows=[];weights=[]
        for t in TOPIC_BANK:
            if profile not in t["suitable_profiles"] and "all" not in t["suitable_profiles"]:continue
            if not self.allowed(t,cfg) or not memory.can_use(t["id"],max(cool,t["cooldown"])):continue
            w=(2.3 if t["category"] in preferred else 1.0)*memory.bias(t["id"])
            if mood in ("hype","excited","funny") and t["energy"]>=7:w*=1.3
            rows.append(t);weights.append(max(.05,w))
        if not rows:
            rows=[t for t in TOPIC_BANK if profile in t["suitable_profiles"] or "all" in t["suitable_profiles"]];weights=[1]*len(rows)
        return self.rng.choices(rows,weights=weights,k=1)[0] if rows else None

class SelfHostEngine:
    def __init__(self,clock=time.time,rng=None):
        self.clock=clock;self.rng=rng or random.Random();self.memory=TopicMemory(clock)
        self.evaluator=EngagementEvaluator();self.hooks=HookGenerator(self.rng);self.topics=TopicEngine(self.rng);self.reset()
    def reset(self):
        now=self.clock();self.state="IDLE";self.paused=False;self.owner_talking=False;self.current_topic=None;self.conversation=None
        self.last_line="";self.last_host_line_at=0;self.last_external_activity=now;self.next_eligible_at=now+20
        self.consecutive=0;self.followups=0;self.force=False;self.force_new=False
    def style(self,cfg):return PERSONA_STYLES.get(cfg.get("preset_id"),DEFAULT_STYLE)
    def banter_level(self,cfg):
        try:return max(0,min(10,int(cfg.get("self_host_banter_level",self.style(cfg)["banter"]))))
        except Exception:return self.style(cfg)["banter"]
    def schedule_after_activity(self,cfg):
        base=max(10,float(cfg.get("self_host_idle_before_start",20)));factor={"low":1.6,"normal":1,"high":.75,"chaos":.55}.get(cfg.get("self_host_activity","normal"),1)
        self.next_eligible_at=self.clock()+base*factor+self.rng.uniform(0,min(10,base*.35))
    def engagement(self):return self.evaluator.score(self.conversation or {})
    def finish_topic(self):
        if not self.current_topic:return
        c=self.conversation or {};score=self.engagement();responses=c.get("responses",0);participants=len(c.get("participants",set()))
        self.memory.finished(self.current_topic,responses,participants,score,responses>0 or score>=35)
        self.current_topic=None;self.conversation=None;self.consecutive=0;self.followups=0;self.state="COOLDOWN"
    def interrupt(self):
        if self.state not in ("PAUSED","IDLE"):self.state="WAITING_FOR_RESPONSE" if self.current_topic else "COOLDOWN"
    def pause(self):self.paused=True;self.state="PAUSED"
    def resume(self,cfg):self.paused=False;self.state="WAITING_FOR_RESPONSE" if self.current_topic else "IDLE";self.schedule_after_activity(cfg)
    def set_owner_talking(self,value,cfg):
        self.owner_talking=bool(value)
        if self.owner_talking:self.state="PAUSED"
        else:self.resume(cfg)
    def stop_topic(self):self.finish_topic();self.next_eligible_at=self.clock()+20
    def request_new_topic(self):self.finish_topic();self.force_new=True;self.force=True;self.next_eligible_at=self.clock()
    def speak_now(self):self.force=True;self.next_eligible_at=self.clock()
    def simulate_idle(self,seconds):self.last_external_activity=self.clock()-max(0,float(seconds));self.next_eligible_at=self.clock()
    def note_event(self,event,cfg):
        kind=event.get("type");now=self.clock()
        if kind in ("comment","gift","follow","share"):self.last_external_activity=now;self.schedule_after_activity(cfg)
        if not self.current_topic or not self.conversation:return False
        if kind=="gift":self.conversation["gifts"]+=1;return False
        if kind=="share":self.conversation["shares"]+=1;return False
        if kind!="comment":return False
        cat=event.get("classification",{}).get("category","other")
        if cat in ("spam","toxic","troll","insult","greeting"):return False
        text=norm(event.get("text"));keywords=self.current_topic.get("keywords",[])
        relevant=any(k and k in text for k in keywords)
        if not relevant and self.state in ("WAITING_FOR_RESPONSE","CONTINUING_TOPIC") and len(text.split())<=7 and cat in ("other","question","gaming_question","request","mention_ai","conversation_continuation","compliment","joke"):relevant=True
        if not relevant:return False
        c=self.conversation;c["responses"]+=1;c["participants"].add(event.get("username","viewer"))
        if c["first_response_delay"] is None:c["first_response_delay"]=max(0,now-c["started_at"])
        if event.get("direct_mention"):c["direct_mentions"]+=1
        event["self_host_topic_reply"]=True
        event["self_host_context"]=dict(topic_id=self.current_topic["id"],title=self.current_topic["title"],responses=c["responses"],participants=len(c["participants"]))
        event["classification"].update(category="conversation_continuation",priority=max(88,event["classification"].get("priority",0)),should_reply=True,needs_context=True,reason="reply to active self-host topic")
        self.state="CONTINUING_TOPIC";self.next_eligible_at=now+max(20,float(cfg.get("self_host_min_gap",20)));return True
    def probability(self,crowd,activity):
        base={"Quiet":.9,"Normal":.6,"Busy":.2,"Packed":.05}.get(crowd.get("state"),.5)
        return max(.02,min(.98,base*{"low":.65,"normal":1,"high":1.18,"chaos":1.35}.get(activity,1)))
    def should_speak(self,cfg,crowd,player_idle,queue_size):
        now=self.clock()
        if not cfg.get("self_host_enabled",True) or self.paused or self.owner_talking or not player_idle or queue_size:return False
        if self.force:return True
        if now<self.next_eligible_at:return False
        if self.current_topic and now-(self.conversation or {}).get("started_at",now)>float(cfg.get("self_host_topic_timeout",90)):
            self.finish_topic();self.schedule_after_activity(cfg);return False
        if self.current_topic:
            if now-self.last_host_line_at<max(15,float(cfg.get("self_host_min_gap",20))):return False
            if self.followups>=1 and (self.conversation or {}).get("responses",0)==0:
                self.finish_topic();self.schedule_after_activity(cfg);return False
            return self.consecutive<int(cfg.get("self_host_max_consecutive",2)) or (self.conversation or {}).get("responses",0)>0
        if now-self.last_external_activity<max(10,float(cfg.get("self_host_idle_before_start",20))):return False
        if self.rng.random()>self.probability(crowd,cfg.get("self_host_activity","normal")):
            self.next_eligible_at=now+self.rng.uniform(8,22);return False
        return True
    def make_event(self,prompt,action):
        return dict(type="self_host",username="self_host",name="المضيف",text=prompt,self_host_action=action,
            self_host_topic_id=self.current_topic["id"] if self.current_topic else "",self_host_topic_title=self.current_topic["title"] if self.current_topic else "")
    def open_topic(self,cfg,mood):
        topic=self.topics.choose(cfg,self.memory,self.style(cfg),mood)
        if not topic:return None
        self.current_topic=topic;now=self.clock();self.memory.started(topic);self.conversation=dict(started_at=now,participants=set(),responses=0,direct_mentions=0,gifts=0,shares=0,first_response_delay=None)
        self.state="OPENING_TOPIC";self.consecutive=0;self.followups=0;self.force_new=False
        hook=self.hooks.next();max_words=min(16,int(cfg.get("persona",{}).get("max_words",14)))
        prompt=f"""مهمة SELF HOST: افتح موضوعا تفاعليا واحدا فقط.
الشخصية: {cfg.get("persona",{}).get("name","المضيف")} — {self.style(cfg)["style"]}.
الموضوع: {topic["title"]}. فكرة الصياغة: {topic["prompt_seed"]}.
ابدأ إن ناسب بهوك قريب من: {hook}. مستوى الطقطقة الآمن: {self.banter_level(cfg)}/10.
تكلم عامية سعودية طبيعية، سؤال أو جملة قابلة للرد، بحد أقصى {max_words} كلمة.
لا تدعي أنك ترى الشاشة ولا تخترع نتيجة أو حدثا داخل اللعبة ولا تطلب هدايا."""
        return self.make_event(prompt,"opening")
    def followup(self,cfg):
        c=self.conversation or {};responses=c.get("responses",0);self.state="ASKING_QUESTION"
        if responses:
            prompt=f"""تابع موضوع SELF HOST الحالي: {self.current_topic["title"]}.
الجمهور رد {responses} مرة من {len(c.get("participants",set()))} مشاركين.
ادع شخصا آخر للرأي أو علّق بخفة، بدون اختراع أسماء أو أرقام. جملة سعودية قصيرة فقط."""
            action="continue"
        else:
            prompt=f"""موضوع SELF HOST الحالي: {self.current_topic["title"]}. لم يصل رد عليه.
اعمل Follow-up واحد فقط قصير وطبيعي وبطقطقة خفيفة، ثم اترك مساحة للجمهور. لا تعيد السؤال حرفيا."""
            action="followup"
        return self.make_event(prompt,action)
    def next_action(self,cfg,crowd,player_idle,queue_size,mood):
        if not self.should_speak(cfg,crowd,player_idle,queue_size):return None
        self.force=False
        return self.open_topic(cfg,mood) if self.force_new or not self.current_topic else self.followup(cfg)
    def record_host_line(self,event,reply,cfg):
        now=self.clock();self.last_line=reply;self.last_host_line_at=now;self.consecutive+=1
        if event.get("self_host_action") in ("followup","continue"):self.followups+=1
        self.state="WAITING_FOR_RESPONSE";mingap=max(10,float(cfg.get("self_host_min_gap",20)));maxgap=max(mingap,float(cfg.get("self_host_max_gap",40)))
        self.next_eligible_at=now+self.rng.uniform(mingap,maxgap)
    def record_ai_reply(self,event,cfg):
        if event.get("self_host_topic_reply"):
            self.last_external_activity=self.clock();self.state="WAITING_FOR_RESPONSE";self.next_eligible_at=self.clock()+max(20,float(cfg.get("self_host_min_gap",20)))
    def status(self,cfg):
        now=self.clock();best,worst=self.memory.best_worst();c=self.conversation or {}
        return dict(self_host_enabled=cfg.get("self_host_enabled",True),self_host_state=self.state,current_topic=self.current_topic["title"] if self.current_topic else "",
            current_topic_id=self.current_topic["id"] if self.current_topic else "",current_topic_age=round(now-c.get("started_at",now),1) if self.current_topic else 0,
            topic_engagement=self.engagement(),topic_participants=len(c.get("participants",set())),next_self_host_at=self.next_eligible_at,
            next_self_host_in=max(0,round(self.next_eligible_at-now,1)),self_host_activity=cfg.get("self_host_activity","normal"),
            banter_level=self.banter_level(cfg),consecutive_self_host_lines=self.consecutive,last_self_host_line=self.last_line,
            owner_talking=self.owner_talking,self_host_paused=self.paused,topic_bank_size=len(TOPIC_BANK),
            best_topic=best["title"] if best else "",worst_topic=worst["title"] if worst else "")
    def metric_snapshot(self):
        best,worst=self.memory.best_worst();rows=[x for x in self.memory.records if x["ended_at"] is not None]
        return dict(average_topic_engagement=round(sum(x["engagement_score"] for x in rows)/len(rows),1) if rows else 0,
            best_topic=best["title"] if best else "",worst_topic=worst["title"] if worst else "")
