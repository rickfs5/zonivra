// MG-PRODUCT-VERTICAL-06 operator journey (sources -> synthetic YELLOW/RED -> incident investigation -> trust -> ack/resolve -> JSON export -> back to monitoring; responsive; deletion disabled)
// (boilerplate shared with the other suites; originally the MG-VERTICAL-02 multi-feed suite) over the DevTools protocol with the system Google Chrome (no extra packages).
// Usage: node e2e/mvp_acceptance.mjs [baseUrl]  -> writes logs/product_vertical06/{results.json,*.png}; exit 1 on any failed check.
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
const PORT = 9346;
const OUT = path.resolve("logs/product_vertical06");
fs.mkdirSync(OUT, { recursive: true });
const profile = fs.mkdtempSync(path.join(os.tmpdir(), "mg-chrome-p06-"));
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


const key = (k, code = k) => send("Input.dispatchKeyEvent", { type: "keyDown", key: k, code, ...(k === "Enter" ? { text: "\r" } : {}), windowsVirtualKeyCode: { Tab: 9, Enter: 13, Escape: 27 }[k] ?? 0 }).then(() => send("Input.dispatchKeyEvent", { type: "keyUp", key: k, code, windowsVirtualKeyCode: { Tab: 9, Enter: 13, Escape: 27 }[k] ?? 0 }));
const nav = async (a) => { await click(`nav-${a}`); };
const AREAS = [["resumen", "view-resumen"], ["video", "view-video"], ["camara", "webcam-panel"], ["feeds", "view-feeds"], ["sesiones", "view-sesiones"], ["eventos", "view-eventos"], ["simulaciones", "view-simulaciones"], ["estadisticas", "view-estadisticas"], ["desarrollador", "view-desarrollador"], ["configuracion", "view-configuracion"]];
const overflow = () => ev("document.documentElement.scrollWidth - window.innerWidth");

const panelIds = () => ev("[...document.querySelectorAll('article.feed[data-feed-id]')].map(e=>e.getAttribute('data-feed-id'))");

const DL = fs.mkdtempSync(path.join(os.tmpdir(), "mg-p06-dl-"));
try {
  await connect();
  await send("Page.enable");
  try { await send("Browser.setDownloadBehavior", { behavior: "allow", downloadPath: DL }); } catch { await send("Page.setDownloadBehavior", { behavior: "allow", downloadPath: DL }); }
  await send("Page.addScriptToEvaluateOnNewDocument", { source: "window.__mgErrors=[];window.addEventListener('error',e=>window.__mgErrors.push(String(e.message)));window.addEventListener('unhandledrejection',e=>window.__mgErrors.push('rejection: '+String(e.reason)))" });
  await send("Emulation.setDeviceMetricsOverride", { width: 1500, height: 1100, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url: BASE });
  await waitFor(() => ev("document.querySelector('h1')?.textContent.includes('MineGuard')"), "page");

  // ---- 1-3: console, no sources yet
  await nav("resumen");
  await waitFor(() => ev("!!document.querySelector('[data-testid=ov-no-sources]')"), "empty overview");
  check("1-3 the console opens on the overview and an empty system says there are no sources (no invented tiles)", true);
  const model = await jget("/api/monitor/overview");

  // ---- 4-6: one source alone, then a second (concurrency simulation), tile facts from real data
  await nav("feeds");
  await waitFor(() => ev("document.querySelector('[data-testid=feeds-video]')?.options.length>0 && !document.querySelector('[data-testid=feeds-add]').disabled"), "feeds form", 20000);
  await click("feeds-add");
  await waitFor(async () => (await panelIds()).length === 1, "feed A", 20000);
  const [pA] = await panelIds();
  await ev(`document.querySelector('[data-feed-id=${JSON.stringify(pA)}] [data-testid=feed-start]').click()`);
  await waitFor(async () => (await analyzed(pA)) >= 2, "feed A analysed", 150000);
  await click("feeds-add");
  await waitFor(async () => (await panelIds()).length === 2, "feed B", 20000);
  const pB = (await panelIds()).find((x) => x !== pA);                                  // B exists but is NOT started: an idle duplicate (MG-PROV-035)
  await nav("resumen");
  await waitFor(() => ev("document.querySelectorAll('[data-testid=ov-feed-tile]').length===2"), "two tiles");
  const tile = (fid, tid, a) => ev(`document.querySelector('[data-testid=ov-feed-tile][data-feed-id=${JSON.stringify(fid)}] [data-testid=${tid}]')?.${a ? `getAttribute(${JSON.stringify(a)})` : "textContent"}`);
  const apiFeeds = (await jget("/api/feeds")).feeds;
  const fa = apiFeeds.find((f) => f.feed_id === pA);
  check("4-5 a tile shows the stable source id, source type and detector taken from the API, not from the page", (await tile(pA, "ov-tile-source-id")) === fa.source.source_id && (await tile(pA, "ov-tile-detector")).includes((await jget("/api/model")).model_id));
  check("5 an active, fresh observation is shown as such with the last source timestamp", (await tile(pA, "ov-tile-fresh", "data-fresh")) === "yes");
  check("5 a playing source with an idle duplicate is NOT shown as simultaneous monitoring (MG-PROV-035)", (await tile(pA, "ov-tile-dup", "data-dup")) === "none" && (await tile(pB, "ov-tile-dup", "data-dup")) === "inactive" && !(await tile(pA, "ov-tile-source-id")).includes("repeated"));
  check("5 the idle source shows no counts and no fresh observation (nothing invented, nothing carried over)", (await tile(pB, "ov-tile-fresh", "data-fresh")) === "no" && (await tile(pB, "ov-tile-counts")).includes("no disponibles"));
  await shot("p01_overview_one_active_one_idle.png");
  await nav("feeds");
  await waitFor(async () => (await panelIds()).length === 2, "feeds grid", 20000);
  await ev(`document.querySelector('[data-feed-id=${JSON.stringify(pB)}] [data-testid=feed-start]').click()`);
  await waitFor(async () => (await analyzed(pB)) >= 2, "feed B analysed", 150000);
  await nav("resumen");
  await waitFor(() => ev("document.querySelectorAll('[data-testid=ov-feed-tile]').length===2"), "two tiles again");
  await waitFor(async () => (await tile(pA, "ov-tile-dup", "data-dup")) === "active_overlap", "active overlap", 20000);
  check("6 two feeds of one file are labelled an ACTIVE overlap simulation on both tiles", (await tile(pB, "ov-tile-dup", "data-dup")) === "active_overlap");
  await shot("p02_overview_active_overlap.png");
  await nav("feeds");
  await waitFor(async () => (await panelIds()).length === 2, "feeds grid again", 20000);
  await ev(`document.querySelector('[data-feed-id=${JSON.stringify(pB)}] [data-testid=feed-stop]').click()`);
  await waitFor(async () => (await panelAttr(pB, "feed-state", "data-state")) === "stopped", "B stopped", 40000);
  await nav("resumen");
  await waitFor(async () => (await tile(pA, "ov-tile-dup", "data-dup")) === "historical_repeated", "historical", 30000);
  check("6 after the duplicate stops, A shows HISTORICAL repeated participation (not active), B is inactive", (await tile(pB, "ov-tile-dup", "data-dup")) === "inactive");

  // ---- 7-9: synthetic YELLOW -> RED from the existing lab while feed A keeps playing
  await nav("feeds");
  await waitFor(async () => (await panelIds()).length === 2, "feeds grid before analysis", 20000);
  await click("feeds-stop-all");                                                         // the single model is shared: the MP4 analysis runs with the feeds stopped
  await waitFor(async () => (await jget("/api/feeds")).feeds.every((f) => f.state !== "playing" && f.state !== "paused"), "feeds stopped", 40000);
  const vids = await jget("/api/videos");
  const job = JSON.parse((await jpost(`/api/videos/${vids[0].video_id}/analyze`, { stride: 15, threshold: 0.3 })).body);
  await waitFor(async () => { const j = await jget(`/api/jobs/${job.job_id}`); if (j.status === "failed") throw new Error("analysis failed"); return j.status === "done"; }, "MP4 analysis", 240000);
  await nav("feeds");
  await waitFor(async () => (await panelIds()).length === 2, "feeds grid after analysis", 20000);
  await ev(`document.querySelector('[data-feed-id=${JSON.stringify(pA)}] [data-testid=feed-start]').click()`);
  await waitFor(async () => (await jget("/api/feeds")).feeds.find((f) => f.feed_id === pA).counters.analyzed >= 2, "feed A restarted alone", 150000);
  const aBefore = (await jget("/api/feeds")).feeds.find((f) => f.feed_id === pA).counters.analyzed;
  await nav("simulaciones");
  await waitFor(() => ev("document.querySelector('[data-testid=sim-red-view]')?.disabled===false"), "lab ready", 20000);
  await click("sim-red-view");
  await waitFor(() => ev("!!document.querySelector('[data-testid=sim-result]')"), "lab result", 60000);
  const states = await ev("[...document.querySelectorAll('[data-testid=sim-interval]')].map(e=>e.dataset.state)");
  check("7 the scripted scenario yields the risk kernel's YELLOW then RED, labelled SIMULATION", states.includes("YELLOW") && states.includes("RED") && (await text("sim-banner")).includes("SINTÉTICO"));
  await waitFor(() => ev("document.querySelectorAll('[data-testid=sim-alert]').length>=1"), "derived alert", 15000);
  await click("sim-alert-detail");
  await waitFor(() => ev("!!document.querySelector('[data-testid=incident-drawer]')"), "incident drawer");
  await waitFor(() => ev("!!document.querySelector('[data-testid=investigation]')"), "investigation panel", 15000);
  const aid = await attr("incident-drawer", "data-alert-id");
  const inc = await jget(`/api/incidents/${aid}`);
  check("8 the workbench opens the real incident and classifies it SYNTHETIC", (await attr("investigation", "data-classification")) === "synthetic" && (await text("inv-class")).includes("SINTÉTICO"));
  check("8 the deterministic rule, the semantics version and the statement that AI never decides severity are shown", (await text("inv-risk")).includes(inc.risk.rule_id) && (await text("inv-risk")).includes(inc.risk.semantics_version) && (await text("inv-decided")).includes("nunca deciden"));
  const obsRows = await ev("[...document.querySelectorAll('[data-testid=tl-observation] li')].map(e=>e.dataset.kind)");
  const procRows = await ev("[...document.querySelectorAll('[data-testid=tl-processing] li')].map(e=>e.dataset.kind)");
  check("9 the timeline shows observation time and processing time apart, with the stored entry kinds only", obsRows.includes("risk_entry") && procRows[0] === "alert_created" && JSON.stringify(obsRows) === JSON.stringify(inc.timeline.observation.map((e) => e.kind)));
  check("9 timeline entries of a simulation are marked SINTÉTICO and unsupported kinds are declared, not invented", (await ev("[...document.querySelectorAll('[data-testid=inv-timeline] .tag-sim')].length>0")) && (await text("tl-unsupported")).includes("No registrado"));
  check("9 UNKNOWN intervals are listed from the stored evaluation or declared unavailable (never GREEN)", ["yes", "no"].includes(await attr("inv-unknown", "data-available")) && (await attr("inv-unknown", "data-available")) === (inc.unknown_intervals.available ? "yes" : "no"));
  check("10 evidence panel: metadata only, local consistency passes, authenticity NOT independently verified", (await text("inv-evidence")).includes("Solo metadatos") && (await attr("inv-evidence", "data-consistency")) === "pass" && (await text("inv-auth")).includes("NO verificada"));
  check("10 provider panel says scripted simulation, no model, no camera", (await text("inv-provider")).includes("Escenario con guion") && (await ev("document.querySelector('[data-testid=inv-provider] [data-kind]')?.getAttribute('data-kind')")) === "scripted_simulation");
  check("10 there is no image anywhere in the incident view", (await ev("document.querySelectorAll('[data-testid=incident-drawer] img, [data-testid=incident-drawer] canvas, [data-testid=incident-drawer] video').length")) === 0);
  await shot("p03_incident_investigation_desktop.png");

  // ---- 11-13: acknowledge, resolve; delivery stays separate
  await click("incident-ack");
  await waitFor(() => ev("document.querySelector('[data-testid=incident-alert]').textContent.includes('Reconocida')"), "acknowledged");
  await waitFor(async () => (await ev("[...document.querySelectorAll('[data-testid=tl-processing] li')].map(e=>e.dataset.kind)")).includes("alert_acknowledged"), "ack in the timeline", 10000);
  await click("incident-resolve");
  await waitFor(() => ev("document.querySelector('[data-testid=incident-alert]').textContent.includes('Resuelta')"), "resolved");
  await waitFor(async () => (await ev("[...document.querySelectorAll('[data-testid=tl-processing] li')].map(e=>e.dataset.kind)")).includes("alert_resolved"), "resolve in the timeline", 10000);
  const after = await jget(`/api/incidents/${aid}`);
  check("11-13 acknowledge then resolve update the stored lifecycle and the timeline; delivery is NOT reported as delivered", after.alert.status === "resolved" && after.delivery.state !== "delivered" && (await text("incident-delivery")).length > 10);
  await shot("p04_incident_resolved.png");

  // ---- 14-15: local JSON export through the real UI control, compared with the incident
  await click("inv-export");
  await waitFor(() => ev("!!document.querySelector('[data-testid=inv-exported]')"), "export done", 15000);
  const file = path.join(DL, `${aid}.mineguard-incident.json`);
  await waitFor(() => fs.existsSync(file) && fs.statSync(file).size > 100, "downloaded file", 15000);
  const exp = JSON.parse(fs.readFileSync(file, "utf8"));
  const shown = await jget(`/api/incidents/${aid}`);
  const strip = (o) => { const c = { ...o }; delete c.generated_utc; return JSON.stringify(c); };
  check("14-15 the downloaded JSON is the same incident the screen shows (same ids, rule, source, lifecycle)", exp.schema === "mg.incident.export/1" && strip(exp.incident) === strip(shown) && exp.incident.identity.alert_id === aid);
  const raw = fs.readFileSync(file, "utf8").toLowerCase();
  check("14 the export holds no image, footage or base64 payload and says images_included=false", !raw.includes("jpeg") && !raw.includes("base64") && exp.incident.evidence.images_included === false);

  // ---- 16-17: back to monitoring, other feeds independent
  await click("incident-back-monitor");
  await waitFor(() => ev("!document.querySelector('[data-testid=incident-drawer]') && document.querySelector('[data-testid=nav-resumen]').getAttribute('aria-selected')==='true'"), "back to monitoring");
  await waitFor(() => ev("document.querySelectorAll('[data-testid=ov-feed-tile]').length===2"), "tiles");
  const aAfter = (await jget("/api/feeds")).feeds.find((f) => f.feed_id === pA);
  check("16-17 the active feed kept running during the whole investigation and its incident count is its own (the simulation is not attributed to it)", aAfter.state === "playing" && aAfter.counters.analyzed > aBefore && (await tile(pA, "ov-tile-incidents")) !== null);

  // ---- 13b: responsive investigation
  for (const [name, w, h, mobile] of [["tablet", 820, 1100, true], ["mobile", 390, 844, true]]) {
    await send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile });
    await nav("eventos");
    await waitFor(() => ev("document.querySelectorAll('[data-testid=alert-detail]').length>=1"), `alerts (${name})`, 15000);
    await click("alert-detail");
    await waitFor(() => ev("!!document.querySelector('[data-testid=investigation]')"), `investigation (${name})`, 15000);
    const ovf = await overflow();
    check(`13 ${name}: the investigation fits the viewport without horizontal page scroll and keeps its controls reachable`, ovf <= 1 && (await ev("!!document.querySelector('[data-testid=inv-export]') && !!document.querySelector('[data-testid=incident-close]')")), `overflow=${ovf}`);
    await shot(`p05_incident_${name}.png`);
    await click("incident-close");
    await waitFor(() => ev("!document.querySelector('[data-testid=incident-drawer]')"), "closed");
  }
  await send("Emulation.setDeviceMetricsOverride", { width: 1500, height: 1100, deviceScaleFactor: 1, mobile: false });

  // ---- MG-DELETE-035: the destructive path is visibly unavailable and refuses at the API; nothing is removed
  await nav("sesiones");
  await waitFor(() => ev("!!document.querySelector('[data-testid=del-unavailable]')"), "sessions view");
  check("DELETE-035 the sessions view explains that deletion is temporarily unavailable and all delete controls are disabled", (await text("del-unavailable")).includes("NO DISPONIBLE") && (await ev("[...document.querySelectorAll('[data-testid=del-selected],[data-testid=del-all],[data-testid^=del-session_]')].every(b=>b.disabled)")));
  const dr = await jpost("/api/sessions/delete", { all: true, expected_count: 0, confirm: "ELIMINAR" });
  check("DELETE-035 the API answers 503 with the honest message id and removes nothing", dr.status === 503 && dr.body.includes("delete_temporarily_unavailable"), `${dr.status}`);

  // ---- privacy / locality / errors
  await nav("camara");
  await waitFor(() => ev("!!document.querySelector('[data-testid=privacy-on]')"), "privacy box");
  check("18 Privacy stays ON by default and no camera stream was opened", (await ev("document.querySelector('[data-testid=privacy-on]').checked")) && (await ev("(window.__streams||[]).length")) === 0);
  const foreign = await ev("performance.getEntriesByType('resource').map(r=>new URL(r.name).origin).filter(o=>o!==location.origin)");
  check("18 no request left the local server origin", foreign.length === 0, JSON.stringify([...new Set(foreign)]));
  const errs = await ev("window.__mgErrors||[]");
  check("no uncaught page errors were recorded", errs.length === 0, JSON.stringify(errs.slice(0, 3)));
  await nav("feeds");
  await click("feeds-stop-all");
} catch (e) {
  check("product vertical 06 acceptance ran to completion", false, e.message + " @ " + String(e.stack).split("\n").slice(1, 3).join(" | "));
} finally {
  const failed = results.filter((r) => !r.ok).length;
  fs.writeFileSync(path.join(OUT, "results.json"), JSON.stringify({ base: BASE, when: new Date().toISOString(), results }, null, 1));
  console.log(`\n${results.length - failed}/${results.length} checks passed`);
  try { ws?.close(); } catch { /* ignore */ }
  chrome.kill("SIGTERM");
  await sleep(500);
  process.exit(failed ? 1 : 0);
}
