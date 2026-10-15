import threading
import time
from intelligence import PriorityEngine


class EventQueue:
    TTL = dict(comment=30, join=18, follow=60, share=35, gift=120, like=8, proactive=8, self_host=15, manual=30)

    def __init__(self, maxsize=80, clock=time.time):
        self.maxsize, self.clock = maxsize, clock
        self.items = []
        self.lock = threading.RLock()
        self.priority = PriorityEngine()

    def size(self):
        with self.lock:
            return len(self.items)

    def clear(self):
        with self.lock:
            old, self.items = self.items, []
            return old

    def drop_types(self, types):
        with self.lock:
            types=set(types)
            dropped=[e for e in self.items if e.get("type") in types]
            self.items=[e for e in self.items if e.get("type") not in types]
            return dropped

    def max_priority(self):
        with self.lock:
            if not self.items:
                return 0
            now=self.clock()
            return max(self.priority.score(e,{"now":now}) for e in self.items)

    def put(self, event):
        with self.lock:
            dropped = None
            if len(self.items) >= self.maxsize:
                worst = min(self.items,key=lambda e:self.priority.score(e,{"now":self.clock()}))
                if self.priority.score(event) <= self.priority.score(worst):
                    return event
                self.items.remove(worst)
                dropped = worst
            self.items.append(event)
            return dropped

    def expired(self, event):
        ttl = 90 if event.get("gift_value",0)>=100 else self.TTL.get(event["type"],15)
        return self.clock()-event["received_at"] > ttl

    def take(self):
        with self.lock:
            expired = [e for e in self.items if self.expired(e)]
            self.items = [e for e in self.items if e not in expired]
            if not self.items:
                return None, expired
            best = max(self.items,key=lambda e:(self.priority.score(e,{"now":self.clock()}),-e["received_at"]))
            self.items.remove(best)
            best["priority_score"] = self.priority.score(best,{"now":self.clock()})
            return best, expired
