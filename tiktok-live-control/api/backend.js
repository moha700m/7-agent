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
  const allowed = new Set(["status","events","test","start","stop","preview","configure","presets","secret_status"]);
  const path = String(req.query.path || "");
  if (!allowed.has(path)) return res.status(400).json({ error: "مسار غير مسموح" });

  const base = process.env.RENDER_API_BASE;
  const token = process.env.CONTROL_TOKEN;
  if (!base || !token) return res.status(503).json({ error: "إعدادات الربط غير مكتملة" });

  const cookies = parseCookies(req);
  let savedSecrets = decryptSecrets(cookies.cyber_secrets, token);

  if (path === "secret_status") {
    return res.status(200).json({
      openrouter_saved: !!savedSecrets.openrouter_key,
      vibi_saved: !!savedSecrets.vibi_key
    });
  }

  const target = new URL("/api/" + path, base);
  if (path === "events" && req.query.since) target.searchParams.set("since", String(req.query.since));

  const headers = { "X-Control-Token": token };
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