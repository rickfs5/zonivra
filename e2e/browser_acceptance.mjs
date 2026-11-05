// Browser acceptance for VS-01 using the system Google Chrome over the DevTools protocol (no extra packages).
// Usage: node e2e/browser_acceptance.mjs [baseUrl]   -> writes logs/acceptance/{results.json,*.png}; exit 1 on any failed check.
// SAFETY (cycle-16 MG-PROC-016): these suites create analyses, tamper with stored evidence and delete sessions. They may only run against a server started by
// scripts/run_mvp_e2e.sh with isolated storage (it sets MG_E2E_ISOLATED); never against a user's live server.
if (!process.env.MG_E2E_ISOLATED) { console.error("refusing to run: use `MG_E2E_PORT=<free port> bash scripts/run_mvp_e2e.sh " + process.argv[1] + "` (isolated server and storage)"); process.exit(3); }
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import http from "node:http";
import path from "node:path";

const BASE = process.argv[2] ?? "http://127.0.0.1:8000";
const PORT = 9333;
const OUT = path.resolve("logs/acceptance");
fs.mkdirSync(OUT, { recursive: true });
const profile = fs.mkdtempSync(path.join(os.tmpdir(), "mg-chrome-"));
const chrome = spawn("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", [
  "--headless=new", `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`, "--window-size=1500,1100",
  "--autoplay-policy=no-user-gesture-required", "--no-first-run", "--no-default-browser-check",
  // Best-effort Chrome hardening, NOT network isolation: these flags switch off Chrome's own background services (component updater, sync,
  // Google pings) and make every non-loopback HOST NAME fail to resolve. They do not stop a connection to a literal IP address, and nothing here
  // is enforced by the operating system. Whether any outbound traffic occurred is only observed (by the external watchdog's sampling).
  "--disable-background-networking", "--disable-component-update", "--disable-sync", "--no-pings", "--disable-domain-reliability",
  "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1 , EXCLUDE localhost",
  "about:blank",
], { stdio: "ignore" });

const results = [];
const check = (name, ok, detail = "") => { results.push({ name, ok: !!ok, detail: String(detail) }); console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  — " + detail : ""}`); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitFor(fn, what, timeout = 20000) {
  const t0 = Date.now();
  for (;;) {
    try { const v = await fn(); if (v) return v; } catch { /* retry */ }
    if (Date.now() - t0 > timeout) throw new Error(`timeout waiting for ${what}`);
    await sleep(150);
  }
}

// raw HTTP request that can set the Host header (fetch cannot): used for the hostile-Host / Origin / credential checks
const rawGet = (urlPath, headers = {}, method = "GET") => new Promise((resolve, reject) => {
  const u = new URL(BASE);
  const req = http.request({ host: u.hostname, port: u.port, path: urlPath, method, headers }, (res) => {
    let body = ""; res.on("data", (d) => (body += d)); res.on("end", () => resolve({ status: res.statusCode, headers: res.headers, body }));
  });
  req.on("error", reject); req.end();
});

let ws, nextId = 1;
const pending = new Map();
async function connect() {
  const page = await waitFor(async () => (await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()).find((t) => t.type === "page"), "chrome");
  ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
  ws.onmessage = (m) => { const d = JSON.parse(m.data); if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); } };
}
const send = (method, params = {}) => new Promise((res, rej) => { const id = nextId++; pending.set(id, (d) => (d.error ? rej(new Error(JSON.stringify(d.error))) : res(d.result))); ws.send(JSON.stringify({ id, method, params })); });
const ev = async (expression) => {
  const r = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description ?? "eval error");
  return r.result.value;
};
const shot = async (name) => { const r = await send("Page.captureScreenshot", { format: "png" }); fs.writeFileSync(path.join(OUT, name), Buffer.from(r.data, "base64")); };
const clickText = (txt) => ev(`(()=>{const b=[...document.querySelectorAll('button,a')].find(x=>x.textContent.includes(${JSON.stringify(txt)})&&!x.disabled);if(!b)return false;b.click();return true})()`);
const setRange = (idx, v) => ev(`(()=>{const el=document.querySelectorAll('input[type=range]')[${idx}];const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;s.call(el,${v});el.dispatchEvent(new Event('input',{bubbles:true}));return el.value})()`);
const seek = (sec) => ev(`new Promise(r=>{const v=document.querySelector('video');v.pause();v.addEventListener('seeked',()=>setTimeout(r,400),{once:true});v.currentTime=${sec}})`);

try {
  await connect();
  await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1500, height: 1100, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url: BASE });
  await waitFor(() => ev("!!document.querySelector('[data-testid=nav-video]')"), "navigation");
  await ev("document.querySelector('[data-testid=nav-video]').click()");
  await waitFor(() => ev("document.querySelector('h1')?.textContent.includes('MineGuard')"), "page");
  check("page loads (Spanish UI)", await ev("document.documentElement.lang==='es' && document.body.textContent.includes('No es un sistema de seguridad certificado')"));

  // 1. MP4 loads
  const media = await waitFor(() => ev("(()=>{const v=document.querySelector('video');return v&&v.readyState>=2?{w:v.videoWidth,h:v.videoHeight,d:v.duration}:null})()"), "video metadata", 40000);
  check("registered MP4 loads in the browser", media.w === 3840 && media.h === 2160 && Math.abs(media.d - 24.758) < 0.05, JSON.stringify(media));

  // 2. play / pause
  await ev("document.querySelector('video').currentTime=0");
  await clickText("Reproducir");
  await sleep(1500);
  const t1 = await ev("document.querySelector('video').currentTime");
  await clickText("Pausar");
  const paused = await ev("document.querySelector('video').paused");
  check("video plays and pauses", t1 > 0.8 && paused, `currentTime after 1.5s = ${t1.toFixed(3)}`);

  // 3. analysis through the UI (default stride 3, 30%)
  const hadRunBefore = await ev("document.body.textContent.includes('run_')");
  if (hadRunBefore) {
    const apiTrust = await ev("fetch('/api/runs').then(r=>r.json()).then(r=>r[0].trust)");
    const ui = await ev("({unverified: !!document.querySelector('[data-testid=unverified]'), trusted: !!document.querySelector('[data-testid=trusted]')})");
    const consistent = apiTrust === "unverified" ? ui.unverified && !ui.trusted : ui.trusted && !ui.unverified;
    check("UI trust indicator on load matches the API trust state of the existing run", consistent, `API trust=${apiTrust}; UI=${JSON.stringify(ui)}`);
    if (apiTrust === "unverified") {  // MG-ORIGIN-002: an on-disk model_inference run that has not been verified is never labelled as real inference
      const originTxt = await ev("document.querySelector('[data-testid=origin]')?.textContent || ''");
      check("unverified stored model-inference run: label says NOT verified and never 'Inferencia experimental real'",
        originTxt.includes("NO verificada") && !originTxt.includes("Inferencia experimental real"), originTxt.slice(0, 120));
      check("unverified run: detections are marked as a provisional, unverified view",
        await ev("document.querySelector('[data-testid=detections]') === null || document.querySelector('[data-testid=detections]').getAttribute('data-trusted') === 'no'"));
    }
    await shot("00_run_on_load.png");
  } else {
    check("(no run existed at load; trust-indicator check not applicable)", true);
  }
  const clickedAt = Math.floor(Date.now() / 1000);
  await clickText("Analizar con RF-DETR Nano");
  await waitFor(() => ev("!!document.querySelector('progress')"), "progress bar", 10000);
  check("analysis starts from the UI (progress shown)", true);
  await waitFor(() => ev("(()=>{const p=document.querySelector('progress');return p&&Number(p.value)>0&&Number(p.value)<Number(p.max)})()"), "partial progress", 60000);
  await shot("00_analysis_in_progress.png");
  await waitFor(() => ev("!document.querySelector('progress') && [...document.querySelectorAll('button')].some(b=>b.textContent.includes('Analizar con RF-DETR Nano')&&!b.disabled)"), "analysis completion", 180000);
  await sleep(800);
  const runId = await ev("(document.body.textContent.match(/run_[0-9a-f]{16}/)||[])[0]");
  const bundle = await ev(`fetch('/api/runs/${runId}').then(r=>r.json())`);
  const createdAt = Date.parse(bundle.observation_set.meta.created_utc) / 1000;
  check("analysis completes and the shown run was created after the click", /^run_[0-9a-f]{16}$/.test(runId) && createdAt >= clickedAt - 1,
    `${runId} created_utc=${bundle.observation_set.meta.created_utc} (a run existed before the click: ${hadRunBefore})`);

  // MG-ORIGIN-003: a run created in this session and not yet verified is real inference but is NOT described as verified or reproduced
  const sessTxt = await ev("(document.querySelector('[data-testid=origin]')?.textContent||'') + ' || ' + (document.querySelector('[data-testid=trust-state]')?.textContent||'')");
  check("new session run before verification: UI state is 'session' (API trust=session)",
    (await ev("document.querySelector('[data-testid=trust-state]')?.getAttribute('data-trust')")) === "session" && bundle.trust === "session", `API trust=${bundle.trust}`);
  check("new session run before verification: text says real inference but never 'verificada localmente' or 'reproducida'",
    sessTxt.includes("Inferencia experimental real") && sessTxt.includes("aún sin reverificar") && !sessTxt.includes("verificada localmente") && !sessTxt.includes("ejecutada y reproducida"), sessTxt.slice(0, 160));

  // 4. overlay alignment, solid on analyzed frames: frame 300 is analyzed with stride 3
  const pts = bundle.source.frame_table.pts_us;
  const frame = bundle.observation_set.frames.find((f) => f.frame_index === 300);
  await seek((pts[300] + 500) / 1e6);
  const readout = await ev("document.querySelector('[data-testid=readout]').textContent");
  check("seek lands on frame 300 and reports it analyzed", readout.includes("Fotograma 300") && readout.includes("fue analizado"), readout);
  const rows = await ev("document.querySelectorAll('[data-testid=detections] tr').length");
  const expectedRows = frame.objects.filter((o) => o.confidence.permille >= 300).length;
  check("detection table matches recorded observations for frame 300", rows === expectedRows && rows > 0, `${rows} rows vs ${expectedRows} recorded`);
  await shot("01_video_overlay_frame300.png");

  const top = [...frame.objects].sort((a, b) => b.confidence.permille - a.confidence.permille)[0];
  const edge = await ev(`(()=>{const c=document.querySelector('[data-testid=overlay]');const st=document.querySelector('[data-testid=stage]');
    const W=st.clientWidth,H=st.clientHeight,aw=1280,ah=720,s=Math.min(W/aw,H/ah),rw=aw*s,rh=ah*s,rx=(W-rw)/2,ry=(H-rh)/2;
    const b=${JSON.stringify(top.bbox)};const x=rx+b.x*s, y=ry+(b.y+b.h/2)*s;const d=c.getContext('2d').getImageData(Math.round(x)-1,Math.round(y),3,1).data;
    let ink=0;for(let i=0;i<12;i+=4)ink=Math.max(ink,d[i+3]);
    const all=c.getContext('2d').getImageData(0,0,c.width,c.height).data;let n=0;for(let i=3;i<all.length;i+=4)if(all[i])n++;return {ink,n}})()`);
  check("overlay box edge drawn exactly at the recorded bbox position (frame 300)", edge.ink > 0 && edge.n > 100, JSON.stringify(edge));

  // 5. non-analyzed frame -> dashed overlay + explicit notice
  await seek((pts[301] + 500) / 1e6);
  const r2 = await ev("document.querySelector('[data-testid=readout]').textContent");
  check("non-analyzed frame is labeled as not analyzed (no pretend-exact overlay)", r2.includes("Fotograma 301") && r2.includes("no fue analizado"), r2);

  // 6. frame mode shows the exact analyzed image
  await seek((pts[300] + 500) / 1e6);
  await clickText("Fotograma analizado");
  const img = await waitFor(() => ev("(()=>{const i=document.querySelector('.stage img');return i&&i.complete&&i.naturalWidth?{w:i.naturalWidth,h:i.naturalHeight}:null})()"), "evidence image");
  check("analyzed-frame view serves the stored evidence frame (1280x720)", img.w === 1280 && img.h === 720, JSON.stringify(img));
  await shot("02_analyzed_frame_mode.png");
  await clickText("Video");

  // 7. step to next analyzed frame
  await clickText("Siguiente fotograma analizado");
  await sleep(700);
  const r3 = await ev("document.querySelector('[data-testid=readout]').textContent");
  check("next-analyzed-frame navigation moves to frame 303", r3.includes("Fotograma 303"), r3);

  // 8. export provenance
  const exp = await ev(`fetch(document.querySelector('a[download]').href).then(r=>r.json())`);
  const p = exp.observation_set.producer;
  check("export JSON carries model identity, weights hash and runtime versions",
    exp.observation_set.origin === "model_inference" && p.weights_sha256 === "d8d6b9ee57d4d0ed2b1f305163624712a0532cb7bce0c747317984fc5457440d" &&
    exp.provenance.packages.rfdetr === "1.11.2" && !!exp.provenance.packages.torch && exp.manifest.digests.source === "7e46386505f9dd8bf1221e3eaba4eb7cd5a99ddc9ebd00f9aaef4a028cf4e3f6",
    `model=${p.model_id} rfdetr=${exp.provenance.packages.rfdetr} torch=${exp.provenance.packages.torch}`);

  // 9. verification through UI
  await clickText("Verificar corrida");
  await waitFor(() => ev("!!document.querySelector('[data-testid=verify]')"), "verification", 120000);
  const ver = await ev("document.querySelector('[data-testid=verify]').textContent");
  check("UI deep verification (re-decodes video, re-runs the model, compares boxes) passes",
    ver.includes("Verificación correcta") && ver.includes("redecoded_pixels_match") && ver.includes("reinference_matches_observations"), ver.slice(0, 160));
  check("after verification the run is shown as real inference",
    await waitFor(() => ev("!!document.querySelector('[data-testid=trusted]') && !document.querySelector('[data-testid=unverified]')"), "trusted state", 10000));
  check("after verification the UI state is 'verified' and the origin text may say verified locally",
    (await waitFor(() => ev("document.querySelector('[data-testid=trust-state]')?.getAttribute('data-trust') === 'verified'"), "verified state", 10000)) &&
    (await ev("(document.querySelector('[data-testid=origin]')?.textContent||'').includes('verificada localmente')")));
  check("the verification scope is stated: local integrity and reproduction, not authenticity",
    await ev("(document.querySelector('[data-testid=trust-scope]')?.textContent||'').includes('No prueba autenticidad')"));
  await shot("03_final_state.png");

  // 10. honesty notes visible
  const notes = await ev("document.body.textContent");
  check("limitations are visible (uncalibrated, no excavator class, person untested, not safety system)",
    notes.includes("no están calibradas") && notes.includes("excavadora") && notes.includes("no validada en este clip") && notes.includes("No es un sistema de seguridad"));

  // 11. tampering (MG-RC1-006): changing one evidence image on disk must drop the trusted state shown in the UI
  const jpg = path.resolve(process.env.MG_RUNS_DIR || "runs", runId, "frames", "000300.jpg");
  const original = fs.readFileSync(jpg);
  const reload = async () => { await send("Page.navigate", { url: BASE }); await waitFor(() => ev("!!document.querySelector('[data-testid=nav-video]')"), "navigation"); await ev("document.querySelector('[data-testid=nav-video]').click()"); await sleep(500); await waitFor(() => ev("document.body.textContent.includes('run_')"), "run shown after reload"); };
  try {
    fs.appendFileSync(jpg, "x");
    await reload();
    check("tampered evidence image: the UI shows the run as unverified, not as real inference",
      await waitFor(() => ev("!!document.querySelector('[data-testid=unverified]') && !document.querySelector('[data-testid=trusted]')"), "unverified banner", 10000));
    const tOrigin = await ev("document.querySelector('[data-testid=origin]')?.textContent || ''");
    check("tampered evidence: UI state returns to 'unverified'", (await ev("document.querySelector('[data-testid=trust-state]')?.getAttribute('data-trust')")) === "unverified");
    check("tampered run: origin label is the unverified claim, not real inference",
      tOrigin.includes("NO verificada") && !tOrigin.includes("Inferencia experimental real"), tOrigin.slice(0, 120));
    check("tampered run: the detections panel is marked provisional/unverified",
      await ev("document.querySelector('[data-testid=detections-title]') === null || (document.querySelector('[data-testid=detections-title]').getAttribute('data-trusted') === 'no' && document.querySelector('[data-testid=detections-title]').textContent.includes('NO verificadas'))"));
    check("tampered evidence image is refused by the server (409)", (await ev(`fetch('/api/runs/${runId}/frames/300').then(r=>r.status)`)) === 409);
    await shot("04_tampered_evidence_unverified.png");
  } finally {
    fs.writeFileSync(jpg, original);
  }
  await reload();
  // monotonic revocation (cycle-5 MG-TRUST-014): the mismatch was SEEN, so restoring the bytes does not bring the earlier grant back; only a new deep verification does
  check("after the evidence file is restored byte-for-byte the run stays UNVERIFIED (no resurrection by loading)",
    await waitFor(() => ev("!!document.querySelector('[data-testid=unverified]') && !document.querySelector('[data-testid=trusted]')"), "unverified banner", 10000));
  await clickText("Verificar corrida");
  await waitFor(() => ev("!!document.querySelector('[data-testid=verify]') && document.querySelector('[data-testid=verify]').textContent.includes('Verificación correcta')"), "re-verification", 120000);
  check("a new successful deep verification is the only way to regain the trusted state",
    await waitFor(() => ev("!!document.querySelector('[data-testid=trusted]') && !document.querySelector('[data-testid=unverified]')"), "trusted banner after re-verification", 10000));

  // 12. local API security (MG-SEC-002) observed through the real server
  const u = new URL(BASE);
  const hostile = await rawGet("/api/health", { Host: "evil.example" });
  check("hostile Host header is refused (DNS-rebinding defence)", hostile.status === 403, `status ${hostile.status}`);
  const rebindIndex = await rawGet("/", { Host: "rebind.attacker.example:" + u.port });
  check("the session token is never handed to a hostile Host", rebindIndex.status === 403 && !/mg-session-token/.test(rebindIndex.body) && !rebindIndex.headers["set-cookie"]);
  const noToken = await rawGet("/api/videos", { Host: u.host });
  check("API without credentials is refused (401)", noToken.status === 401, `status ${noToken.status}`);
  const badOrigin = await rawGet("/api/videos", { Host: u.host, Origin: "http://evil.example" });
  check("foreign Origin is refused (403)", badOrigin.status === 403, `status ${badOrigin.status}`);
  const csrf = await rawGet(`/api/videos/x/analyze`, { Host: u.host, Cookie: "mg_session=anything" }, "POST");
  check("cookie alone cannot trigger analysis (401)", csrf.status === 401, `status ${csrf.status}`);
  check("session cookie is HttpOnly (not readable by page script) and the token is in the page",
    (await ev("!document.cookie.includes('mg_session')")) && (await ev("!!document.querySelector('meta[name=mg-session-token]')?.content")));
  check("observation origin is labeled as actual experimental model inference",
    await ev("(document.querySelector('[data-testid=origin]')?.textContent||'').includes('Inferencia experimental real') && !(document.querySelector('[data-testid=origin]')?.textContent||'').includes('sintética')"));
} catch (e) {
  check("acceptance script ran to completion", false, e.message);
} finally {
  fs.writeFileSync(path.join(OUT, "results.json"), JSON.stringify({ base: BASE, when: new Date().toISOString(), results }, null, 1));
  try { ws?.close(); } catch { /* ignore */ }
  chrome.kill();
  const failed = results.filter((r) => !r.ok).length;
  console.log(`\n${results.length - failed}/${results.length} checks passed`);
  process.exit(failed ? 1 : 0);
}
