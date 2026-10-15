import threading
import time
from collections import Counter, deque


class MetricsManager:
    def __init__(self, clock=time.time):
        self.clock = clock
        self.lock = threading.RLock()
        self.reset()

    def reset(self):
        with self.lock:
            self.session_start = self.clock()
            self.counts = Counter({k: 0 for k in (
                "comments_received", "comments_answered", "comments_skipped", "joins_received",
                "joins_spoken", "gifts_received", "follows", "shares", "likes", "ai_errors",
                "tts_errors", "reconnects", "queue_peak", "suppressed_spam", "deduped_messages",
                "expired", "replies", "audio_failed", "queue_dropped")})
            self.recent = deque(maxlen=20000)
            self.latencies = deque(maxlen=500)

    def inc(self, key, amount=1):
        with self.lock:
            self.counts[key] += amount
            self.recent.append((self.clock(), key, amount))

    def peak(self, size):
        with self.lock:
            self.counts["queue_peak"] = max(size, self.counts["queue_peak"])

    def latency(self, seconds):
        with self.lock:
            self.latencies.append(seconds)

    def snapshot(self):
        with self.lock:
            now = self.clock()
            while self.recent and now-self.recent[0][0] > 60:
                self.recent.popleft()
            rates = Counter()
            for _, key, value in self.recent:
                rates[key] += value
            return dict(self.counts, session_start=self.session_start,
                average_response_latency=round(sum(self.latencies)/len(self.latencies), 3) if self.latencies else 0,
                comments_per_min=rates["comments_received"], joins_per_min=rates["joins_received"],
                gifts_per_min=rates["gifts_received"], replies_per_min=rates["replies"])
