import re

COMMON_TERMS = (
    (r"\bCall\s*of\s*Duty\b", "كول أوف ديوتي"),
    (r"\bCOD\b", "كود"),
    (r"\bS\s*&\s*D\b|\bSND\b|\bSearch\s*(?:and|&)\s*Destroy\b", "سيرش أند ديستروي"),
    (r"\bWarzone\b", "وارزون"),
    (r"\bValorant\b", "فالورانت"),
    (r"\bFortnite\b", "فورتنايت"),
    (r"\bTik\s*Tok\b", "تيك توك"),
    (r"\bAI\b", "إيه آي"),
    (r"\bTTS\b", "تي تي إس"),
    (r"\bPC\b", "بي سي"),
    (r"\bAFK\b", "إيه إف كي"),
    (r"\bFPS\b", "إف بي إس"),
    (r"\bADS\b", "إيه دي إس"),
    (r"\bGG\b", "جي جي"),
    (r"\bquick\s*scope\b", "كويك سكوب"),
    (r"\bhard\s*scope\b", "هارد سكوب"),
    (r"\bshotgun\b", "شتقن"),
    (r"\bsniper\b", "سنايبر"),
    (r"\branked\b", "رانكد"),
    (r"\bclutch\b", "كلتش"),
    (r"\brushing\b|\brush\b", "راش"),
    (r"\blive\b", "لايف"),
)

ONES = ["صفر","واحد","اثنين","ثلاثة","أربعة","خمسة","ستة","سبعة","ثمانية","تسعة",
        "عشرة","أحدعش","اثنعش","ثلاثطعش","أربعطعش","خمسطعش","ستطعش","سبعطعش","ثمنطعش","تسعطعش"]
TENS = {20:"عشرين",30:"ثلاثين",40:"أربعين",50:"خمسين",60:"ستين",70:"سبعين",80:"ثمانين",90:"تسعين"}

def _number_words(n):
    if 0 <= n < 20:
        return ONES[n]
    if 20 <= n < 100:
        ten=(n//10)*10; one=n%10
        return TENS[ten] if not one else f"{ONES[one]} و{TENS[ten]}"
    if n == 100:
        return "مية"
    if 100 < n < 1000:
        h=n//100; rest=n%100
        hword={1:"مية",2:"ميتين",3:"ثلاثمية",4:"أربعمية",5:"خمسمية",6:"ستمية",7:"سبعمية",8:"ثمانمية",9:"تسعمية"}[h]
        return hword if not rest else f"{hword} و{_number_words(rest)}"
    return str(n)

def _replace_number(match):
    raw=match.group(0)
    try:
        n=int(raw)
    except Exception:
        return raw
    return _number_words(n) if 0 <= n < 1000 else raw

def normalize_saudi_speech(text):
    """Prepare short Saudi-Arabic LIVE speech for TTS without changing meaning."""
    text=str(text or "")
    text=re.sub(r"\[[^\]]{1,60}\]", " ", text)
    text=re.sub(r"https?://\S+|www\.\S+", " ", text, flags=re.I)
    text=text.replace("_"," ").replace("|","،")
    # Guard against obvious non-Saudi dialect drift in generated text before TTS.
    dialect_map=(
        (r"\bعايز(?:ة|ين)?\b","أبي"),
        (r"\bدلوقتي\b","الحين"),
        (r"\bازاي|إزاي\b","كيف"),
        (r"\bأوي|اوي\b","مرّة"),
        (r"\bمش كده\b","مو كذا"),
        (r"\bمش\b","مو"),
    )
    for pattern,replacement in dialect_map:
        text=re.sub(pattern,replacement,text,flags=re.I)
    for pattern,replacement in COMMON_TERMS:
        text=re.sub(pattern,replacement,text,flags=re.I)
    text=re.sub(r"(?<!\w)\d{1,3}(?!\w)",_replace_number,text)
    text=re.sub(r"([!?؟،,.])\1+",r"\1",text)
    text=re.sub(r"\s*([،؟!])\s*",r"\1 ",text)
    text=re.sub(r"\s+"," ",text).strip(" -،")
    return text[:500]

def safe_spoken_name(name):
    name=str(name or "").strip().replace("_"," ")
    if not name:
        return ""
    # Random handles sound broken when read aloud; skip them instead of mangling Arabic.
    latin=sum(ch.isascii() and ch.isalnum() for ch in name)
    letters=sum(ch.isalnum() for ch in name)
    if letters and latin/letters > .85 and (len(name)>14 or sum(ch.isdigit() for ch in name)>=4):
        return ""
    return normalize_saudi_speech(name)[:40]
