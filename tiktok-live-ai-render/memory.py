"""Bounded, ephemeral viewer memory. Replace this interface with Redis later."""
import re
import threading
import time
from collections import deque


def safe_text(text):
    text = str(text or "")[:700]
    text = re.sub(r"sk-or-[A-Za-z0-9_-]+|sk_[A-Za-z0-9_-]{16,}|Bearer\s+\S+", "[مفتاح محذوف]",text,flags=re.I)
    # Never retain contact details or declarations of sensitive personal facts.
    text = re.sub(r"https?://\S+|[\w.+-]+@[\w.-]+|\+?\d[\d\s-]{7,}\d", "[محذوف]", text)
    if re.search(r"عمري|رقم جوالي|عنواني|اسكن في|أسكن في|مرضي|تشخيصي|ديني|راتبي|حسابي البنكي|توجهي|انا مصاب|أنا مصاب|انا مريض|أنا مريض|my (address|phone|diagnosis|age|religion|salary|bank)", text, re.I):
        return "[معلومة شخصية محذوفة]"
    return text[:300]


class ViewerMemoryManager:
    def __init__(self, ttl=43200, max_users=5000, clock=time.time):
        self.ttl, self.max_users, self.clock = ttl, max_users, clock
        self.users = {}
        self.lock = threading.RLock()

    def _user(self, username, nickname=""):
        key = str(username or "anonymous").lower().lstrip("@")[:80]
        now = self.clock()
        self.cleanup_expired()
        if key not in self.users:
            if len(self.users) >= self.max_users:
                self.users.pop(min(self.users, key=lambda k: self.users[k]["last_interaction"]))
            self.users[key] = dict(username=key, nickname=safe_text(nickname)[:80],
                messages=deque(maxlen=15), replies=deque(maxlen=10), first_seen=now,
                last_interaction=now, joins=0, comments=0, gifts=0, last_welcome=0,
                last_topic="", last_interaction_type="")
        u = self.users[key]
        u["last_interaction"] = now
        if nickname:
            u["nickname"] = safe_text(nickname)[:80]
        return u

    def add_user_message(self, username, text, nickname="", topic=""):
        with self.lock:
            u = self._user(username, nickname)
            u["messages"].append(safe_text(text))
            u["comments"] += 1
            u["last_topic"] = safe_text(topic)[:80]
            u["last_interaction_type"] = "comment"

    def add_ai_reply(self, username, reply, interaction="comment"):
        with self.lock:
            u = self._user(username)
            u["replies"].append(safe_text(reply))
            u["last_interaction_type"] = interaction
            if interaction == "join":
                u["last_welcome"] = self.clock()

    def mark_join(self, username, nickname=""):
        with self.lock:
            u = self._user(username, nickname)
            u["joins"] += 1
            u["last_interaction_type"] = "join"
            return u["joins"]

    def mark_gift(self, username, nickname="", count=1):
        with self.lock:
            u = self._user(username, nickname)
            u["gifts"] += count
            u["last_interaction_type"] = "gift"

    def get_user_context(self, username):
        with self.lock:
            self.cleanup_expired()
            u = self.users.get(str(username).lower().lstrip("@"))
            if not u:
                return {}
            return dict(username=u["username"], nickname=u["nickname"],
                known_minutes=round((self.clock()-u["first_seen"])/60, 1),
                joins=u["joins"], comments=u["comments"], gifts=u["gifts"],
                last_interaction_time=u["last_interaction"],
                last_interaction_type=u["last_interaction_type"],
                welcomed_recently=bool(u["last_welcome"] and self.clock()-u["last_welcome"] < 300),
                last_topic=u["last_topic"], last_message=list(u["messages"])[-2:],
                last_reply=list(u["replies"])[-1:])

    def cleanup_expired(self):
        with self.lock:
            now = self.clock()
            for k in list(self.users):
                if now-self.users[k]["last_interaction"] >= self.ttl:
                    del self.users[k]
