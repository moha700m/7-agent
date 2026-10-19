import http from "node:http";
import fsp from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import Busboy from "busboy";
import AdmZip from "adm-zip";

const execFileAsync = promisify(execFile);
const PORT = Number(process.env.PORT || 10000);
const ACCESS_KEY = process.env.ACCESS_KEY || "";
const MAX_UPLOAD = 25 * 1024 * 1024;
const MAX_UNZIPPED = 120 * 1024 * 1024;
const REA_BIN = path.join(process.cwd(), "node_modules", ".bin", "rea");

function send(res, status, body, type = "application/json; charset=utf-8") {
  res.writeHead(status, {
    "content-type": type,
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
  });
  res.end(typeof body === "string" ? body : JSON.stringify(body));
}

function html(keyPrefill = "") {
  const safeKey = keyPrefill.replace(/[&<>"']/g, "");
  return `<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>REA Cloud</title>
<style>
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:#0b0d10;color:#f5f7fb;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.wrap{max-width:760px;margin:auto;padding:24px 16px 60px}.card{background:#141820;border:1px solid #252b36;border-radius:20px;padding:18px;margin:14px 0;box-shadow:0 12px 36px #0005}h1{font-size:28px;margin:0 0 8px}.muted{color:#aab3c2;line-height:1.7}.pill{display:inline-block;padding:7px 10px;border-radius:999px;background:#1b2431;border:1px solid #2b394c;font-size:13px;margin:4px 0}label{display:block;margin:14px 0 7px;font-weight:700}input,button{width:100%;font:inherit;border-radius:14px;border:1px solid #303848;background:#0d1117;color:#fff;padding:13px}input[type=file]{padding:11px}button{background:#fff;color:#111;font-weight:800;margin-top:14px;border:0;cursor:pointer}button:disabled{opacity:.55}pre{white-space:pre-wrap;word-break:break-word;background:#090c10;border:1px solid #252b36;border-radius:14px;padding:13px;max-height:55vh;overflow:auto;font-size:12px;direction:ltr;text-align:left}.ok{color:#82e6a8}.bad{color:#ff9b9b}.small{font-size:12px}
</style>
</head>
<body><main class="wrap">
<div class="card">
<h1>REA Cloud</h1>
<div class="pill">Reverse Engineer Anything · Static JS/Electron</div>
<p class="muted">نسخة سحابية للجوال. ارفع ZIP / ASAR / ملف JavaScript أو حط رابط GitHub عام. التحليل هنا <b>ثابت فقط</b> وما يشغّل الملف المرفوع.</p>
</div>
<div class="card">
<label>مفتاح الدخول</label>
<input id="key" type="password" autocomplete="off" value="${safeKey}" placeholder="ACCESS KEY">
<label>رفع ملف من الآيفون</label>
<input id="file" type="file" accept=".zip,.asar,.js,.mjs,.cjs,.json,.html">
<label>أو رابط GitHub عام</label>
<input id="url" type="url" placeholder="https://github.com/owner/repo">
<button id="go">حلّل بـ REA</button>
<p id="status" class="muted small"></p>
</div>
<div class="card">
<strong>النتيجة</strong>
<pre id="out">جاهز.</pre>
<button id="download" style="display:none">تنزيل التقرير JSON</button>
</div>
<script>
const $=s=>document.querySelector(s); let last="";
const qs=new URLSearchParams(location.search); if(qs.get("key")) $("#key").value=qs.get("key");
if(sessionStorage.reaKey && !$("#key").value) $("#key").value=sessionStorage.reaKey;
$("#go").onclick=async()=>{
  const key=$("#key").value.trim(); sessionStorage.reaKey=key;
  const file=$("#file").files[0]; const url=$("#url").value.trim();
  if(!file && !url){$("#status").textContent="اختر ملف أو رابط GitHub.";return}
  const fd=new FormData(); if(file) fd.append("file",file); if(url) fd.append("url",url);
  $("#go").disabled=true; $("#status").textContent="جاري التحليل…"; $("#out").textContent="REA يعمل الآن…"; $("#download").style.display="none";
  try{
    const r=await fetch("/analyze?key="+encodeURIComponent(key),{method:"POST",body:fd});
    const data=await r.json();
    if(!r.ok) throw new Error(data.error||"فشل التحليل");
    last=data.report||JSON.stringify(data,null,2);
    $("#out").textContent=last;
    $("#status").innerHTML='<span class="ok">تم التحليل.</span>'+ (data.truncated?" التقرير مختصر بسبب الحجم.":"");
    $("#download").style.display="block";
  }catch(e){$("#status").innerHTML='<span class="bad">'+e.message+'</span>';$("#out").textContent=e.stack||e.message}
  finally{$("#go").disabled=false}
};
$("#download").onclick=()=>{
  const b=new Blob([last],{type:"application/json"}); const a=document.createElement("a"); a.href=URL.createObjectURL(b); a.download="rea-report.json"; a.click(); setTimeout(()=>URL.revokeObjectURL(a.href),1000);
};
</script>
</main></body></html>`;
}

function authorized(req, url) {
  if (!ACCESS_KEY) return true;
  return url.searchParams.get("key") === ACCESS_KEY || req.headers["x-access-key"] === ACCESS_KEY;
}

function safeName(name) {
  const base = path.basename(name || "upload.bin").replace(/[^a-zA-Z0-9._-]/g, "_");
  return base.slice(0, 120) || "upload.bin";
}

async function parseMultipart(req) {
  return await new Promise((resolve, reject) => {
    let fileData = null, fileName = "", urlField = "";
    let total = 0, done = false;
    const bb = Busboy({ headers: req.headers, limits: { fileSize: MAX_UPLOAD, files: 1, fields: 4 } });
    bb.on("file", (_name, stream, info) => {
      fileName = safeName(info.filename);
      const chunks = [];
      stream.on("data", chunk => {
        total += chunk.length;
        if (total > MAX_UPLOAD) {
          stream.unpipe();
          stream.resume();
          if (!done) { done = true; reject(new Error("حجم الملف أكبر من 25MB")); }
          return;
        }
        chunks.push(chunk);
      });
      stream.on("limit", () => {
        if (!done) { done = true; reject(new Error("حجم الملف أكبر من 25MB")); }
      });
      stream.on("end", () => { if (!done) fileData = Buffer.concat(chunks); });
    });
    bb.on("field", (name, value) => { if (name === "url") urlField = value.trim(); });
    bb.on("error", err => { if (!done) { done = true; reject(err); } });
    bb.on("close", () => { if (!done) { done = true; resolve({ fileData, fileName, urlField }); } });
    req.pipe(bb);
  });
}

function githubDownloadUrl(input) {
  const u = new URL(input);
  if (u.protocol !== "https:") throw new Error("الرابط لازم HTTPS");
  if (u.hostname === "raw.githubusercontent.com") return { url: input, filename: path.basename(u.pathname) || "source.js" };
  if (u.hostname !== "github.com") throw new Error("حاليًا الروابط مسموحة من GitHub فقط");
  const parts = u.pathname.split("/").filter(Boolean);
  if (parts.length < 2) throw new Error("رابط GitHub غير صحيح");
  const owner = parts[0], repo = parts[1].replace(/\.git$/,"");
  let branch = "main";
  const ti = parts.indexOf("tree");
  if (ti >= 0 && parts[ti+1]) branch = parts[ti+1];
  return { url: `https://codeload.github.com/${owner}/${repo}/zip/refs/heads/${encodeURIComponent(branch)}`, filename: `${repo}-${branch}.zip` };
}

async function downloadLimited(input) {
  const info = githubDownloadUrl(input);
  const r = await fetch(info.url, { redirect: "follow", headers: { "user-agent": "REA-Cloud-Mobile/1.0" } });
  if (!r.ok) throw new Error(`فشل تنزيل GitHub: HTTP ${r.status}`);
  const len = Number(r.headers.get("content-length") || 0);
  if (len && len > MAX_UPLOAD) throw new Error("التحميل أكبر من 25MB");
  const reader = r.body.getReader();
  const chunks = []; let total = 0;
  while (true) {
    const { done, value } = await reader.read(); if (done) break;
    total += value.byteLength;
    if (total > MAX_UPLOAD) { try { await reader.cancel(); } catch {} throw new Error("التحميل أكبر من 25MB"); }
    chunks.push(Buffer.from(value));
  }
  return { data: Buffer.concat(chunks), filename: safeName(info.filename) };
}

function inspectZip(zipPath, dest) {
  const zip = new AdmZip(zipPath);
  let total = 0;
  for (const entry of zip.getEntries()) {
    total += Number(entry.header?.size || 0);
    if (total > MAX_UNZIPPED) throw new Error("محتوى ZIP بعد الفك أكبر من 120MB");
    const target = path.resolve(dest, entry.entryName);
    const root = path.resolve(dest) + path.sep;
    if (!target.startsWith(root) && target !== path.resolve(dest)) throw new Error("ZIP يحتوي مسار غير آمن");
  }
  zip.extractAllTo(dest, true, false);
}

async function analyzeBuffer(data, filename) {
  const tmp = await fsp.mkdtemp(path.join(os.tmpdir(), "rea-cloud-"));
  try {
    const uploaded = path.join(tmp, safeName(filename));
    await fsp.writeFile(uploaded, data);
    let target = uploaded;
    const ext = path.extname(uploaded).toLowerCase();

    if (ext === ".zip") {
      const dest = path.join(tmp, "extracted");
      await fsp.mkdir(dest);
      inspectZip(uploaded, dest);
      target = dest;
    } else if (ext === ".asar") {
      target = uploaded;
    } else if ([".js",".mjs",".cjs",".json",".html"].includes(ext)) {
      const dest = path.join(tmp, "app");
      await fsp.mkdir(dest);
      await fsp.copyFile(uploaded, path.join(dest, path.basename(uploaded)));
      target = dest;
    } else {
      throw new Error("هذه النسخة تدعم ZIP / ASAR / JS / MJS / CJS / JSON / HTML للتحليل الثابت");
    }

    const { stdout, stderr } = await execFileAsync(
      REA_BIN,
      ["analyze-javascript-application", target, "--json"],
      {
        cwd: process.cwd(),
        timeout: 120000,
        maxBuffer: 12 * 1024 * 1024,
        env: { ...process.env, CI: "1" }
      }
    );
    const combined = (stdout || "").trim();
    const limit = 1500000;
    return {
      report: combined.length > limit ? combined.slice(0, limit) + "\n\n[TRUNCATED]" : combined,
      truncated: combined.length > limit,
      stderr: (stderr || "").slice(0, 4000)
    };
  } finally {
    await fsp.rm(tmp, { recursive: true, force: true }).catch(()=>{});
  }
}

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, `http://${req.headers.host || "localhost"}`);
    if (req.method === "GET" && url.pathname === "/health") {
      return send(res, 200, { ok: true, service: "rea-cloud", rea: "6.7.0", mode: "static-javascript-electron" });
    }
    if (req.method === "GET" && url.pathname === "/") {
      return send(res, 200, html(url.searchParams.get("key") || ""), "text/html; charset=utf-8");
    }
    if (req.method === "POST" && url.pathname === "/analyze") {
      if (!authorized(req, url)) return send(res, 401, { error: "مفتاح الدخول غير صحيح" });
      const ctype = req.headers["content-type"] || "";
      if (!ctype.startsWith("multipart/form-data")) return send(res, 415, { error: "ارسل multipart/form-data" });
      const { fileData, fileName, urlField } = await parseMultipart(req);
      let data = fileData, filename = fileName;
      if (!data && urlField) {
        const dl = await downloadLimited(urlField); data = dl.data; filename = dl.filename;
      }
      if (!data) return send(res, 400, { error: "ارفع ملف أو ضع رابط GitHub عام" });
      const result = await analyzeBuffer(data, filename || "upload.zip");
      return send(res, 200, { ok: true, filename, ...result });
    }
    return send(res, 404, { error: "Not found" });
  } catch (err) {
    const msg = err?.killed ? "انتهت مهلة التحليل (120 ثانية)" : String(err?.message || err);
    const detail = String(err?.stderr || "").slice(0, 4000);
    console.error(err);
    return send(res, 500, { error: msg, detail });
  }
});

server.listen(PORT, "0.0.0.0", () => {
  console.log(`REA Cloud listening on ${PORT}`);
});
