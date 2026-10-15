import json
import os
import threading
import time

import requests


RACE_STATE_URL = os.getenv(
    "RACE_GAME_STATE_URL",
    "https://tiktok-race-game-pmcx.onrender.com/api/state",
).strip()


class RaceCommentator:
    """Poll the race game and inject meaningful commentary into the existing voice pipeline."""

    def __init__(self, host):
        self.host = host
        self.lock = threading.RLock()
        self.session = requests.Session()
        self.stop_event = threading.Event()
        self.connected = False
        self.last_error = ""
        self.last_state = None
        self.last_spoken = 0.0
        self.last_event = ""
        self.last_poll = 0.0
        self.force_speak = False
        self.milestones = set()
        self.thread = threading.Thread(
            target=self._loop, daemon=True, name="race-commentator"
        )
        self.thread.start()

    def reset(self):
        with self.lock:
            self.connected = False
            self.last_error = ""
            self.last_state = None
            self.last_spoken = 0.0
            self.last_event = ""
            self.last_poll = 0.0
            self.force_speak = True
            self.milestones = set()

    def request_speak_now(self):
        with self.lock:
            self.force_speak = True
        return self.status(self.host.cfg)

    def status(self, cfg):
        with self.lock:
            state = self.last_state or {}
            players = state.get("players") or []
            leader = self._leader(players)
            gap = int(cfg.get("race_commentator_min_gap", 8))
            return dict(
                race_commentator_enabled=bool(cfg.get("race_commentator_enabled")),
                race_commentator_connected=self.connected,
                race_commentator_state="connected" if self.connected else ("error" if self.last_error else "waiting"),
                race_commentator_phase=state.get("phase") or "—",
                race_commentator_round=state.get("round") or "—",
                race_commentator_players=int(state.get("player_count") or len(players)),
                race_commentator_leader=(leader or {}).get("name") or (leader or {}).get("username") or "—",
                race_commentator_last_event=self.last_event or "—",
                race_commentator_last_error=self.last_error,
                race_commentator_next_in=max(0, round(gap - (time.time() - self.last_spoken), 1)) if self.last_spoken else 0,
                race_commentator_source=RACE_STATE_URL,
            )

    @staticmethod
    def _leader(players):
        if not players:
            return None
        return max(players, key=lambda p: float(p.get("progress") or 0))

    @staticmethod
    def _top(players, limit=3):
        ranked = sorted(players, key=lambda p: float(p.get("progress") or 0), reverse=True)[:limit]
        return [
            {
                "name": str(p.get("name") or p.get("username") or "لاعب")[:40],
                "progress": round(float(p.get("progress") or 0) * 100),
                "boost": round(float(p.get("boost") or 0) * 100),
            }
            for p in ranked
        ]

    def _choose_event(self, previous, state, cfg, now, forced=False):
        players = state.get("players") or []
        if not players:
            return None

        phase = str(state.get("phase") or "waiting")
        round_no = int(state.get("round") or 1)
        leader = self._leader(players)
        leader_name = str((leader or {}).get("name") or (leader or {}).get("username") or "")
        leader_progress = float((leader or {}).get("progress") or 0)
        prev_phase = str((previous or {}).get("phase") or "")
        prev_round = int((previous or {}).get("round") or round_no)
        prev_players = (previous or {}).get("players") or []
        prev_leader = self._leader(prev_players)
        prev_leader_name = str((prev_leader or {}).get("name") or (prev_leader or {}).get("username") or "")
        pcount = int(state.get("player_count") or len(players))
        prev_count = int((previous or {}).get("player_count") or len(prev_players))

        if round_no != prev_round:
            self.milestones = set()

        if forced:
            return "علّق الآن على وضع السباق الحالي ووضح أهم شيء للمشاهدين"

        if previous is None:
            return "قدم دخول سريع كمعلق وقل للمشاهدين وش صاير الآن"

        if phase == "finished" and prev_phase != "finished":
            winners = state.get("winners") or []
            winner = winners[0].get("name") if winners else leader_name
            return f"أعلن نهاية الجولة والفائز {winner or 'المتصدر'} بحماس مختصر"

        if phase == "countdown" and prev_phase != "countdown":
            if cfg.get("race_commentator_explain", True):
                return "اشرح اللعبة بسرعة: دخول المشاهد يدخله السباق، والتفاعل يعطي دفعات، وأول واحد يوصل يفوز"
            return "العد التنازلي بدأ، حمّس الجمهور للجولة"

        if phase == "racing" and prev_phase != "racing":
            return f"السباق بدأ وفيه {pcount} متسابقين، افتح التعليق بحماس"

        if phase == "waiting" and pcount >= 2 and prev_count < 2:
            return f"صار عندنا {pcount} متسابقين والجولة قربت تبدأ"

        if phase == "racing":
            if leader_progress >= 0.90 and "90" not in self.milestones:
                self.milestones.add("90")
                return f"{leader_name} قرب جدًا من خط النهاية، علّق على اللحظة"
            if leader_progress >= 0.70 and "70" not in self.milestones:
                self.milestones.add("70")
                return f"{leader_name} تعدى سبعين بالمية ومتقدم، علّق باختصار"
            if leader_name and prev_leader_name and leader_name != prev_leader_name:
                return f"تغير المتصدر وصار {leader_name} في الأول"
            if pcount != prev_count and abs(pcount - prev_count) >= 1:
                direction = "زاد" if pcount > prev_count else "نقص"
                return f"عدد المتسابقين {direction} وصار {pcount}، اذكرها فقط إذا تستاهل"

            gap = int(cfg.get("race_commentator_min_gap", 8))
            if now - self.last_spoken >= max(12, gap * 2):
                return "اعط تحديث سريع عن المتصدر وأقرب المنافسين بدون تكرار"

        return None

    def _prompt(self, event_text, state, cfg):
        players = state.get("players") or []
        payload = {
            "round": state.get("round"),
            "phase": state.get("phase"),
            "remaining": state.get("remaining"),
            "player_count": state.get("player_count"),
            "top3": self._top(players),
            "winners": state.get("winners") or [],
        }
        hype = int(cfg.get("race_commentator_hype", 7))
        return (
            "أنت المعلق الصوتي الوحيد للعبة سباق الجمهور في بث تيك توك. "
            "تكلم باللهجة السعودية الطبيعية وبجملة واحدة قصيرة من 5 إلى 16 كلمة. "
            "افهم بيانات السباق أول ثم علّق، ولا تخترع أسماء أو نتائج غير موجودة. "
            "لا تقل إنك تشوف الشاشة؛ أنت تقرأ بيانات اللعبة المباشرة. "
            "إذا الحدث يحتاج شرح اشرح ببساطة، وإذا سباق حمّس بدون صراخ مزعج. "
            f"مستوى الحماس {hype}/10. "
            "قواعد اللعبة: اللي يدخل ينضم للسباق، والتفاعل مثل اللايك والمتابعة والهدايا يعطي دفعات، وأول من يصل يفوز. "
            f"المطلوب الآن: {event_text}. "
            "بيانات السباق الحالية: " + json.dumps(payload, ensure_ascii=False)
        )

    def _loop(self):
        while not self.stop_event.wait(0.75):
            try:
                with self.host.lock:
                    running = bool(self.host.running)
                    cfg = dict(self.host.cfg)
                    enabled = bool(cfg.get("race_commentator_enabled"))
                if not running or not enabled:
                    with self.lock:
                        self.connected = False
                    continue

                response = self.session.get(
                    RACE_STATE_URL,
                    timeout=(1.5, 2.5),
                    headers={"User-Agent": "TikTok-Race-Commentator/1.0"},
                )
                response.raise_for_status()
                state = response.json()
                if not isinstance(state, dict) or not state.get("ok"):
                    raise RuntimeError("race state unavailable")

                now = time.time()
                with self.lock:
                    previous = self.last_state
                    forced = self.force_speak
                    self.force_speak = False
                    self.last_state = state
                    self.last_poll = now
                    self.connected = True
                    self.last_error = ""

                event_text = self._choose_event(previous, state, cfg, now, forced)
                if not event_text:
                    continue

                min_gap = int(cfg.get("race_commentator_min_gap", 8))
                with self.lock:
                    if not forced and now - self.last_spoken < min_gap:
                        continue

                # Viewer comments/gifts and current playback always win over autonomous commentary.
                with self.host.lock:
                    if not self.host.running or self.host.paused or self.host.muted:
                        continue
                    if self.host.queue.size() > 1 or not self.host.player_idle():
                        continue

                prompt = self._prompt(event_text, state, cfg)
                self.host.ingest(
                    dict(
                        type="self_host",
                        username="race_commentator",
                        name="المعلق",
                        text=prompt,
                        self_host_action="race_commentary",
                    )
                )
                with self.lock:
                    self.last_spoken = now
                    self.last_event = event_text

            except Exception as exc:
                with self.lock:
                    self.connected = False
                    self.last_error = type(exc).__name__
                time.sleep(1.25)
