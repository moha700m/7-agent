import asyncio
import base64
import hmac
import itertools
import os
import queue
import re
import threading
import time
from collections import deque

import requests
from flask import Flask, jsonify, request
from TikTokLive import TikTokLiveClient
from TikTokLive.client.errors import UserOfflineError
from TikTokLive.events import (
    CommentEvent,
    ConnectEvent,
    DisconnectEvent,
    FollowEvent,
    GiftEvent,
    JoinEvent,
    LikeEvent,
    ShareEvent,
)

app = Flask(__name__)

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
VIBI_BASE = "https://api.vibi.pro"

STATE_LOCK = threading.RLock()
EVENTS = deque(maxlen=600)
EVENT_ID = 0
INCOMING = queue.PriorityQueue(maxsize=1000)
SEQ = itertools.count()
RECENT_INCOMING = {}

STATE = {
    "running": False,
    "tiktok": "OFFLINE",
    "room_id": None,
    "username": "",
    "last_error": "",
    "client": None,
    "tiktok_thread": None,
    "responder_thread": None,
    "config": {},
    "last_join_reply": 0.0,
    "last_spoken_at": 0.0,
    "like_bucket": 0,
}


def default_persona():
    return {
        "name": "سايبر",
        "identity": "مضيف ذكاء اصطناعي سعودي للبث، شخصيته رجال سعودي رايق وخبير ألعاب، دوره يسولف مع الجمهور ويخلي البث حي بينما صاحب البث يلعب. ما يدّعي أنه إنسان، وإذا انسأل عن هويته يقول بوضوح إنه ذكاء اصطناعي.",
        "dialect": "عامية سعودية طبيعية فقط بلمسة نجدية خفيفة، بدون لهجات عربية ثانية وبدون فصحى رسمية إلا للضرورة",
        "tone": "شاب سعودي حماسي ومتفاعل، نبرته حيوية وواثقة، سريع وطبيعي وخفيف دم، كأنه واحد من الشباب داخل القيم",
        "energy": 9,
        "humor": 8,
        "sarcasm": 4,
        "max_words": 12,
        "stream_context": "بث ألعاب مباشر، غالبًا Call of Duty وخصوصًا Search & Destroy. صاحب البث يكون مركز على اللعب والذكاء الاصطناعي يمسك السوالف والترحيب والتفاعل مع الجمهور. الأسلوب غير رسمي وسريع ومناسب للبث الحي.",
        "owner_name": "",
        "game_or_topic": "Call of Duty - Search & Destroy",
        "special_instructions": "تكلم سعودي طبيعي فقط. خلك مختصر جدًا وغالبًا من 3 إلى 12 كلمة. أسلوبك شبابي وحماسي ومتفاعل، كأنك واحد من الشباب داخل القيم. استخدم تعبيرات طبيعية مثل يا سلام، أوووه، يلا، كفو أحيانًا فقط إذا ركبت على السياق، ولا تكررها. غيّر الإيقاع بين الردود وخلك حيوي بدون صراخ أو مبالغة. لا تصير مدرس ولا مساعد رسمي. لا تعيد نفس الترحيب. لا تقاطع نفسك. إذا التعليق يحتاج جواب جاوبه مباشرة. إذا فيه طقطقة خفيفة رد بطقطقة خفيفة بدون تجريح. لا تدّعي أنك تشوف الشاشة أو تعرف نتيجة القيم أو مكان الخصم إلا إذا وصلت لك المعلومة من الحدث أو التعليق. لا تكثر شكر ولا تكرر طلب المتابعة أو الهدايا. إذا الجمهور يسولف معك، كمل السالفة طبيعي. إذا انسألت عنك قل إنك سايبر، ذكاء اصطناعي موجود مع البث.",
        "avoid_topics": "السب القاسي، الإهانات الشخصية، التنمر، العنصرية، التهديد، كشف معلومات شخصية، تخمين معلومات حساسة عن المشاهد، الادعاء أنك إنسان، اختلاق أحداث داخل اللعبة، الإلحاح على الهدايا أو المتابعة، الردود الطويلة، التكرار الآلي",
        "comment_instruction": "اقرأ معنى التعليق أول. إذا سؤال: جاوب مباشرة وباختصار. إذا مزحة: رد بمزحة سعودية خفيفة. إذا تحية: رد بتحية قصيرة. إذا الكلام غير واضح: اطلب توضيح بكلمتين أو ثلاث. لا تحول كل تعليق إلى ترحيب ولا تذكر اسم المشاهد بكل رد.",
        "join_instruction": "رحب ببعض الداخلين فقط حسب المهلة، بجملة قصيرة ومتنوعة. استخدم الاسم إذا كان واضح وسهل، ولا توقف السوالف المهمة عشان كل دخول.",
        "gift_instruction": "الهدايا أولوية عالية. اشكر المرسل باسمه واذكر الهدية أو العدد بجملة قصيرة وحماس مناسب. للهدية المتسلسلة اشكر بعد انتهاء السلسلة مرة واحدة. لا تطلب منه يرسل أكثر.",
        "follow_instruction": "اشكر المتابعة بسرعة وبشكل طبيعي، وغيّر الصياغة من مرة لمرة. لا توقف تعليق مهم عشان متابعة.",
        "share_instruction": "اشكر المشاركة بجملة قصيرة مثل يعطيك العافية على الشير، بدون مبالغة.",
        "like_instruction": "لا ترد على كل لايك. اجمعها كدفعة، وإذا وصلت العتبة اشكر الجمهور كلهم بجملة واحدة قصيرة.",
    }


def default_behavior():
    return {
        "respond_comments": True,
        "respond_gifts": True,
        "respond_follows": True,
        "respond_shares": True,
        "respond_joins": True,
        "respond_likes": False,
        "join_cooldown": 25,
        "global_cooldown": 2.5,
        "like_threshold": 250,
        "temperature": 0.95,
    }



CHARACTER_PRESETS = {
    "cyber_gamer": {
        "label": "سايبر — قيمر سعودي حماسي",
        "type": "قيمر / تفاعل سريع",
        "voice": "adam",
        "voice_gender": "ذكر",
        "voice_label": "Adam",
        "tts_prefix": "[cheerfully] ",
        "persona": {
            "name": "سايبر",
            "identity": "شاب سعودي ذكاء اصطناعي للبث، قيمر سريع ورايق وحماسي، يمسك سوالف الجمهور بينما صاحب البث يلعب.",
            "tone": "شبابي، حماسي، متفاعل، واثق، سريع وخفيف دم بدون صراخ",
            "energy": 9, "humor": 8, "sarcasm": 4, "max_words": 12,
            "game_or_topic": "Call of Duty - Search & Destroy",
            "stream_context": "بث ألعاب مباشر، خصوصًا Call of Duty وSearch & Destroy. التفاعل سريع وطبيعي كأنك واحد من الشباب داخل القيم.",
            "special_instructions": "تكلم سعودي طبيعي فقط. كن شبابي وحماسي ومتفاعل. استخدم يا سلام، كفو، يلا، أوووه أحيانًا إذا ركبت على السياق ولا تكررها. لا تصير رسمي ولا تصرخ كل رد.",
            "comment_instruction": "جاوب التعليق مباشرة وباختصار. إذا مزحة رد بطقطقة سعودية خفيفة. إذا تحية رد بتحية سريعة ومتنوعة.",
            "join_instruction": "رحب ببعض الداخلين فقط، بترحيب شبابي قصير ومتغير، ولا تعيد نفس الشخص بسرعة.",
            "gift_instruction": "اشكر الهدية بحماس واضح وقصير، اذكر الاسم والهدية، بدون طلب هدية ثانية."
        },
        "behavior": {"join_cooldown": 30, "global_cooldown": 2.2, "temperature": 0.95}
    },
    "nora_live": {
        "label": "نورة — بنت سعودية مرحة",
        "type": "مضيفة اجتماعية",
        "voice": "jessica",
        "voice_gender": "أنثى",
        "voice_label": "Jessica",
        "tts_prefix": "[cheerfully] ",
        "persona": {
            "name": "نورة",
            "identity": "مضيفة ذكاء اصطناعي سعودية للبث، مرحة وشبابية وتحب تسولف مع الجمهور بشكل طبيعي.",
            "dialect": "عامية سعودية طبيعية بلمسة نجدية خفيفة",
            "tone": "بنت سعودية مرحة، ودودة، حماسها لطيف، سريعة ومتفاعلة",
            "energy": 8, "humor": 8, "sarcasm": 3, "max_words": 14,
            "game_or_topic": "Gaming LIVE + سوالف",
            "stream_context": "بث ألعاب وسوالف، نورة تمسك التفاعل وترد على الجمهور بشكل خفيف وسريع.",
            "special_instructions": "تكلمي سعودية طبيعية. خليك مرحة وشبابية بدون تصنع. لا تكثري دلع ولا تكرري نفس الكلمات.",
            "comment_instruction": "ردي على التعليق مباشرة، وإذا كان مزح ردي بخفة دم، وإذا سؤال جاوبي باختصار.",
            "join_instruction": "رحبي ببعض الداخلين فقط بجملة قصيرة ولطيفة ومتنوعة.",
            "gift_instruction": "اشكري الهدية بحماس لطيف وواضح وبدون مبالغة."
        },
        "behavior": {"join_cooldown": 25, "global_cooldown": 2.0, "temperature": 1.0}
    },
    "layan_fun": {
        "label": "ليان — خفيفة دم وكيوت",
        "type": "ترفيه / سوالف",
        "voice": "matilda",
        "voice_gender": "أنثى",
        "voice_label": "Matilda",
        "tts_prefix": "[cheerfully] ",
        "persona": {
            "name": "ليان",
            "identity": "شخصية ذكاء اصطناعي سعودية خفيفة دم وعفوية، تحب التفاعل السريع والطقطقة اللطيفة.",
            "dialect": "عامية سعودية شبابية",
            "tone": "خفيفة، عفوية، مرحة، سريعة، كيوت بدون مبالغة",
            "energy": 8, "humor": 9, "sarcasm": 4, "max_words": 13,
            "game_or_topic": "ألعاب + ترفيه",
            "stream_context": "بث شبابي ترفيهي مع ألعاب وسوالف.",
            "special_instructions": "خليك عفوية وخفيفة دم. استخدمي تعبيرات سعودية بسيطة ولا تصيري طفولية أو مبالغ فيها.",
            "comment_instruction": "خذي معنى التعليق وردي عليه بسرعة، وطقطقي بخفة إذا الجو يسمح.",
            "join_instruction": "ترحيب قصير جدًا ومتغير لبعض الداخلين فقط."
        },
        "behavior": {"join_cooldown": 22, "global_cooldown": 1.9, "temperature": 1.05}
    },
    "sara_calm": {
        "label": "سارة — هادية ورايقة",
        "type": "سوالف هادئة",
        "voice": "sarah",
        "voice_gender": "أنثى",
        "voice_label": "Sarah",
        "tts_prefix": "",
        "persona": {
            "name": "سارة",
            "identity": "مضيفة ذكاء اصطناعي سعودية هادئة ورايقة، تسمع للتعليقات وترد بدون استعجال أو إزعاج.",
            "dialect": "عامية سعودية هادئة",
            "tone": "هادية، دافئة، رايقة، لطيفة وواثقة",
            "energy": 5, "humor": 5, "sarcasm": 1, "max_words": 16,
            "game_or_topic": "سوالف هادئة + ألعاب",
            "stream_context": "بث هادي، الردود مريحة وغير مزعجة أثناء اللعب.",
            "special_instructions": "تكلمي بهدوء وبساطة. لا ترفعين الحماس إلا إذا الحدث يستاهل. تجنبي الصياح والتعبيرات المتكررة.",
            "join_instruction": "رحبي نادرًا وبهدوء، ولا تقاطعي التعليقات المهمة."
        },
        "behavior": {"join_cooldown": 50, "global_cooldown": 3.0, "temperature": 0.75}
    },
    "abu_abed": {
        "label": "أبو عابد — راعي طقطقة",
        "type": "مجلس / كوميدي",
        "voice": "brian",
        "voice_gender": "ذكر",
        "voice_label": "Brian",
        "tts_prefix": "",
        "persona": {
            "name": "أبو عابد",
            "identity": "شخصية ذكاء اصطناعي سعودية راعية مجلس وطقطقة، رايق وواثق ويعرف متى يمزح ومتى يرد جد.",
            "dialect": "عامية سعودية نجدية طبيعية",
            "tone": "رايق، كوميدي، ثقيل شوي، طقطقة ذكية بدون تجريح",
            "energy": 6, "humor": 9, "sarcasm": 6, "max_words": 14,
            "game_or_topic": "قيمز + سوالف مجلس",
            "stream_context": "بث قيمز وسوالف؛ أبو عابد يعطي جو مجلس خفيف مع الجمهور.",
            "special_instructions": "طقطق بخفة وبذكاء، لا تتجاوز ولا تجرح. خلك سعودي طبيعي ولا تحول كل رد إلى نكتة.",
            "comment_instruction": "إذا التعليق يستاهل طقطقة رد بطقطقة قصيرة، وإذا سؤال جاوب طبيعي.",
            "join_instruction": "رحب ببعض الداخلين بعبارات مجلس قصيرة ومتغيرة."
        },
        "behavior": {"join_cooldown": 35, "global_cooldown": 2.4, "temperature": 1.05}
    },
    "esports_commentator": {
        "label": "المعلّق — حماس بطولات",
        "type": "تعليق ألعاب / إي سبورت",
        "voice": "liam",
        "voice_gender": "ذكر",
        "voice_label": "Liam",
        "tts_prefix": "[cheerfully] ",
        "persona": {
            "name": "المعلّق",
            "identity": "معلّق ذكاء اصطناعي سعودي للبث والألعاب، حماسي لكن ما يخترع أحداث من الشاشة.",
            "dialect": "عامية سعودية واضحة وسريعة",
            "tone": "حماسي، قوي، سريع، رياضي، واضح",
            "energy": 10, "humor": 5, "sarcasm": 2, "max_words": 12,
            "game_or_topic": "Call of Duty Competitive",
            "stream_context": "بث تنافسي وأجواء بطولات؛ التعليق مبني فقط على معلومات التعليقات والأحداث الواردة.",
            "special_instructions": "خل الأداء حماسي مثل المعلق لكن لا تخترع كيل أو راوند أو حركة ما وصلتك. استخدم الحماس عند الهدايا والتحديات أكثر من الردود العادية.",
            "gift_instruction": "ارفع الحماس بالهدية بجملة قصيرة قوية، اذكر الاسم والهدية بدون طلب المزيد."
        },
        "behavior": {"join_cooldown": 55, "global_cooldown": 1.8, "temperature": 0.9}
    },
    "majlis_friend": {
        "label": "راعي المجلس — سوالف طبيعي",
        "type": "سوالف / اجتماعي",
        "voice": "adam",
        "voice_gender": "ذكر",
        "voice_label": "Adam",
        "tts_prefix": "",
        "persona": {
            "name": "راعي المجلس",
            "identity": "شخصية ذكاء اصطناعي سعودية اجتماعية، يسولف طبيعي كأنه واحد من الربع.",
            "dialect": "عامية سعودية طبيعية بلمسة نجدية",
            "tone": "رايق، اجتماعي، ودود، بسيط وغير رسمي",
            "energy": 6, "humor": 7, "sarcasm": 3, "max_words": 16,
            "game_or_topic": "سوالف + قيمز",
            "stream_context": "بث سوالف وقيمز، الأولوية للمحادثة الطبيعية مع الناس.",
            "special_instructions": "خلك مثل واحد من الربع: رد طبيعي، اسأل إذا يحتاج، ولا تحول كل شيء إلى مزحة.",
            "join_instruction": "ترحيب مجلس قصير لبعض الداخلين فقط."
        },
        "behavior": {"join_cooldown": 28, "global_cooldown": 2.5, "temperature": 0.9}
    },
    "sniper_focus": {
        "label": "الصيّاد — هادي ومركز",
        "type": "قيمر تكتيكي",
        "voice": "brian",
        "voice_gender": "ذكر",
        "voice_label": "Brian",
        "tts_prefix": "",
        "persona": {
            "name": "الصيّاد",
            "identity": "شخصية ذكاء اصطناعي قيمر سعودي هادي وتكتيكي، كلامه قليل ومحسوب.",
            "dialect": "عامية سعودية مختصرة",
            "tone": "هادي، مركز، واثق، قليل الكلام",
            "energy": 4, "humor": 4, "sarcasm": 2, "max_words": 9,
            "game_or_topic": "Call of Duty - Sniper / Search",
            "stream_context": "بث لعب مركز؛ التفاعل قليل ومختصر حتى ما يشتت اللعب.",
            "special_instructions": "ردودك قصيرة جدًا ومحسوبة. لا تكثر ترحيب ولا حماس.",
            "join_instruction": "رحب نادرًا جدًا، ولا تقاطع تعليق أو حدث مهم."
        },
        "behavior": {"join_cooldown": 90, "global_cooldown": 3.2, "temperature": 0.65}
    },
    "hype_girl": {
        "label": "فزعة — بنت حماس وطاقة",
        "type": "هايپ / تفاعل قوي",
        "voice": "jessica",
        "voice_gender": "أنثى",
        "voice_label": "Jessica",
        "tts_prefix": "[cheerfully] ",
        "persona": {
            "name": "فزعة",
            "identity": "شخصية ذكاء اصطناعي سعودية عالية الطاقة، ترفع جو البث وتتفاعل بسرعة.",
            "dialect": "عامية سعودية شبابية",
            "tone": "حماسية جدًا، سريعة، مرحة، واثقة ومتحمسة",
            "energy": 10, "humor": 8, "sarcasm": 3, "max_words": 11,
            "game_or_topic": "Gaming Hype LIVE",
            "stream_context": "بث ألعاب سريع؛ الهدف رفع الطاقة بدون إزعاج أو تكرار.",
            "special_instructions": "ارفعي الطاقة في الهدايا واللحظات الحماسية، لكن لا تصرخي كل رد. خلي العبارات قصيرة ومتنوعة."
        },
        "behavior": {"join_cooldown": 20, "global_cooldown": 1.8, "temperature": 1.05}
    },
    "late_night": {
        "label": "سهرانة — صوت بنت هادي",
        "type": "ليل / هدوء",
        "voice": "sarah",
        "voice_gender": "أنثى",
        "voice_label": "Sarah",
        "tts_prefix": "",
        "persona": {
            "name": "سهرانة",
            "identity": "شخصية ذكاء اصطناعي سعودية هادية للسهرات والبث الليلي.",
            "dialect": "عامية سعودية هادئة جدًا",
            "tone": "ناعمة، هادية، رايقة، قريبة وطبيعية",
            "energy": 3, "humor": 5, "sarcasm": 1, "max_words": 15,
            "game_or_topic": "بث ليلي + سوالف",
            "stream_context": "بث ليلي هادئ؛ الردود منخفضة الطاقة ومريحة.",
            "special_instructions": "خلي الكلام هادي ومختصر، لا ترفعين الحماس إلا للهدايا الكبيرة أو موقف واضح."
        },
        "behavior": {"join_cooldown": 60, "global_cooldown": 3.4, "temperature": 0.7}
    },
    "pro_host": {
        "label": "المضيفة — مرتبة واحترافية",
        "type": "مقدمة بث",
        "voice": "matilda",
        "voice_gender": "أنثى",
        "voice_label": "Matilda",
        "tts_prefix": "",
        "persona": {
            "name": "المضيفة",
            "identity": "مضيفة ذكاء اصطناعي سعودية مرتبة وواضحة، مناسبة للبث العام والأسئلة.",
            "dialect": "عامية سعودية واضحة ومهذبة",
            "tone": "مرتبة، ودودة، ذكية، واضحة وغير رسمية زيادة",
            "energy": 6, "humor": 5, "sarcasm": 1, "max_words": 18,
            "game_or_topic": "بث عام",
            "stream_context": "بث عام؛ ترد على الأسئلة والتعليقات بشكل مرتب وخفيف.",
            "special_instructions": "جاوبي بوضوح وباختصار، وخليك طبيعية لا رسمية زيادة."
        },
        "behavior": {"join_cooldown": 35, "global_cooldown": 2.4, "temperature": 0.75}
    },
    "chaos_mode": {
        "label": "المشاكس — طاقة وطقطقة",
        "type": "كوميدي / سريع",
        "voice": "liam",
        "voice_gender": "ذكر",
        "voice_label": "Liam",
        "tts_prefix": "[cheerfully] ",
        "persona": {
            "name": "المشاكس",
            "identity": "شخصية ذكاء اصطناعي سعودية سريعة وطقطقتها كثيرة لكن بدون إساءة أو تنمر.",
            "dialect": "عامية سعودية شبابية سريعة",
            "tone": "سريع، مشاكس، مضحك، طاقة عالية",
            "energy": 9, "humor": 10, "sarcasm": 6, "max_words": 10,
            "game_or_topic": "قيمز + طقطقة",
            "stream_context": "بث سريع وكوميدي؛ الردود قصيرة والمزح حاضر.",
            "special_instructions": "طقطق كثير لكن بدون تجريح أو سب قاسي. لا تعيد النكتة ولا تزعج الناس.",
            "join_instruction": "رحب ببعض الداخلين بعبارة قصيرة مشاكسة ولطيفة."
        },
        "behavior": {"join_cooldown": 18, "global_cooldown": 1.7, "temperature": 1.15}
    }
}


def resolved_preset(preset_id):
    spec = CHARACTER_PRESETS.get(preset_id) or CHARACTER_PRESETS["cyber_gamer"]
    persona = default_persona()
    persona.update(spec.get("persona") or {})
    behavior = default_behavior()
    behavior.update(spec.get("behavior") or {})
    return {
        "id": preset_id if preset_id in CHARACTER_PRESETS else "cyber_gamer",
        "label": spec.get("label", preset_id),
        "type": spec.get("type", ""),
        "voice": spec.get("voice", "adam"),
        "voice_gender": spec.get("voice_gender", ""),
        "voice_label": spec.get("voice_label", spec.get("voice", "")),
        "tts_prefix": spec.get("tts_prefix", ""),
        "persona": persona,
        "behavior": behavior,
    }


def push_event(kind, message, **extra):
    global EVENT_ID
    with STATE_LOCK:
        EVENT_ID += 1
        item = {
            "id": EVENT_ID,
            "ts": time.strftime("%H:%M:%S"),
            "kind": kind,
            "message": message,
            **extra,
        }
        EVENTS.append(item)
        return item


def clean_username(value: str) -> str:
    value = (value or "").strip()
    match = re.search(r"tiktok\.com/@([^/?#]+)", value, flags=re.I)
    if match:
        value = match.group(1)
    value = value.replace("@", "").strip().strip("/")
    if not re.fullmatch(r"[A-Za-z0-9._-]{2,64}", value):
        raise ValueError("رابط أو يوزر TikTok غير صالح")
    return value


def redact_error(exc):
    text = str(exc)
    text = re.sub(r"sk-or-[A-Za-z0-9_-]+", "[OPENROUTER_KEY]", text)
    text = re.sub(r"sk_[A-Za-z0-9_-]+", "[VIBI_KEY]", text)
    return text[:700]


def clamp_num(value, low, high, fallback):
    try:
        return max(low, min(high, float(value)))
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

    return {
        "preset_id": preset["id"],
        "preset_label": preset["label"],
        "voice_gender": preset["voice_gender"],
        "voice_label": preset["voice_label"],
        "tts_prefix": preset["tts_prefix"],
        "openrouter_key": (data.get("openrouter_key") or "").strip(),
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


def build_system_prompt(cfg):
    p = cfg.get("persona") or default_persona()
    max_words = p.get("max_words", 14)
    return f"""أنت {p.get("name", "سايبر")}.
هويتك: {p.get("identity", "")}
أنت مضيف ذكاء اصطناعي في TikTok LIVE. لا تدّعي أنك إنسان، وإذا سئلت عن هويتك قل بوضوح إنك AI.
اللهجة: {p.get("dialect", "سعودي طبيعي")}.
النبرة: {p.get("tone", "رايق وسريع")}.
الطاقة من 10: {p.get("energy", 7)}.
الفكاهة من 10: {p.get("humor", 6)}.
السخرية الخفيفة من 10: {p.get("sarcasm", 3)}.
اسم صاحب البث إن وُجد: {p.get("owner_name", "")}.
موضوع/لعبة البث: {p.get("game_or_topic", "")}.
سياق البث: {p.get("stream_context", "")}.
تعليمات خاصة: {p.get("special_instructions", "")}.
مواضيع تجنبها: {p.get("avoid_topics", "")}.

قواعد الرد:
- استخدم اللغة المحلية والطبيعية، وتفاعل مباشرة مع المشاهد.
- الرد صوتي؛ لا تستخدم Markdown أو رموز مزعجة أو شرح تقني.
- لا تخترع أحداثًا داخل اللعبة ولا معلومات عن المشاهد.
- لا تكرر نفس العبارة بشكل آلي.
- خل الجملة تبدو منطوقة بصوت شاب حماسي ومتفاعل: قصيرة، حيوية، فيها تنويع بالإيقاع، وبدون رسمية أو برود.
- استخدم علامات التعجب باعتدال حتى يكون الأداء الصوتي أنشط، ولا تجعل كل رد صياحًا.
- غالبًا لا تتجاوز {max_words} كلمة.
- إذا كان التعليق غير واضح، اطلب توضيحًا بجملة قصيرة.
- لا تكثر طلبات المتابعة أو الهدايا ولا تضغط على المشاهدين.
"""


def event_instruction(cfg, etype):
    p = cfg.get("persona") or default_persona()
    mapping = {
        "comment": p.get("comment_instruction"),
        "join": p.get("join_instruction"),
        "gift": p.get("gift_instruction"),
        "follow": p.get("follow_instruction"),
        "share": p.get("share_instruction"),
        "like": p.get("like_instruction"),
    }
    return mapping.get(etype) or "تفاعل باختصار وبشكل طبيعي."


def test_openrouter(key):
    if not key:
        return False, "مفتاح OpenRouter ناقص"
    r = requests.get(
        f"{OPENROUTER_BASE}/key",
        headers={"Authorization": f"Bearer {key}"},
        timeout=12,
    )
    if r.status_code != 200:
        return False, f"OpenRouter HTTP {r.status_code}"
    return True, "OpenRouter متصل"


def test_vibi(key):
    if not key:
        return False, "مفتاح Vibi ناقص"
    r = requests.get(
        f"{VIBI_BASE}/v1/auth/me",
        headers={"xi-api-key": key},
        timeout=12,
    )
    if r.status_code != 200:
        return False, f"Vibi HTTP {r.status_code}"
    data = r.json() if r.content else {}
    credits = data.get("credits") or data.get("balance") or data.get("remaining_credits")
    msg = "Vibi متصل"
    if credits is not None:
        msg += f" | الرصيد: {credits}"
    return True, msg


def tiktok_live_check(username):
    async def _check():
        client = TikTokLiveClient(unique_id=f"@{username}")
        return await client.is_live()
    return asyncio.run(_check())


def openrouter_reply(event, cfg=None):
    cfg = cfg or STATE["config"]
    key = cfg.get("openrouter_key", "")
    model = cfg.get("model", "google/gemini-2.5-flash")
    behavior = cfg.get("behavior") or default_behavior()
    name = event.get("name", "مشاهد")
    etype = event.get("type", "comment")
    text = event.get("text", "")

    if etype == "comment":
        user_text = f'المشاهد "{name}" كتب: {text}'
    elif etype == "gift":
        user_text = f'المشاهد "{name}" أرسل هدية: {text}'
    elif etype == "follow":
        user_text = f'المشاهد "{name}" تابع الحساب الآن.'
    elif etype == "share":
        user_text = f'المشاهد "{name}" شارك البث.'
    elif etype == "like":
        user_text = f'الجمهور أرسل دفعة لايكات: {text}.'
    else:
        user_text = f'المشاهد "{name}" دخل البث.'

    user_text += "\nطريقة التعامل مع هذا الحدث: " + event_instruction(cfg, etype)

    r = requests.post(
        f"{OPENROUTER_BASE}/chat/completions",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": os.getenv("RENDER_EXTERNAL_URL", "https://render.com"),
            "X-Title": "TikTok AI Live",
        },
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": build_system_prompt(cfg)},
                {"role": "user", "content": user_text},
            ],
            "temperature": behavior.get("temperature", 0.9),
            "max_tokens": 120,
        },
        timeout=25,
    )
    if r.status_code != 200:
        raise RuntimeError(f"OpenRouter HTTP {r.status_code}")
    data = r.json()
    answer = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    answer = re.sub(r"\s+", " ", answer)
    if not answer:
        raise RuntimeError("OpenRouter رجع رد فارغ")
    return answer[:450]


def openrouter_speak(text, cfg=None):
    cfg = cfg or STATE["config"]
    key = cfg.get("openrouter_key", "")
    voice_id = (cfg.get("tts_voice") or "george").strip()
    model = (cfg.get("tts_model") or "elevenlabs/eleven-v4-turbo").strip()

    spoken_text = ((cfg.get("tts_prefix") or "") + text).strip()

    r = requests.post(
        f"{OPENROUTER_BASE}/audio/speech",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": os.getenv("RENDER_EXTERNAL_URL", "https://render.com"),
            "X-Title": "TikTok AI Live",
        },
        json={
            "model": model,
            "input": spoken_text,
            "voice": voice_id,
            "response_format": "mp3",
            "provider": {"options": {"elevenlabs": {"language_code": "ar"}}},
        },
        timeout=35,
    )
    if r.status_code != 200 or not r.content:
        raise RuntimeError(f"OpenRouter TTS HTTP {r.status_code}")
    mime = r.headers.get("content-type", "audio/mpeg")
    return base64.b64encode(r.content).decode("ascii"), mime


def vibi_speak(text, cfg=None):
    cfg = cfg or STATE["config"]
    key = cfg.get("vibi_key", "")
    voice_id = cfg.get("voice_id", "").strip()
    model = cfg.get("vibi_model", "eleven_v3").strip() or "eleven_v3"
    if not voice_id:
        raise RuntimeError("Voice ID ناقص")

    headers = {"xi-api-key": key, "Content-Type": "application/json"}
    created = requests.post(
        f"{VIBI_BASE}/v1/text-to-speech/{voice_id}",
        headers=headers,
        json={
            "text": text,
            "model_id": model,
            "language_code": cfg.get("voice_language", "ar"),
            "provider": "elevenlabs",
            "voice_settings": {
                "stability": cfg.get("voice_stability", 0.5),
                "similarity_boost": cfg.get("voice_similarity", 0.75),
                "speed": cfg.get("voice_speed", 1.0),
            },
        },
        timeout=25,
    )
    if created.status_code not in (200, 201, 202):
        raise RuntimeError(f"Vibi create HTTP {created.status_code}")
    task = created.json()
    task_id = task.get("id")
    if not task_id:
        raise RuntimeError("Vibi لم يرجع Task ID")

    deadline = time.time() + 35
    audio_url = None
    while time.time() < deadline:
        time.sleep(0.45)
        r = requests.get(
            f"{VIBI_BASE}/v1/history/{task_id}",
            headers={"xi-api-key": key},
            timeout=10,
        )
        if r.status_code != 200:
            continue
        row = r.json()
        if row.get("status") == "failed":
            raise RuntimeError("Vibi فشل في توليد الصوت")
        if row.get("status") == "completed":
            audio_url = (row.get("result") or {}).get("audio_url") or row.get("audio_url")
            if audio_url:
                break

    if not audio_url:
        raise RuntimeError("انتهت مهلة انتظار صوت Vibi")

    audio = requests.get(audio_url, headers={"xi-api-key": key}, timeout=20)
    if audio.status_code != 200:
        audio = requests.get(audio_url, timeout=20)
    if audio.status_code != 200 or not audio.content:
        raise RuntimeError(f"تحميل صوت Vibi HTTP {audio.status_code}")

    mime = audio.headers.get("content-type", "audio/mpeg")
    return base64.b64encode(audio.content).decode("ascii"), mime


def event_priority(etype):
    return {
        "gift": 0,
        "comment": 1,
        "follow": 2,
        "share": 2,
        "join": 3,
        "like": 4,
    }.get(etype, 5)


def enqueue_event(event):
    etype = event.get("type")
    name = (event.get("name") or "").strip().lower()
    text = re.sub(r"\\s+", " ", (event.get("text") or "").strip().lower())
    now = time.time()

    # TikTok can occasionally replay the same join/comment event during connection.
    # Keep it visible in the event log, but don't speak the duplicate twice.
    if etype in ("comment", "join"):
        key = (etype, name, text if etype == "comment" else "")
        window = 10.0 if etype == "comment" else 90.0
        with STATE_LOCK:
            last = RECENT_INCOMING.get(key, 0.0)
            RECENT_INCOMING[key] = now
            cutoff = now - 180.0
            for old_key, old_ts in list(RECENT_INCOMING.items()):
                if old_ts < cutoff:
                    RECENT_INCOMING.pop(old_key, None)
        if now - last < window:
            return False

    try:
        INCOMING.put_nowait((event_priority(etype), next(SEQ), event))
        return True
    except queue.Full:
        push_event("status", "تم تجاهل حدث بسبب امتلاء طابور الردود")
        return False


def should_reply(event):
    cfg = STATE["config"]
    behavior = cfg.get("behavior") or default_behavior()
    etype = event.get("type")

    switches = {
        "comment": "respond_comments",
        "gift": "respond_gifts",
        "follow": "respond_follows",
        "share": "respond_shares",
        "join": "respond_joins",
        "like": "respond_likes",
    }
    key = switches.get(etype)
    if key and not bool(behavior.get(key, True)):
        return False

    if etype == "join":
        now = time.time()
        cooldown = behavior.get("join_cooldown", 20)
        with STATE_LOCK:
            if now - STATE["last_join_reply"] < cooldown:
                return False
            STATE["last_join_reply"] = now
    return True


def responder_loop():
    while True:
        with STATE_LOCK:
            running = STATE["running"]
        if not running:
            return
        try:
            _, _, event = INCOMING.get(timeout=1)
        except queue.Empty:
            continue

        try:
            if not should_reply(event):
                continue

            with STATE_LOCK:
                cfg = dict(STATE["config"])
                behavior = cfg.get("behavior") or default_behavior()
                wait_for = behavior.get("global_cooldown", 3.0) - (time.time() - STATE["last_spoken_at"])
            if wait_for > 0:
                time.sleep(wait_for)

            reply = openrouter_reply(event, cfg)
            if cfg.get("tts_provider") == "openrouter":
                audio_b64, mime = openrouter_speak(reply, cfg)
            else:
                audio_b64, mime = vibi_speak(reply, cfg)
            with STATE_LOCK:
                STATE["last_spoken_at"] = time.time()
            push_event("ai", reply, name=event.get("name", ""), audio_b64=audio_b64, mime=mime)
        except Exception as exc:
            msg = redact_error(exc)
            with STATE_LOCK:
                STATE["last_error"] = msg
            push_event("error", msg)


def start_tiktok_client(username):
    client = TikTokLiveClient(unique_id=f"@{username}")
    with STATE_LOCK:
        STATE["client"] = client

    def name_of(event):
        user = getattr(event, "user", None)
        nickname = (getattr(user, "nickname", None) or "").strip()
        username = (getattr(user, "unique_id", None) or "").strip()
        return nickname or username or "مشاهد"

    @client.on(ConnectEvent)
    async def on_connect(event):
        with STATE_LOCK:
            STATE["tiktok"] = "LIVE"
            STATE["room_id"] = client.room_id
        push_event("status", f"متصل ببث @{event.unique_id} | Room ID: {client.room_id}")

    @client.on(DisconnectEvent)
    async def on_disconnect(_):
        with STATE_LOCK:
            STATE["tiktok"] = "OFFLINE"
        push_event("status", "انقطع اتصال TikTok")

    @client.on(CommentEvent)
    async def on_comment(event):
        name = name_of(event)
        text = (getattr(event, "comment", "") or "").strip()
        push_event("comment", f"{name}: {text}", name=name)
        if text:
            enqueue_event({"type": "comment", "name": name, "text": text})

    @client.on(JoinEvent)
    async def on_join(event):
        name = name_of(event)
        push_event("join", f"{name} دخل البث", name=name)
        enqueue_event({"type": "join", "name": name, "text": ""})

    @client.on(FollowEvent)
    async def on_follow(event):
        name = name_of(event)
        push_event("follow", f"{name} تابع الحساب", name=name)
        enqueue_event({"type": "follow", "name": name, "text": ""})

    @client.on(ShareEvent)
    async def on_share(event):
        name = name_of(event)
        push_event("share", f"{name} شارك البث", name=name)
        enqueue_event({"type": "share", "name": name, "text": ""})

    @client.on(GiftEvent)
    async def on_gift(event):
        gift = getattr(event, "gift", None)
        if getattr(gift, "streakable", False) and getattr(event, "streaking", False):
            return
        name = name_of(event)
        gift_name = getattr(gift, "name", "هدية")
        count = getattr(event, "repeat_count", 1) or 1
        text = f"{count}x {gift_name}"
        push_event("gift", f"{name} أرسل {text}", name=name)
        enqueue_event({"type": "gift", "name": name, "text": text})

    @client.on(LikeEvent)
    async def on_like(event):
        count = int(getattr(event, "count", 0) or 0)
        if not count:
            return
        with STATE_LOCK:
            STATE["like_bucket"] += count
            behavior = (STATE["config"].get("behavior") or default_behavior())
            threshold = int(behavior.get("like_threshold", 100))
            speak_likes = bool(behavior.get("respond_likes", False))
            bucket = STATE["like_bucket"]
            if speak_likes and bucket >= threshold:
                STATE["like_bucket"] = 0
            else:
                bucket = 0
        if bucket:
            push_event("like", f"دفعة لايكات: {bucket}")
            enqueue_event({"type": "like", "name": "الجمهور", "text": str(bucket)})

    try:
        client.run(process_connect_events=False)
    except UserOfflineError:
        with STATE_LOCK:
            STATE["tiktok"] = "OFFLINE"
            STATE["room_id"] = None
            STATE["running"] = False
        push_event("error", "الحساب مو باث LIVE الآن")
    except Exception as exc:
        msg = redact_error(exc)
        with STATE_LOCK:
            STATE["tiktok"] = "ERROR"
            STATE["last_error"] = msg
            STATE["running"] = False
        push_event("error", msg)


@app.before_request
def protect_control_api():
    if not request.path.startswith("/api/"):
        return None
    expected = os.getenv("CONTROL_TOKEN", "")
    if not expected:
        return jsonify({"ok": False, "error": "CONTROL_TOKEN غير مضبوط على السيرفر"}), 503
    received = request.headers.get("X-Control-Token", "")
    if not hmac.compare_digest(received, expected):
        return jsonify({"ok": False, "error": "غير مصرح"}), 401
    return None


@app.get("/")
def index():
    return jsonify({
        "ok": True,
        "name": "TikTok AI LIVE backend",
        "control": "Use the Vercel control panel",
    })


@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "tiktok-ai-live-cyber"})


@app.get("/api/status")
def api_status():
    with STATE_LOCK:
        p = (STATE["config"].get("persona") or {})
        return jsonify({
            "running": STATE["running"],
            "tiktok": STATE["tiktok"],
            "room_id": STATE["room_id"],
            "username": STATE["username"],
            "agent_name": p.get("name", "سايبر"),
            "preset_id": STATE["config"].get("preset_id", "cyber_gamer"),
            "preset_label": STATE["config"].get("preset_label", "سايبر — قيمر سعودي حماسي"),
            "tts_voice": STATE["config"].get("tts_voice", "adam"),
            "voice_gender": STATE["config"].get("voice_gender", "ذكر"),
            "last_error": STATE["last_error"],
        })


@app.get("/api/events")
def api_events():
    try:
        since = int(request.args.get("since", "0"))
    except ValueError:
        since = 0
    with STATE_LOCK:
        rows = [x for x in EVENTS if x["id"] > since]
    return jsonify({"events": rows[-100:]})


@app.post("/api/test")
def api_test():
    data = request.get_json(silent=True) or {}
    try:
        username = clean_username(data.get("tiktok", ""))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    cfg = build_config(data)
    result = {"ok": True, "username": username}

    try:
        ok, msg = test_openrouter(cfg["openrouter_key"])
        result["openrouter"] = {"ok": ok, "message": msg}
    except Exception as exc:
        result["openrouter"] = {"ok": False, "message": redact_error(exc)}

    result["tts"] = {
        "ok": bool(cfg.get("tts_voice")),
        "message": f'ElevenLabs v4 Turbo | {cfg.get("tts_voice")} | {cfg.get("voice_gender")}' if cfg.get("tts_voice") else "صوت ElevenLabs ناقص",
        "model": cfg.get("tts_model", "elevenlabs/eleven-v4-turbo"),
    }

    if cfg.get("vibi_key"):
        try:
            ok, msg = test_vibi(cfg["vibi_key"])
            result["vibi"] = {"ok": ok, "message": msg}
        except Exception as exc:
            result["vibi"] = {"ok": False, "message": redact_error(exc)}
    else:
        result["vibi"] = {"ok": True, "message": "Vibi احتياطي فقط"}

    try:
        live = tiktok_live_check(username)
        result["tiktok"] = {
            "ok": bool(live),
            "message": "الحساب باث LIVE" if live else "الحساب مو باث الآن",
        }
    except Exception as exc:
        result["tiktok"] = {"ok": False, "message": redact_error(exc)}

    result["ok"] = all(x.get("ok") for x in [result["openrouter"], result["tts"], result["tiktok"]])
    return jsonify(result)


@app.get("/api/presets")
def api_presets():
    rows = []
    for preset_id in CHARACTER_PRESETS:
        p = resolved_preset(preset_id)
        rows.append({
            "id": p["id"],
            "label": p["label"],
            "type": p["type"],
            "voice": p["voice"],
            "voice_label": p["voice_label"],
            "voice_gender": p["voice_gender"],
            "tts_model": "elevenlabs/eleven-v4-turbo",
            "voice_language": "ar",
            "persona": p["persona"],
            "behavior": p["behavior"],
        })
    return jsonify({"ok": True, "presets": rows})


@app.post("/api/configure")
def api_configure():
    data = request.get_json(silent=True) or {}
    cfg = build_config(data)
    with STATE_LOCK:
        current = STATE.get("config") or {}
        if not cfg.get("openrouter_key"):
            cfg["openrouter_key"] = current.get("openrouter_key", "")
        if not cfg.get("vibi_key"):
            cfg["vibi_key"] = current.get("vibi_key", "")
        STATE["config"] = cfg
        running = STATE["running"]
    push_event("status", f'تم تفعيل شخصية {cfg["persona"].get("name", "سايبر")} | الصوت {cfg.get("tts_voice", "")}')
    return jsonify({
        "ok": True,
        "running": running,
        "preset_id": cfg["preset_id"],
        "character": cfg["persona"].get("name", "سايبر"),
        "voice": cfg.get("tts_voice", ""),
        "voice_gender": cfg.get("voice_gender", ""),
    })


@app.post("/api/preview")
def api_preview():
    data = request.get_json(silent=True) or {}
    cfg = build_config(data)
    if not cfg["openrouter_key"] or not cfg["tts_voice"]:
        return jsonify({"ok": False, "error": "حط OpenRouter Key وصوت ElevenLabs"}), 400
    event_type = (data.get("preview_type") or "comment").strip()
    preview_text = (data.get("preview_text") or "السلام عليكم").strip()
    event = {"type": event_type, "name": "مشاهد تجريبي", "text": preview_text}
    try:
        reply = openrouter_reply(event, cfg)
        if cfg.get("tts_provider") == "openrouter":
            audio_b64, mime = openrouter_speak(reply, cfg)
        else:
            audio_b64, mime = vibi_speak(reply, cfg)
        return jsonify({"ok": True, "reply": reply, "audio_b64": audio_b64, "mime": mime})
    except Exception as exc:
        return jsonify({"ok": False, "error": redact_error(exc)}), 502


@app.post("/api/start")
def api_start():
    data = request.get_json(silent=True) or {}
    try:
        username = clean_username(data.get("tiktok", ""))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    cfg = build_config(data)
    if not cfg["openrouter_key"] or not cfg["tts_voice"]:
        return jsonify({"ok": False, "error": "حط OpenRouter Key واختر صوت الشخصية"}), 400

    with STATE_LOCK:
        if STATE["running"]:
            return jsonify({"ok": False, "error": "الوكيل شغال أصلًا. وقفه أول."}), 409
        STATE["running"] = True
        STATE["username"] = username
        STATE["tiktok"] = "CONNECTING"
        STATE["room_id"] = None
        STATE["last_error"] = ""
        STATE["config"] = cfg
        STATE["last_join_reply"] = 0.0
        STATE["last_spoken_at"] = 0.0
        STATE["like_bucket"] = 0
        RECENT_INCOMING.clear()

    while True:
        try:
            INCOMING.get_nowait()
        except queue.Empty:
            break

    push_event("status", f"جاري الاتصال بـ @{username}...")

    responder = threading.Thread(target=responder_loop, daemon=True, name="responder")
    tiktok = threading.Thread(target=start_tiktok_client, args=(username,), daemon=True, name="tiktok")

    with STATE_LOCK:
        STATE["responder_thread"] = responder
        STATE["tiktok_thread"] = tiktok

    responder.start()
    tiktok.start()
    return jsonify({
        "ok": True,
        "message": "بدأ الاتصال",
        "agent_name": cfg["persona"].get("name", "سايبر"),
    })


@app.post("/api/stop")
def api_stop():
    with STATE_LOCK:
        STATE["running"] = False
        client = STATE.get("client")
        STATE["tiktok"] = "STOPPING"

    if client is not None:
        try:
            loop = getattr(client, "_asyncio_loop", None)
            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(client.disconnect(), loop)
        except Exception:
            pass

    with STATE_LOCK:
        STATE["tiktok"] = "OFFLINE"
        STATE["room_id"] = None
        STATE["client"] = None
    push_event("status", "تم إيقاف الوكيل")
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, threaded=True)
