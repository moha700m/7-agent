module.exports = async function handler(req, res) {
  const allowed = new Set(["status","events","test","start","stop","preview"]);
  const path = String(req.query.path || "");
  if (!allowed.has(path)) return res.status(400).json({ error: "مسار غير مسموح" });

  const base = process.env.RENDER_API_BASE;
  const token = process.env.CONTROL_TOKEN;
  if (!base || !token) return res.status(503).json({ error: "إعدادات الربط غير مكتملة" });

  const target = new URL("/api/" + path, base);
  if (path === "events" && req.query.since) target.searchParams.set("since", String(req.query.since));

  const headers = { "X-Control-Token": token };
  const init = { method: req.method || "GET", headers };
  if (!["GET","HEAD"].includes(init.method)) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(req.body || {});
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