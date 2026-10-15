# Virtual LIVE Host v2

## Deploy targets

- Frontend: `main` / `tiktok-live-control`, existing Vercel project.
- Backend: `tiktok-live-ai-render` / `tiktok-live-ai-render`, existing Render service.
- Keep **one Gunicorn worker and one Render instance**. Scale only after moving shared state, leases and queue to Redis.
- `/health` remains public. All `/api/*` backend endpoints require server-to-server CONTROL_TOKEN.
- Existing encrypted browser cookies remain compatible. Keys never return to the browser or appear in presets/status/health.

## Startup after a backend release

A release restarts the process, so ephemeral config, memory, queue and stats reset. The operator's saved browser cookie remains intact. Open the panel and press Start; the proxy injects saved keys server to server. Hot Configure and character changes never reconnect TikTok. Stop cancels its loop and fences provider results. If a synchronous provider request is still finishing, Start refuses to create another worker until it exits.

## Player

Click “نسخ رابط مشغل LIVE” in the authenticated panel, then use that URL in LIVE Studio. The signed fragment capability permits audio polling and heartbeat only; it is not CONTROL_TOKEN or a provider key. It expires after 30 days. Treat the URL as private. An operator cookie also works when opening player.html in the same browser. `?controls=1` shows optional playback controls. Only the lease owner plays audio; another player waits until its 10-second heartbeat lease expires. Live metadata never includes audio; only Preview plays in the panel.

## Failover and voices

Three configurable OpenRouter chat models have bounded HTTP timeouts and a 30-second circuit breaker. TTS uses the configured primary and optional backup. OpenRouter speech uses its documented `/audio/speech` API and provider passthrough settings; a 400 retries without advanced voice settings. No unverified expressive tags are sent. Vibi's existing task API is retained. A female Vibi backup needs `VIBI_FEMALE_VOICE_ID`; `VIBI_MALE_VOICE_ID` overrides the existing male ID. No backup is called without its key. Existing aliases are shared by some characters; unique voices are not promised.

## Defaults and memory

Manual personality, automatic mood, crowd mode enabled, proactive host disabled. Automatic changes have a 15-minute minimum duration (configurable 10–60 minutes). All time-of-day decisions use UTC. Viewer memory defaults to 12 hours, supports 6–24 hours, and holds at most 5,000 viewers. Histories are bounded and prompts use only the last two messages and one reply. Contact information and common sensitive declarations are redacted; no demographic or personal inferences are stored. A janitor removes expired memories even while the host is stopped. All state is intentionally ephemeral.

## Simulation and validation

Stop the host before starting Simulation. Normal Simulation runs the real heuristic/dedupe/priority/AI/TTS/audio pipeline without TikTok. The explicitly labeled fixture option uses a fixed response and a silent WAV; it verifies transport, not provider audio quality. Burst injects up to 80 comments through the same ingestion function.

Run backend tests from the repository root:

```sh
python -m pip install -r tiktok-live-ai-render/requirements.txt pytest
python -m pytest -q tiktok-live-ai-render/tests
```

Frontend proxy tests: `node --test tiktok-live-control/tests/proxy.test.cjs` on `main`.

The public Vercel proxy validates a supplied/saved OpenRouter key before granting control; it is not a multi-user access-control system. Optional `OPERATOR_KEY_SHA256` pins control to the owner's key digest. When not pinned, anyone with their own valid OpenRouter key can establish an operator cookie; use platform access protection for owner isolation. Read-only status/presets/metrics remain public. Same-origin mutation checks and rate limits reduce accidental abuse but do not replace authentication.
