# TikTok AI LIVE — Render

واجهة خفيفة تربط TikTok LIVE مع OpenRouter وVibi.

## Render
- Runtime: Python
- Build: `pip install -r tiktok-live-ai-render/requirements.txt`
- Start: `gunicorn --workers 1 --threads 8 --timeout 0 --bind 0.0.0.0:$PORT tiktok-live-ai-render.app:app`

المفاتيح تُدخل من الواجهة ولا تُحفظ في المستودع.
