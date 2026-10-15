# Existing system audit (2026-10-15)

- Repository: `moha700m/7-agent`; frontend `main`, backend `tiktok-live-ai-render`.
- Vercel project `tiktok-live-control`: buildless HTML/JS, Node proxy `/api/backend`.
- Render `tiktok-ai-live-cyber`: Python 3 / Flask, TikTokLive 7.0.1, Gunicorn **one worker, one instance**, eight HTTP threads. Keep that process topology while using in-memory state.
- Existing endpoints: status, events, presets, configure, preview, test, start, stop. Preserve payloads and response fields.
- AES-GCM HttpOnly Secure cookie holds provider keys; CONTROL_TOKEN is injected server to server. Preserve existing secrets and encryption compatibility.
- Existing 12 personas and voices must remain available. Hot configuration currently doesn't reconnect TikTok; preserve that property.

## Risks and implementation order

1. Add bounded memory/metrics and extract cards/config/providers before pipeline changes.
2. Replace static queue priorities and exact dedupe; enforce event TTL and inspect every decision.
3. Add mood, crowd and provider-compatible voice styles without assuming support for speech tags.
4. Add opt-in auto personality/proactive speech; require a healthy idle player for proactive output.
5. Own the asyncio loop explicitly; one lifecycle lock, cancelable stop, generation fencing and bounded reconnect backoff. Never start a second client while an old thread is alive.
6. Extend the existing dashboard/proxy; keep LIVE audio in player.html only.
7. Run simulation through the same ingestion/responder pipeline, with an explicit offline fixture mode for tests.
8. Test offline before deployment; verify production status and deployment commits. A backend release restarts the process and requires the browser to start a new connection with its saved keys. Production was LIVE during the audit; coordinate that interruption with the owner.

## Confirmed defects

- Stop and immediate Start could overlap old worker work; in-flight results could still publish after Stop.
- No reconnect, event expiration, playback acknowledgement or dynamic queue selection.
- Every join incurred an LLM call when selected; voice tags were prefixed unconditionally.
- `/api/test` reported TTS success based only on a nonempty voice, without making a TTS request.
- Errors could include unredacted third-party details; normalization used a literal escaped whitespace regex.
- Public Vercel proxy forwards a server credential for anyone. Add same-origin mutation checks and require a saved/supplied operator key for mutations; the audio player needs a narrowly scoped, separate capability.

No credential values were read, changed or recorded in this audit.
