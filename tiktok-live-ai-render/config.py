import os
import math
from characters import resolved_preset

def clamp_num(value, low, high, fallback):
    try:
        number = float(value)
        return max(low, min(high, number)) if math.isfinite(number) else fallback
    except Exception:
        return fallback


def build_config(data):
    preset_id = (data.get("preset_id") or "cyber_gamer").strip()
    preset = resolved_preset(preset_id)

    persona = dict(preset["persona"])
    persona.update({k: v for k, v in (data.get("persona") or {}).items() if v is not None})
    behavior = dict(preset["behavior"])
    behavior.update({k: v for k, v in (data.get("behavior") or {}).items() if v is not None})

    persona["energy"] = int(clamp_num(persona.get("energy"), 0, 10, 7))
    persona["humor"] = int(clamp_num(persona.get("humor"), 0, 10, 6))
    persona["sarcasm"] = int(clamp_num(persona.get("sarcasm"), 0, 10, 3))
    persona["max_words"] = int(clamp_num(persona.get("max_words"), 4, 40, 14))

    behavior["join_cooldown"] = int(clamp_num(behavior.get("join_cooldown"), 0, 300, 20))
    behavior["global_cooldown"] = clamp_num(behavior.get("global_cooldown"), 0, 30, 3.0)
    behavior["like_threshold"] = int(clamp_num(behavior.get("like_threshold"), 10, 5000, 100))
    behavior["temperature"] = clamp_num(behavior.get("temperature"), 0, 2, 0.9)

    result = {
        "preset_id": preset["id"],
        "preset_label": preset["label"],
        "voice_gender": preset["voice_gender"],
        "voice_label": preset["voice_label"],
        "tts_prefix": preset["tts_prefix"],
        "openrouter_key": (data.get("openrouter_key") or os.getenv("OPENROUTER_API_KEY", "")).strip(),
        "model": (data.get("model") or "google/gemini-3.8-flash").strip(),
        "vibi_key": (data.get("vibi_key") or os.getenv("VIBI_API_KEY", "")).strip(),
        "voice_id": (data.get("voice_id") or os.getenv("VIBI_VOICE_ID", "rpGHcNQJvO8dFNNFNj1v")).strip(),
        "tts_voice": (data.get("tts_voice") or preset["voice"] or "adam").strip(),
        "vibi_model": (data.get("vibi_model") or os.getenv("VIBI_MODEL", "eleven_v3")).strip(),
        "tts_model": (data.get("tts_model") or "elevenlabs/eleven-v4-turbo").strip(),
        "tts_provider": (data.get("tts_provider") or "openrouter").strip(),
        "voice_language": (data.get("voice_language") or "ar").strip(),
        "voice_stability": clamp_num(data.get("voice_stability"), 0, 1, 0.5),
        "voice_similarity": clamp_num(data.get("voice_similarity"), 0, 1, 0.75),
        "voice_speed": clamp_num(data.get("voice_speed"), 0.7, 1.3, 1.0),
        "persona": persona,
        "behavior": behavior,
    }



    for key, default in {"personality_mode":"manual", "mood_override":"auto", "topic_profile":"cod_snd", "secondary_model":"google/gemini-3.7-flash", "third_model":"openai/gpt-4o-mini", "tts_fallback":"vibi"}.items():
        result[key] = str(data.get(key) or default)[:120]
    for key, default in {"proactive_enabled":False, "crowd_enabled":True, "allow_profile_persona":False,
        "self_host_enabled":True, "self_host_allow_stories":True, "self_host_allow_debates":True,
        "self_host_allow_banter":True, "self_host_allow_challenges":True, "self_host_allow_polls":True,
        "saudi_voice_auto":True, "tts_streaming":True}.items():
        result[key] = data.get(key, default) is True
    for key, low, high, default in [("memory_ttl_hours",6,24,12),("persona_min_duration",600,3600,900),("idle_threshold",45,300,75),("max_proactive_per_10min",0,8,3),
        ("self_host_idle_before_start",10,120,20),("self_host_min_gap",10,60,20),("self_host_max_gap",15,120,40),
        ("self_host_max_consecutive",1,5,2),("self_host_topic_timeout",30,300,90),("self_host_topic_repeat_cooldown",300,3600,1200),
        ("self_host_banter_level",0,10,6),("self_host_major_question_min",180,1200,300),("self_host_major_question_max",240,1800,480)]:
        result[key] = int(clamp_num(data.get(key),low,high,default))
    result["self_host_activity"] = str(data.get("self_host_activity") or "normal").lower()
    if result["self_host_activity"] not in ("low","normal","high","chaos"): result["self_host_activity"]="normal"
    result["self_host_mode"] = str(data.get("self_host_mode") or "auto").lower()
    if result["self_host_mode"] not in ("auto","manual"): result["self_host_mode"]="auto"
    result["self_host_question_frequency"] = str(data.get("self_host_question_frequency") or "high").lower()
    if result["self_host_question_frequency"] not in ("low","medium","high"): result["self_host_question_frequency"]="high"
    from characters import CHARACTER_PRESETS
    from behavior import PROFILES, MOODS
    result["mood_default"] = CHARACTER_PRESETS[result["preset_id"]].get("mood_default","normal")
    if result["mood_override"] not in (*MOODS,"auto"):
        result["mood_override"] = "auto"
    if result["topic_profile"] not in PROFILES:
        result["topic_profile"] = "cod_snd"
    if result["personality_mode"] not in ("manual","auto"):
        result["personality_mode"] = "manual"
    if result["tts_provider"] not in ("openrouter","vibi"):
        result["tts_provider"] = "openrouter"
    if result["tts_fallback"] not in ("openrouter","vibi","none"):
        result["tts_fallback"] = "vibi"
    # Migrate the old production default before its retirement and keep replies low-latency.
    if result["model"] == "google/gemini-2.5-flash":
        result["model"] = "google/gemini-3.8-flash"
    # Voice aliases have known genders; custom voice IDs keep the preset gender.
    voices = {"adam":"ذكر","brian":"ذكر","liam":"ذكر","george":"ذكر","jessica":"أنثى","matilda":"أنثى","sarah":"أنثى","charlotte":"أنثى","rachel":"أنثى","alice":"أنثى","bella":"أنثى"}
    if result["tts_voice"].lower() in voices:
        result["voice_gender"] = voices[result["tts_voice"].lower()]
    # English-native stock voices are the main source of broken Saudi Arabic.
    # Auto-upgrade them to public Saudi-native ElevenLabs library voices.
    stock_english=set(voices)
    if result.get("saudi_voice_auto",True) and result["tts_voice"].lower() in stock_english:
        result["tts_voice"] = "gVzwmdZzRgBrNjXaTmi5" if result["voice_gender"]=="أنثى" else "s83SAGdFTflAwJcAV81K"
    result["tts_voice_label"] = "Layan — Saudi Najdi" if result["voice_gender"]=="أنثى" and result["tts_voice"]=="gVzwmdZzRgBrNjXaTmi5" else ("Adeeb — Saudi Natural" if result["tts_voice"]=="s83SAGdFTflAwJcAV81K" else result["tts_voice"])
    return result
