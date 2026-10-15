# TikTok LIVE Control Panel

واجهة Vercel للتحكم في شخصية وكيل TikTok LIVE الموجود على Render.

- الشخصية واللهجة والنبرة
- قواعد التعليقات والدخول والهدايا والمتابعات والمشاركة واللايكات
- OpenRouter + Vibi
- اختبار شخصية وصوت
- تشغيل وإيقاف البث ومشاهدة الأحداث

Secrets are configured on Vercel/Render environment variables, not committed.

## Virtual Host v2

The added **المضيف الذكي** tab manages mood, crowd behavior, topic profiles, automatic personality dwell, proactive speech and failover. **المحاكاة** sends fake events through the actual pipeline without a TikTok connection. Its fixture checkbox is an explicit offline test with a fixed reply and silent audio.

Use “نسخ رابط مشغل LIVE” after saving/testing your OpenRouter key. Paste the resulting player URL into LIVE Studio. Its audio-only capability lasts 30 days; keep it private. `player.html?controls=1` exposes optional player controls. The control panel plays Preview audio only, never LIVE audio.

Custom presets are stored locally with an allowlist of persona/behavior/voice fields. Keys remain in the existing AES-GCM HttpOnly Secure cookie. On a backend release, reopen the panel and press Start to send the saved keys server to server.

Optional `OPERATOR_KEY_SHA256` pins mutation access to the owner's OpenRouter key SHA-256 digest. Without this setting or platform access protection, any valid OpenRouter key can create an operator cookie. The proxy is not a multi-user authentication service.

Validation: `node --test tests/proxy.test.cjs`. The backend operations guide and audit are on the `tiktok-live-ai-render` branch under `docs/`.
