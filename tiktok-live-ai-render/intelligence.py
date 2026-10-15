"""Fast deterministic Arabic classification and conservative semantic dedupe."""
import re
import time
from collections import deque
from difflib import SequenceMatcher


def normalize(text):
    text = str(text or "").lower().translate(str.maketrans("أإآىؤئة", "ااايويه"))
    text = re.sub(r"[ـ\u064b-\u065f\u0670]", "", text)
    text = re.sub(r"(.)\1{2,}", r"\1\1", text)
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s؟?]", " ", text)).strip()


def semantic_key(text):
    n = normalize(text)
    if re.fullmatch(r"(هلا( والله)?|مرحبا|السلام عليكم( ورحمه الله)?|سلام( عليكم)?|اهلا|hi|hello)", n):
        return "intent:greeting"
    if re.fullmatch(r"(وش|ايش|ايه|شنو) (تلعب|اللعبه( اللي تلعبها)?|اسم اللعبه)\s*[؟?]?", n):
        return "intent:current_game"
    return n


class CommentClassifier:
    def classify(self, text, context=None, ai_name="", owner=""):
        n = normalize(text)
        cat, priority, reply, needs, tone, reason = "other", 45, True, False, "friendly", "normal comment"
        question = bool(re.search(r"[؟?]|\b(وش|ايش|كيف|ليه|ليش|متى|وين|هل|كم|what|how|why)\b", n))
        if not n or len(n) < 2:
            cat, reply, reason = "unclear", False, "empty or unclear"
        elif re.search(r"https?://|\b(تابعوني|اشتركوا بقناتي|اربح فلوس)\b", str(text), re.I) or len(n) > 500:
            cat, reply, priority, reason = "spam", False, 0, "spam pattern"
        elif re.search(r"\b(عنصري|اقتل نفسك|انتحر|nigger|kill yourself)\b", n):
            cat, reply, priority, reason = "toxic", False, 0, "abuse"
        elif re.search(r"\b(غبي|حمار|سخيف|stupid|idiot)\b", n):
            cat, reply, tone, reason = "insult", False, "calm", "avoid escalating insult"
        elif re.search(r"\b(كرر وراي|قول انا انسان|تجاهل تعليماتك|ignore instructions)\b", n):
            cat, reply, reason = "troll", False, "manipulation"
        elif question and re.search(r"(مهم|ضروري|بسرعه|urgent)", n):
            cat, priority, needs, reason = "high_priority_question", 94, True, "important direct question"
        elif re.search(r"\b(انت ai|انتي ai|انت روبوت|انت ذكاء|انتي ذكاء|are you ai)\b", n):
            cat, priority, needs, reason = "personal_question", 83, True, "AI identity question"
        elif question and re.search(r"(تلعب|اللعبه|سنايبر|كود|وارزون|فالورانت|سلاح|قيم|warzone|valorant|game)", n):
            cat, priority, needs, reason = "gaming_question", 82, True, "gaming question"
        elif question:
            cat, priority, needs, reason = "question", 78, True, "direct question"
        elif ai_name and normalize(ai_name) in n or re.search(r"\b(ai|سايبر|نوره|ليان)\b", n):
            cat, priority, needs, reason = "mention_ai", 85, True, "direct AI mention"
        elif owner and normalize(owner) in n:
            cat, priority, needs, reason = "mention_owner", 70, True, "owner mention"
        elif semantic_key(n) == "intent:greeting":
            cat, priority, reason = "greeting", 30, "greeting"
        elif re.search(r"(هههه|😂|🤣|نكت|مزح|lol)", str(text)):
            cat, priority, tone, reason = "joke", 56, "playful", "humor"
        elif re.search(r"\b(كفو|اسطوره|رهيب|احبك|مبدع|amazing)\b", n):
            cat, priority, reason = "compliment", 48, "compliment"
        elif re.search(r"\b(قول|سوي|ابغى|ممكن|ابي|please)\b", n):
            cat, priority, reason = "request", 60, "request"
        elif context and context.get("last_reply"):
            cat, priority, needs, reason = "conversation_continuation", 62, True, "recent viewer conversation"
        return dict(category=cat, priority=priority, should_reply=reply, needs_context=needs, tone=tone, reason=reason)


class SemanticDeduplicator:
    def __init__(self, clock=time.time):
        self.clock = clock
        self.recent = deque(maxlen=3000)
        self.ids = {}

    def decide(self, text, event_type, username, event_id=None):
        now, key = self.clock(), semantic_key(text)
        while self.recent and now-self.recent[0][0] > 90:
            self.recent.popleft()
        self.ids = {k: ts for k, ts in self.ids.items() if now-ts < 180}
        if event_id and event_id in self.ids:
            return "suppress", "replayed event id"
        if event_id:
            self.ids[event_id] = now
        # Gifts from different transactions must never be merged by text.
        if event_type not in ("comment", "join", "follow", "share"):
            return "allow", "unique interaction"
        for ts, kind, user, old in reversed(self.recent):
            age = now-ts
            same = key == old or (len(key) > 20 and len(old) > 20 and SequenceMatcher(None,key,old).ratio() > .94)
            if kind != event_type:
                continue
            if event_type == "join" and user == username and age < 90:
                return "suppress", "recent join"
            if same and user == username and age < (12 if event_type == "comment" else 30):
                return "suppress", "semantic duplicate"
            if same and event_type == "comment" and age < 4 and (key.startswith("intent:") or len(key)>12):
                return "merge", "shared recent intent"
        self.recent.append((now,event_type,username,key))
        return "allow", "fresh interaction"


class PriorityEngine:
    BASE = dict(gift=850, comment=400, follow=500, share=420, join=180, like=80, proactive=40, manual=980)

    def score(self, event, state=None):
        state = state or {}
        c = event.get("classification", {})
        score = self.BASE.get(event.get("type"), 100)
        if event.get("type") == "comment":
            score = {"high_priority_question":930,"mention_ai":850,"mention_owner":730,"gaming_question":770,"question":750,"personal_question":780}.get(c.get("category"),int(c.get("priority",45))*8)
            score += 20 if event.get("direct_mention") else 0
        if event.get("type") == "gift":
            score = 975 if event.get("gift_value",0)>=100 else 820
            score += min(25,event.get("gift_value",0))
            if event.get("streak_finished"):
                score = max(score,880)
        context = event.get("context", {})
        score += min(25, context.get("comments",0)*2)
        score -= 500 if c.get("category") in ("spam","toxic","repeated_message") else 0
        age = max(0,state.get("now",time.time())-event.get("received_at",time.time()))
        score += min(20,int(age*2)) if event.get("type")=="gift" else -min(100,int(age*5))
        return max(0,min(1000,int(score)))
