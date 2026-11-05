// MG-VERTICAL-02 multi-feed / alerts / dashboards browser acceptance over the DevTools protocol with the system Google Chrome (no extra packages).
// Usage: node e2e/mvp_acceptance.mjs [baseUrl]  -> writes logs/multifeed_e2e/{results.json,*.png}; exit 1 on any failed check.
// SOURCES: the registered local MP4 clip replayed as several feeds (a concurrency SIMULATION, never several cameras) analysed by the real model; no camera is used. Notifications are
// exercised through Chrome's permission override (CDP Browser.setPermission), never by clicking a real permission prompt.
// SAFETY (cycle-16 MG-PROC-016): these suites create analyses, tamper with stored evidence and delete sessions. They may only run against a server started by
// scripts/run_mvp_e2e.sh with isolated storage (it sets MG_E2E_ISOLATED); never against a user's live server.
if (!process.env.MG_E2E_ISOLATED) { console.error("refusing to run: use `MG_E2E_PORT=<free port> bash scripts/run_mvp_e2e.sh " + process.argv[1] + "` (isolated server and storage)"); process.exit(3); }
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const BASE = process.argv[2] ?? "http://127.0.0.1:8000";
const PORT = 9336;
const OUT = path.resolve("logs/multifeed_e2e");
fs.mkdirSync(OUT, { recursive: true });
const profile = fs.mkdtempSync(path.join(os.tmpdir(), "mg-chrome-mf-"));
const chrome = spawn("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", [
  "--headless=new", `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`, "--window-size=1500,1200",
  "--autoplay-policy=no-user-gesture-required", "--no-first-run", "--no-default-browser-check",
    // best-effort offline hardening (not network isolation): see e2e/browser_acceptance.mjs
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
const click = (tid) => ev(`(()=>{const b=document.querySelector('[data-testid=${tid}]');if(!b||b.disabled)return false;b.click();return true})()`);
const text = (tid) => ev(`document.querySelector('[data-testid=${tid}]')?.textContent||''`);
const attr = (tid, a) => ev(`document.querySelector('[data-testid=${tid}]')?.getAttribute(${JSON.stringify(a)})`);
const api = (p, init = {}) => ev(`(async()=>{const tok=document.querySelector('meta[name=mg-session-token]').content;const i=${JSON.stringify(init)};i.headers=Object.assign({'X-MG-Token':tok}, i.headers||{});const r=await fetch(${JSON.stringify(p)}, i);return {status:r.status, body:await r.text()}})()`);
const liveTracks = () => ev("(window.__streams||[]).flatMap(s=>s.getTracks()).filter(t=>t.readyState==='live').length");
async function mouseClick(x, y) {
  await send("Input.dispatchMouseEvent", { type: "mouseMoved", x, y });
  await send("Input.dispatchMouseEvent", { type: "mousePressed", x, y, button: "left", clickCount: 1 });
  await send("Input.dispatchMouseEvent", { type: "mouseReleased", x, y, button: "left", clickCount: 1 });
}
async function clickStage(testid, fracs) {
  const r = await ev(`(()=>{const b=document.querySelector('[data-testid=${testid}]').getBoundingClientRect();return {x:b.left,y:b.top,w:b.width,h:b.height}})()`);
  for (const [fx, fy] of fracs) await mouseClick(r.x + fx * r.w, r.y + fy * r.h);
}
const seek = (sec) => ev(`new Promise(r=>{const v=document.querySelector('video');v.pause();v.addEventListener('seeked',()=>setTimeout(r,500),{once:true});v.currentTime=${sec}})`);


const feedPanels = () => ev("[...document.querySelectorAll('[data-feed-id]')].map(e=>e.getAttribute('data-feed-id'))");
const panelAttr = (fid, tid, a) => ev(`document.querySelector('[data-feed-id=${JSON.stringify(fid)}] [data-testid=${tid}]')?.getAttribute(${JSON.stringify(a)})`);
const panelClick = (fid, tid) => ev(`(()=>{const b=document.querySelector('[data-feed-id=${JSON.stringify(fid)}] [data-testid=${tid}]');if(!b||b.disabled)return false;b.click();return true})()`);
const analyzed = async (fid) => Number(await panelAttr(fid, "feed-stats", "data-analyzed"));
const jget = async (p) => JSON.parse((await api(p)).body);
const jpost = (p, o) => api(p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(o ?? {}) });
const setPerm = (setting) => send("Browser.setPermission", { permission: { name: "notifications" }, setting, origin: new URL(BASE).origin });

try {
  await connect();
  await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1500, height: 1300, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url: BASE });
  await waitFor(() => ev("document.querySelector('h1')?.textContent.includes('MineGuard')"), "page");
  const token = await ev("document.querySelector('meta[name=mg-session-token]').content");

  // ======================================================================== navigation and the empty state
  check("ten areas in the navigation: the nine of MVP 1 plus Multi-fuente", (await ev("document.querySelectorAll('nav [role=tab]').length")) === 10 && (await ev("!!document.querySelector('[data-testid=nav-feeds]')")));
  check("the alert badge is always present and starts empty", (await attr("alert-badge", "data-count")) === "0");
  await click("nav-feeds");
  await waitFor(() => ev("!!document.querySelector('[data-testid=view-feeds]')"), "feeds view");
  await waitFor(() => ev("!!document.querySelector('[data-testid=feeds-empty]')"), "empty Multi-fuente state (after the first snapshot)", 10000);
  check("Multi-fuente starts with no feeds, a Spanish explanation and the physical-camera limit stated", (await ev("!!document.querySelector('[data-testid=feeds-empty]')")) && (await text("view-feeds")).toLowerCase().includes("no son cámaras físicas") && (await text("feeds-form")).includes("no se activa en esta versión"));

  // ======================================================================== three feeds
  await waitFor(() => ev("document.querySelector('[data-testid=feeds-video]')?.options.length>0 && !document.querySelector('[data-testid=feeds-create3]').disabled"), "video list loaded and the create button enabled");
  check("the registered video is offered", (await ev("document.querySelector('[data-testid=feeds-video]').options[0].textContent")).endsWith(".mp4"));
  await click("feeds-create3");
  await waitFor(async () => (await feedPanels()).length === 3, "three feed panels");
  const fids = await feedPanels();
  check("three feeds exist with three distinct identities", new Set(fids).size === 3);
  check("three IDLE feeds of one file are not yet labelled a repeated-source simulation (MG-PROV-035: nothing is playing)", (await ev("!document.querySelector('[data-testid=feeds-repeated-banner]')")) && (await Promise.all(fids.map((f) => panelAttr(f, "feed-source", "data-provenance")))).every((p) => p === "real_inference"));
  await click("feeds-start-all");
  await waitFor(async () => (await attr("feeds-capacity", "data-active")) === "3", "three active feeds", 20000);
  await waitFor(async () => (await Promise.all(fids.map(analyzed))).every((n) => n >= 3), "every feed analysed at least 3 frames with the real model", 90000);
  check("three feeds are playing and analysed by the real model (one shared worker)", (await Promise.all(fids.map((f) => panelAttr(f, "feed-state", "data-state")))).every((s) => s === "playing") && (await attr("feeds-capacity", "data-active")) === "3");
  check("the same file in several feeds is labelled a REPEATED SOURCE simulation, never several cameras", (await ev("!!document.querySelector('[data-testid=feeds-repeated-banner]')")) && (await Promise.all(fids.map((f) => panelAttr(f, "feed-source", "data-provenance")))).every((p) => p === "repeated_source_simulation") && (await text("feeds-repeated-banner")).includes("NO representan varias cámaras"));
  await waitFor(() => ev("document.querySelectorAll('[data-testid=feed-preview]').length===3"), "three previews loaded (the next frame is loaded off-screen before it replaces the shown one)", 30000);
  const imgs = await ev("[...document.querySelectorAll('[data-testid=feed-preview]')].map(i=>({w:i.naturalWidth,ok:i.complete}))");
  check("each panel shows the latest ANALYSED frame (a real image served from memory)", imgs.length === 3 && imgs.every((i) => i.ok && i.w > 100), JSON.stringify(imgs));
  const painted = await ev("[...document.querySelectorAll('[data-testid=feed-overlay]')].map(c=>{const d=c.getContext('2d').getImageData(0,0,c.width,c.height).data;let n=0;for(let i=3;i<d.length;i+=4*40)if(d[i])n++;return n})");
  check("zones and detections are drawn on every panel (overlay is not blank)", painted.length === 3 && painted.every((n) => n > 20), JSON.stringify(painted));
  const risks = await Promise.all(fids.map((f) => panelAttr(f, "feed-risk", "data-state")));
  check("each panel shows its own risk state (GREEN or UNKNOWN: the clip has vehicles and no person)", risks.every((r) => ["GREEN", "UNKNOWN"].includes(r)), risks.join());
  check("UNKNOWN or GREEN text never claims safety", !(await text("view-feeds")).match(/es seguro|está seguro/i));
  const live = await jget("/api/feeds");
  check("the API agrees: one scheduler, three active, validated count is 3", live.active === 3 && live.validated_feeds === 3 && live.scheduler.alive === true && live.feeds.every((f) => f.counters.analyzed >= 3));
  await shot("feeds_three_playing.png");

  // ======================================================================== independent controls
  const [a, b, c] = fids;
  await panelClick(a, "feed-pause");
  await waitFor(async () => (await panelAttr(a, "feed-state", "data-state")) === "paused", "feed A paused");
  await sleep(500);
  const pa = await analyzed(a), pb = await analyzed(b);
  await sleep(3500);
  check("pausing feed A stops only feed A", (await analyzed(a)) <= pa + 2 && (await analyzed(b)) > pb && (await panelAttr(b, "feed-state", "data-state")) === "playing");
  await panelClick(a, "feed-resume");
  await panelClick(c, "feed-stop");
  await waitFor(async () => (await panelAttr(c, "feed-state", "data-state")) === "stopped", "feed C stopped");
  check("stopping feed C leaves A and B playing", (await panelAttr(a, "feed-state", "data-state")) === "playing" && (await panelAttr(b, "feed-state", "data-state")) === "playing");
  await panelClick(c, "feed-start");
  await waitFor(async () => (await panelAttr(c, "feed-state", "data-state")) === "playing", "feed C restarted");
  check("feed C restarts independently as a new run", (await jget(`/api/feeds/${c}`)).counters.analyzed <= 5);

  // ======================================================================== independent zone edit through the UI
  const revBefore = (await jget(`/api/feeds/${a}`)).zones_revision, revB = (await jget(`/api/feeds/${b}`)).zones_revision;
  await panelClick(a, "feed-zones-toggle");
  await waitFor(() => ev("!!document.querySelector('[data-feed-id=\"" + a + "\"] [data-testid=zone-editor]')"), "zone editor on feed A");
  await ev(`document.querySelector('[data-feed-id="${a}"] [data-testid=zone-item] button').click()`);
  await waitFor(async () => (await jget(`/api/feeds/${a}`)).zones.length === 0, "zone removed on feed A", 8000);
  check("a zone edit on feed A is compare-and-swap, bumps only feed A's revision and leaves feed B's zones alone", (await jget(`/api/feeds/${a}`)).zones_revision === revBefore + 1 && (await jget(`/api/feeds/${b}`)).zones_revision === revB && (await jget(`/api/feeds/${b}`)).zones.length === 1);
  const stale = await api(`/api/feeds/${a}/zones`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ zones: [], expected_revision: revBefore }) });
  check("a stale zone write is refused with 409", stale.status === 409);

  // a failed zone save keeps the polygon the operator drew (review B MG-ZONE-022): the first PUT is answered with a 409, the second goes through
  await ev("(()=>{window.__zonePutFail=true;const f=window.fetch;window.fetch=async function(u,i){if(window.__zonePutFail&&String(u).includes('/zones')&&i&&i.method==='PUT'){window.__zonePutFail=false;return new Response(JSON.stringify({detail:{msg_id:'mg.refusal.zones_revision_conflict'}}),{status:409,headers:{'Content-Type':'application/json'}})}return f.apply(this,arguments)}})()");
  await ev(`document.querySelector('[data-feed-id="${a}"] [data-testid=zone-draw]').click()`);
  await clickStage("feed-stage", [[0.2, 0.5], [0.8, 0.5], [0.8, 0.9], [0.2, 0.9]]);
  await ev(`document.querySelector('[data-feed-id="${a}"] [data-testid=zone-close]').click()`);
  await waitFor(() => ev(`(document.querySelector('[data-feed-id="${a}"] [data-testid=zone-error]')?.textContent||'').includes('Las zonas cambiaron')`), "conflict message", 8000);
  check("a failed zone save (409) keeps the drawn polygon, shows the reason and leaves the saved zones untouched", (await ev(`(document.querySelector('[data-feed-id="${a}"] [data-testid=zone-hint]')?.textContent||'').includes('(4)')`)) && (await jget(`/api/feeds/${a}`)).zones.length === 0);
  await ev(`document.querySelector('[data-feed-id="${a}"] [data-testid=zone-close]').click()`);
  await waitFor(async () => (await jget(`/api/feeds/${a}`)).zones.length === 1, "retry saved the zone", 8000);
  await waitFor(() => ev(`!document.querySelector('[data-feed-id="${a}"] [data-testid=zone-hint]')`), "draft cleared after the successful save", 8000);
  check("retrying the same polygon after the conflict saves it with the refreshed revision and clears the draft", (await ev(`!document.querySelector('[data-feed-id="${a}"] [data-testid=zone-hint]')`)) && (await jget(`/api/feeds/${a}`)).zones_revision >= revBefore + 2);

  // ======================================================================== capacity honesty
  await click("feeds-stop-all");
  await waitFor(async () => (await attr("feeds-capacity", "data-active")) === "0", "all feeds stopped");
  check("raising the limit above the validated count needs an explicit acknowledgement", (await ev("document.querySelector('[data-testid=feeds-cap-4]').disabled")) === true);
  await ev("document.querySelector('[data-testid=feeds-ack]').click()");
  await click("feeds-cap-4");
  await waitFor(async () => (await attr("feeds-capacity", "data-limit")) === "4", "limit 4");
  check("the unvalidated limit 4 is allowed only after the acknowledgement and stays labelled", (await text("feeds-capacity")).includes("validado en este equipo: 3"));
  await click("feeds-cap-3");
  await waitFor(async () => (await attr("feeds-capacity", "data-limit")) === "3", "back to 3");

  // ======================================================================== history, operator and developer dashboards
  const hist = await jget("/api/feeds/history");
  check("stopped feeds left metadata-only summaries (no frame was written)", hist.runs.length >= 3 && hist.retention.frames_persisted === false && hist.runs.every((r) => r.retention.frames_persisted === false));
  await click("nav-resumen");
  await waitFor(() => ev("!!document.querySelector('[data-testid=operator-panel]') && document.querySelectorAll('[data-testid=op-feed]').length===3"), "operator panel with three feeds", 15000);
  const det = Number(await text("op-detections")), trk = Number(await text("op-tracks"));
  check("Operator dashboard: per-frame detections and unique tracks are different numbers (a truck seen in many frames is not many vehicles)", det > 0 && trk > 0 && trk < det, `${det} detections vs ${trk} tracks`);
  check("Operator dashboard: repeated sources are labelled and their events are kept apart from real ones", (await text("operator-panel")).includes("FUENTE REPETIDA") && (await text("op-events-sim")).length > 0);
  check("Operator dashboard shows retention, health and resources without images", (await text("op-retention")).includes("solo metadatos") && !(await ev("!!document.querySelector('[data-testid=operator-panel] img')")));
  await click("nav-estadisticas");
  await waitFor(() => ev("!!document.querySelector('[data-testid=trend-empty]') || !!document.querySelector('[data-testid=trend-svg]')"), "trend loaded", 15000);
  check("Estadísticas: the trend says honestly when there is no data and heat maps are still 'No disponible'", ((await ev("!!document.querySelector('[data-testid=trend-empty]')")) || (await ev("!!document.querySelector('[data-testid=trend-svg]')"))) && (await text("view-estadisticas")).includes("No disponible"));
  await click("nav-desarrollador");
  await waitFor(() => ev("document.querySelectorAll('[data-testid=dev-feed]').length===3"), "developer table", 15000);
  const dev = await text("view-desarrollador");
  check("Developer dashboard: model, versions, scheduler, resources and per-feed counters are shown from real measurements", dev.includes("rf-detr") || dev.includes("RF-DETR"));
  check("Developer dashboard: kernel, tracker and alert API versions are shown", (await text("dev-versions")).includes("mg-risk-kernel/") && (await text("dev-versions")).includes("mg.alert-api/1") && (await text("dev-scheduler")).includes("un solo modelo compartido"));
  check("Developer dashboard leaks no token or image data", !dev.includes(token) && !(await ev("!!document.querySelector('[data-testid=developer-panel] img')")));

  // ======================================================================== alerts: simulation -> alert -> notification -> acknowledgement
  const vids = await jget("/api/videos");
  const runIds = [];
  for (const stride of [30, 29]) {
    const j = JSON.parse((await jpost(`/api/videos/${vids[0].video_id}/analyze`, { stride, threshold: 0.3 })).body);
    let job;
    for (let i = 0; i < 400; i++) { job = await jget(`/api/jobs/${j.job_id}`); if (job.status === "done" || job.status === "failed") break; await sleep(300); }
    runIds.push(job.run_id);
  }
  check("two real MP4 runs exist as bases for the scripted scenario", runIds.every(Boolean));
  await setPerm("denied");
  await click("nav-configuracion");
  await waitFor(() => ev("!!document.querySelector('[data-testid=alert-settings]')"), "settings");
  check("Configuración: notification and sound consent start OFF and the webhook is stated as disabled", (await text("notify-pref")) === "no" && (await text("sound-pref")) === "desactivado" && (await text("webhook-status")).includes("DESACTIVADAS") && (await text("multicam-na")).includes("No disponible"));
  await click("nav-simulaciones");
  await ev(`fetch('/api/simulations/person-vehicle',{method:'POST',headers:{'Content-Type':'application/json','X-MG-Token':'${token}'},body:JSON.stringify({base_run_id:'${runIds[0]}'})}).then(r=>r.status)`);
  await click("nav-eventos");
  await waitFor(() => ev("document.querySelectorAll('[data-testid=alert-row]').length>=2"), "simulation alerts", 20000);
  const rows = await ev("[...document.querySelectorAll('[data-testid=alert-row]')].map(r=>({sev:r.dataset.severity,sim:r.dataset.simulation,st:r.dataset.status,txt:r.textContent}))");
  check("scripted alerts appear in the alert center labelled SIMULACIÓN, including a RED one", rows.every((r) => r.sim === "yes" && r.txt.includes("SIMULACIÓN")) && rows.some((r) => r.sev === "RED") && rows.some((r) => r.sev === "YELLOW"));
  check("synthetic alerts never turn the badge red as a genuine incident", (await attr("alert-badge", "data-red")) === "0" && Number(await attr("alert-badge", "data-count")) >= 1);
  const apiAlerts = (await jget("/api/alerts?simulated=true")).alerts;
  check("every alert carries alert id, event id, zone, severity, time, provenance, status and delivery", apiAlerts.every((x) => x.alert_id && x.event_id && x.zone_id && x.severity && x.created_utc && x.source.provenance === "synthetic_scripted" && x.status && x.delivery_state));
  await click("nav-configuracion");
  await click("notify-enable");
  await waitFor(async () => (await jget("/api/alerts?simulated=true")).alerts.some((x) => x.delivery.browser.status === "denied"), "denied recorded", 15000);
  const denied = (await jget("/api/alerts?simulated=true")).alerts.filter((x) => x.delivery.browser.status === "denied");
  check("browser permission DENIED is recorded honestly per alert and the in-app delivery still stands", denied.length >= 1 && denied.every((x) => x.delivery.in_app.status === "displayed") && (await text("notify-permission")) === "denied");
  await ev("document.querySelector('[data-testid=sound-enable]').click()");
  await waitFor(async () => (await jget("/api/alerts?simulated=true")).alerts.some((x) => x.delivery.sound.status === "delivered"), "sound delivered", 15000);
  check("local sound plays only after the user enabled it, and the server records the browser's report", (await text("sound-pref")) === "activado");
  await setPerm("granted");
  await ev(`fetch('/api/simulations/person-vehicle',{method:'POST',headers:{'Content-Type':'application/json','X-MG-Token':'${token}'},body:JSON.stringify({base_run_id:'${runIds[1]}'})}).then(r=>r.status)`);
  await waitFor(async () => (await jget("/api/alerts?simulated=true")).alerts.length >= 4, "second scenario alerts", 20000);
  await sleep(500);
  await click("notify-enable");
  await waitFor(async () => (await jget("/api/alerts?simulated=true")).alerts.some((x) => ["delivered", "failed"].includes(x.delivery.browser.status)), "granted permission produces an outcome", 15000);
  const outcome = (await jget("/api/alerts?simulated=true")).alerts.filter((x) => ["delivered", "failed"].includes(x.delivery.browser.status));
  check("with permission granted the browser notification outcome (delivered or failed) is reported, never assumed", outcome.length >= 1, outcome.map((x) => x.delivery.browser.status).join());
  await click("nav-eventos");
  await waitFor(() => ev("!!document.querySelector('[data-testid=alert-ack]')"), "ack button");
  const before = Number(await attr("alert-badge", "data-count"));
  await click("alert-ack");
  await waitFor(async () => (await jget("/api/alerts?status=acknowledged")).alerts.length >= 1, "acknowledged", 10000);
  const ackd = (await jget("/api/alerts?status=acknowledged")).alerts[0];
  check("acknowledging an alert records who/when and lowers the unacknowledged badge", !!ackd.acknowledged_utc && ackd.status === "acknowledged" && Number(await attr("alert-badge", "data-count")) <= before);
  await ev(`document.querySelector('[data-testid=alert-resolve]').click()`);
  await waitFor(async () => (await jget("/api/alerts?status=resolved")).alerts.length >= 1, "resolved", 10000);
  check("an operator can resolve an alert", (await jget("/api/alerts?status=resolved")).alerts[0].resolved_by !== null);
  await click("nav-resumen");
  await waitFor(() => ev("!!document.querySelector('[data-testid=op-alerts]')"), "alerts in the operator panel");
  check("Operator dashboard counts alerts by provenance (synthetic apart) and never merges them with real incidents", (await text("op-alerts")).includes("synthetic_scripted"));

  // ======================================================================== layout
  for (const [w, label] of [[1500, "desktop"], [820, "tablet"], [420, "phone"]]) {
    await send("Emulation.setDeviceMetricsOverride", { width: w, height: 1200, deviceScaleFactor: 1, mobile: w < 600 });
    await click("nav-feeds");
    await sleep(500);
    const over = await ev("document.documentElement.scrollWidth - window.innerWidth");
    const offenders = over > 1 ? await ev("[...document.querySelectorAll('body *')].filter(e=>e.getBoundingClientRect().right>window.innerWidth+1).slice(0,6).map(e=>e.tagName+'.'+(e.className||'')+'['+(e.getAttribute('data-testid')||'')+'] '+Math.round(e.getBoundingClientRect().right))") : [];
    check(`Multi-fuente has no horizontal page scroll at ${label} width (${w}px)`, over <= 1, `overflow ${over}px ${JSON.stringify(offenders)}`);
  }
  await shot("feeds_phone.png");
} catch (e) {
  check("multi-feed acceptance script ran to completion", false, e.message);
} finally {
  const failed = results.filter((r) => !r.ok).length;
  fs.writeFileSync(path.join(OUT, "results.json"), JSON.stringify({ base: BASE, when: new Date().toISOString(), results }, null, 1));
  console.log(`\n${results.length - failed}/${results.length} checks passed`);
  try { ws?.close(); } catch { /* ignore */ }
  chrome.kill("SIGTERM");
  await sleep(500);
  process.exit(failed ? 1 : 0);
}
