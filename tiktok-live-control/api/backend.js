const crypto = require("crypto");

function parseCookies(req) {
  const out = {};
  const raw = String(req.headers.cookie || "");
  for (const part of raw.split(";")) {
    const i = part.indexOf("=");
    if (i < 0) continue;
    const k = part.slice(0, i).trim();
    const v = part.slice(i + 1).trim();
    if (k) out[k] = v;
  }
  return out;
}

function keyFromSecret(secret) {
  return crypto.createHash("sha256").update(String(secret)).digest();
}

function encryptSecrets(data, secret) {
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv("aes-256-gcm", keyFromSecret(secret), iv);
  const enc = Buffer.concat([cipher.update(JSON.stringify(data), "utf8"), cipher.final()]);
  const tag = cipher.getAuthTag();
  return Buffer.concat([iv, tag, enc]).toString("base64url");
}

function decryptSecrets(token, secret) {
  try {
    if (!token) return {};
    const raw = Buffer.from(token, "base64url");
    const iv = raw.subarray(0, 12);
    const tag = raw.subarray(12, 28);
    const enc = raw.subarray(28);
    const decipher = crypto.createDecipheriv("aes-256-gcm", keyFromSecret(secret), iv);
    decipher.setAuthTag(tag);
    const dec = Buffer.concat([decipher.update(enc), decipher.final()]).toString("utf8");
    const obj = JSON.parse(dec);
    return obj && typeof obj === "object" ? obj : {};
  } catch {
    return {};
  }
}

function saveSecretCookie(res, secrets, secret) {
  const token = encryptSecrets(secrets, secret);
  const cookie = [
    "cyber_secrets=" + token,
    "Path=/",
    "HttpOnly",
    "Secure",
    "SameSite=Lax",
    "Max-Age=31536000"
  ].join("; ");
  res.setHeader("Set-Cookie", cookie);
}

module.exports = async function handler(req, res) {
  const allowed = new Set(["status","events","test","start","stop","restart","preview","configure","presets","secret_status","health/details","metrics","metrics/reset","profiles","control","simulation/start","simulate","audio/events","player/heartbeat","player_token","player_bootstrap","self-host/status","self-host/configure","self-host/new-topic","self-host/stop-topic","self-host/speak-now","self-host/pause","self-host/resume"]);
  res.setHeader("Cache-Control", "no-store");
  const path = String(req.query.path || "");
  if (!allowed.has(path)) return res.status(400).json({ error: "مسار غير مسموح" });

  const base = process.env.RENDER_API_BASE;
  const token = process.env.CONTROL_TOKEN;
  if (!base || !token) return res.status(503).json({ error: "إعدادات الربط غير مكتملة" });

  const readPaths = new Set(["status","events","presets","secret_status","health/details","metrics","profiles","audio/events","player_token","player_bootstrap","self-host/status"]);
  if ((readPaths.has(path) && req.method !== "GET") || (!readPaths.has(path) && req.method !== "POST")) {
    return res.status(405).json({error:"طريقة غير مسموحة"});
  }
  if (req.method === "POST") {
    const origin = req.headers.origin;
    const expectedHost = req.headers["x-forwarded-host"] || req.headers.host;
    try {if (origin && new URL(origin).host !== expectedHost) return res.status(403).json({error:"مصدر الطلب غير مسموح"});} catch {return res.status(403).json({error:"مصدر الطلب غير صالح"});}
    if (req.headers["sec-fetch-site"] === "cross-site") return res.status(403).json({error:"مصدر الطلب غير مسموح"});
  }
  const cookies = parseCookies(req);
  let savedSecrets = decryptSecrets(cookies.cyber_secrets, token);

  if (path === "secret_status") {
    return res.status(200).json({
      openrouter_saved: !!savedSecrets.openrouter_key,
      vibi_saved: !!savedSecrets.vibi_key
    });
  }

  const sign = value => crypto.createHmac("sha256", token).update(value).digest("base64url");
  if (path === "player_bootstrap") {
    // Public, player-only capability. It cannot call operator/control endpoints.
    // This keeps the plain player.html URL working inside TikTok LIVE Studio.
    const claims = Buffer.from(JSON.stringify({
      exp: Date.now() + 24 * 60 * 60 * 1000,
      nonce: crypto.randomBytes(12).toString("hex"),
      scope: "player"
    })).toString("base64url");
    return res.status(200).json({token:claims+"."+sign("player:"+claims),expires_hours:24});
  }
  const playerCapability = String(req.headers["x-player-token"] || "");
  const [playerPayload, playerSignature] = playerCapability.split(".");
  let playerAllowed = false;
  try {
    const expected = sign("player:" + playerPayload);
    const claims = JSON.parse(Buffer.from(playerPayload, "base64url").toString());
    playerAllowed = playerSignature && playerSignature.length === expected.length && crypto.timingSafeEqual(Buffer.from(playerSignature),Buffer.from(expected)) && claims.exp > Date.now();
  } catch {}
  const playerPaths = new Set(["audio/events","player/heartbeat"]);
  // Keep an already-open TikTok LIVE Studio browser source working after upgrades.
  // Only same-origin requests originating from player.html get this narrow player access.
  let sameOriginPlayer = false;
  try {
    const ref = req.headers.referer ? new URL(req.headers.referer) : null;
    const expectedHost = req.headers["x-forwarded-host"] || req.headers.host;
    sameOriginPlayer = !!ref && ref.host === expectedHost && /\/player\.html$/.test(ref.pathname);
  } catch {}
  if (playerPaths.has(path) && sameOriginPlayer) playerAllowed = true;
  const incoming = req.body && typeof req.body === "object" && !Array.isArray(req.body) ? req.body : {};
  const candidate = typeof incoming.openrouter_key === "string" ? incoming.openrouter_key.trim() : "";
  let operator = !!savedSecrets.openrouter_key;
  if ((req.method === "POST" || path === "player_token" || playerPaths.has(path)) && !(playerPaths.has(path) && playerAllowed)) {
    const key = candidate || savedSecrets.openrouter_key;
    if (!key) return res.status(401).json({error:"اختبر مفتاح OpenRouter أو احفظه لفتح أدوات التحكم"});
    const pinnedHash = process.env.OPERATOR_KEY_SHA256;
    if (pinnedHash) {
      const digest=crypto.createHash("sha256").update(key).digest("hex");
      if (digest!==pinnedHash.toLowerCase()) return res.status(403).json({error:"هذا المفتاح غير مخول للتحكم بهذه اللوحة"});
    }
    // Validate the operator credential before issuing a trusted encrypted cookie.
    if (candidate || !savedSecrets.validated_at || Date.now()-savedSecrets.validated_at > 28800000) {
      try {
        const check = await fetch("https://openrouter.ai/api/v1/key", {headers:{Authorization:"Bearer " + key},signal:AbortSignal.timeout(12000)});
        if (!check.ok) return res.status(401).json({error:"تعذر التحقق من مفتاح OpenRouter"});
        savedSecrets.openrouter_key = key;
        savedSecrets.validated_at = Date.now();
        saveSecretCookie(res, savedSecrets, token);
      } catch {return res.status(502).json({error:"تعذر التحقق من صلاحية التحكم الآن"});}
    }
    operator = true;
  }
  if (path === "player_token") {
    if (!operator) return res.status(401).json({error:"احفظ مفتاحك أولًا"});
    const claims = Buffer.from(JSON.stringify({exp:Date.now()+30*86400000,nonce:crypto.randomBytes(12).toString("hex")})).toString("base64url");
    return res.status(200).json({token:claims+"."+sign("player:"+claims),expires_days:30});
  }
  const target = new URL("/api/" + path, base);
  if (["events","audio/events"].includes(path)) {
    for (const key of ["since","initialize","player_id"]) if (req.query[key] !== undefined) target.searchParams.set(key,String(req.query[key]));
  }

  const headers = { "X-Control-Token": token, "X-Operator-Id": crypto.createHash("sha256").update(savedSecrets.openrouter_key || "player").digest("hex").slice(0,24) };
  const init = { method: req.method || "GET", headers };

  if (!["GET","HEAD"].includes(init.method)) {
    headers["Content-Type"] = "application/json";
    const body = (req.body && typeof req.body === "object") ? { ...req.body } : {};

    const suppliedOpenRouter = typeof body.openrouter_key === "string" ? body.openrouter_key.trim() : "";
    const suppliedVibi = typeof body.vibi_key === "string" ? body.vibi_key.trim() : "";

    if (suppliedOpenRouter) savedSecrets.openrouter_key = suppliedOpenRouter;
    if (suppliedVibi) savedSecrets.vibi_key = suppliedVibi;

    if (suppliedOpenRouter || suppliedVibi) {
      saveSecretCookie(res, savedSecrets, token);
    }

    if (!suppliedOpenRouter && savedSecrets.openrouter_key) body.openrouter_key = savedSecrets.openrouter_key;
    if (!suppliedVibi && savedSecrets.vibi_key) body.vibi_key = savedSecrets.vibi_key;

    init.body = JSON.stringify(body);
  }

  try {
    init.signal = AbortSignal.timeout(55000);
    const upstream = await fetch(target, init);
    const text = await upstream.text();
    res.status(upstream.status);
    res.setHeader("Cache-Control", "no-store");
    res.setHeader("Content-Type", upstream.headers.get("content-type") || "application/json; charset=utf-8");
    return res.send(text);
  } catch (error) {
    return res.status(502).json({ error: "تعذر الوصول إلى سيرفر Render" });
  }
};