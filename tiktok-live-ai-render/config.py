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
        "model": (data.get("model") or "google/gemini-2.5-flash").strip(),
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



    for key, default in {"personality_mode":"manual", "mood_override":"auto", "topic_profile":"cod_snd", "secondary_model":"openai/gpt-4o-mini", "third_model":"meta-llama/llama-3.3-70b-instruct", "tts_fallback":"vibi"}.items():
        result[key] = str(data.get(key) or default)[:120]
    for key, default in {"proactive_enabled":False, "crowd_enabled":True, "allow_profile_persona":False}.items():
        result[key] = data.get(key, default) is True
    for key, low, high, default in [("memory_ttl_hours",6,24,12),("persona_min_duration",600,3600,900),("idle_threshold",45,300,75),("max_proactive_per_10min",0,8,3)]:
        result[key] = int(clamp_num(data.get(key),low,high,default))
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
    # Voice aliases have known genders; custom presets can safely change voice.
    voices = {"adam":"ذكر","brian":"ذكر","liam":"ذكر","george":"ذكر","jessica":"أنثى","matilda":"أنثى","sarah":"أنثى","charlotte":"أنثى","rachel":"أنثى"}
    if result["tts_voice"] in voices:
        result["voice_gender"] = voices[result["tts_voice"]]
    return result
