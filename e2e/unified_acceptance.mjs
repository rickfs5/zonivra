// UNIFIED NIGHT SPRINT 01 journey: real local RF-DETR analysis of the registered MP4 -> provider status (read-only) -> synthetic drill incident -> SIMULATED explanation that cannot change the incident.
// Over the DevTools protocol with the system Google Chrome (no extra packages). Usage: MG_E2E_PORT=<free> bash scripts/run_mvp_e2e.sh e2e/unified_acceptance.mjs  (isolated server and storage; refuses otherwise).
// REAL: only the model analysis of the registered nonpersonal MP4. SYNTHETIC: the scripted drill. SIMULATED: the explanation. No external call is made or possible.
if (!process.env.MG_E2E_ISOLATED) { console.error("refusing to run: use `MG_E2E_PORT=<free port> bash scripts/run_mvp_e2e.sh " + process.argv[1] + "` (isolated server and storage)"); process.exit(3); }
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const BASE = process.argv[2] ?? "http://127.0.0.1:8000";
const PORT = 9351;
const OUT = path.resolve("logs/unified_sprint");
fs.mkdirSync(OUT, { recursive: true });
const profile = fs.mkdtempSync(path.join(os.tmpdir(), "mg-chrome-unified-"));
const chrome = spawn("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", [
  "--headless=new", `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`, "--window-size=1500,1200",
  "--autoplay-policy=no-user-gesture-required", "--no-first-run", "--no-default-browser-check",
    // best-effort offline hardening (not network isolation): see e2e/browser_acceptance.mjs
  "--disable-background-networking", "--disable-component-update", "--disable-sync", "--no-pings", "--disable-domain-reliability",
  "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1 , EXCLUDE localhost",
  "about:blank",
], { stdio: "ignore" });

const results = [];
const notes = [];
const note = (n) => { notes.push(n); console.log(`NOTE  ${n}`); };
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

const DL = fs.mkdtempSync(path.join(os.tmpdir(), "mg-unified-dl-"));

const lifeApi = async (fid) => jget(`/api/feeds/${fid}/lifecycle`);
const lifeKinds = (d) => d.events.map((e) => e.kind);
const uiLifeKinds = () => ev("[...document.querySelectorAll('[data-testid=feed-life-list] li')].map(e=>e.dataset.kind)");
const openLife = async (fid) => { await ev(`(()=>{const d=document.querySelector('[data-feed-id=${JSON.stringify(fid)}] [data-testid=feed-life]');if(d&&!d.open){d.querySelector('summary').click()}return true})()`); };
const tile = (fid, tid, a) => ev(`document.querySelector('[data-testid=ov-feed-tile][data-feed-id=${JSON.stringify(fid)}] [data-testid=${tid}]')?.${a ? `getAttribute(${JSON.stringify(a)})` : "textContent"}`);
const feedState = async (fid) => (await jget("/api/feeds")).feeds.find((f) => f.feed_id === fid);
let aid = null;
try {
  await connect();
  await send("Page.enable");
  await send("Page.addScriptToEvaluateOnNewDocument", { source: "window.__mgErrors=[];window.addEventListener('error',e=>window.__mgErrors.push(String(e.message)));window.addEventListener('unhandledrejection',e=>window.__mgErrors.push('rejection: '+String(e.reason)));" });
  await send("Emulation.setDeviceMetricsOverride", { width: 1500, height: 1100, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url: BASE });
  await waitFor(() => ev("document.querySelector('h1')?.textContent.includes('MineGuard')"), "page");

  // ---- U1: provider status is inspectable, honest and has no control that enables anything
  await nav("configuracion");
  await waitFor(() => ev("!!document.querySelector('[data-testid=providers-panel]') && document.querySelectorAll('[data-testid=providers-table] tbody tr').length===5"), "providers panel", 20000);
  const prov = await jget("/api/providers");
  const ids = await ev("[...document.querySelectorAll('[data-testid=providers-table] tbody tr')].map(r=>r.dataset.testid)");
  check("U1 the Configuración view lists the five providers", JSON.stringify(ids) === JSON.stringify(["provider-local_rfdetr", "provider-meta_sam31", "provider-muse_spark", "provider-baseten", "provider-modal"]), JSON.stringify(ids));
  const states = await ev("Object.fromEntries([...document.querySelectorAll('[data-testid=providers-table] tbody tr')].map(r=>[r.dataset.testid,[...r.querySelectorAll('[data-state]')].map(e=>e.dataset.state)]))");
  check("U1 the UI shows the server's states: local ready (weights verified), Meta disabled/unconfigured/unbilled/not verified, Muse simulated", states["provider-local_rfdetr"].join() === "LOCAL_READY" && states["provider-meta_sam31"].includes("META_DISABLED") && states["provider-meta_sam31"].includes("CREDENTIALS_NOT_CONFIGURED") && states["provider-meta_sam31"].includes("BILLING_NOT_APPROVED") && states["provider-meta_sam31"].includes("LIVE_NOT_VERIFIED") && states["provider-muse_spark"].join() === "SIMULATED", JSON.stringify(states));
  check("U1 the panel offers NO control (no button, input, select, switch): nothing in the UI can enable a provider or spending", (await ev("document.querySelectorAll('[data-testid=providers-panel] button, [data-testid=providers-panel] input, [data-testid=providers-panel] select, [data-testid=providers-panel] a').length")) === 0);
  check("U1 cost block: 0 external calls, US$ 0, NullRealTransport, and no 'zero charge guaranteed' claim", (await text("providers-cost")).includes("US$ 0") && (await text("providers-cost")).includes("NullRealTransport") && prov.cost.zero_charge_guaranteed === false && prov.cost.ui_can_enable_spending === false);
  await ev("document.querySelector('[data-testid=providers-panel]').scrollIntoView()"); await sleep(300);
  await shot("u1_providers.png");

  // ---- U2: REAL local inference of the registered MP4 through the product's own multi-source path
  await nav("feeds");
  await waitFor(() => ev("document.querySelector('[data-testid=feeds-video]')?.options.length>0 && !document.querySelector('[data-testid=feeds-add]').disabled"), "feeds form", 20000);
  await click("feeds-add");
  await waitFor(async () => (await panelIds()).length === 1, "feed", 20000);
  const [pA] = await panelIds();
  await panelClick(pA, "feed-start");
  await waitFor(async () => (await analyzed(pA)) >= 2, "feed analysed by the real model", 240000);
  const fa = await feedState(pA);
  const modelId = (await jget("/api/model")).model_id;
  check("U2 the MP4 is analysed by the real local model (RF-DETR): counted samples, provenance real_inference, model named", fa.counters.analyzed >= 2 && fa.source.provenance === "real_inference" && !!modelId && prov.providers[0].model_id === modelId, `analyzed=${fa.counters.analyzed} model=${modelId}`);
  note(`real-model class histogram so far: ${JSON.stringify(fa.class_hist)} (no RED is faked from it)`);
  await shot("u2_real_analysis.png");
  // U2b: an operator-defined zone covering the whole frame, so the REAL detections can meet the deterministic rules (the zone is a test choice; nothing is faked: events come from the kernel over real detections)
  const f0 = await feedState(pA);
  const fullFrame = { zone_id: "Z1", kind: "restricted", polygon: [[0, 0], [1280, 0], [1280, 720], [0, 720]], name: "Todo el cuadro (prueba)" };
  const put = await api(`/api/feeds/${pA}/zones`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ zones: [fullFrame], expected_revision: f0.zones_revision }) });
  check("U2b the operator-defined full-frame zone was accepted by the zone API", put.status === 200, `${put.status} ${String(put.body).slice(0, 160)}`);
  const realAlert = await waitFor(async () => (await jget("/api/alerts?limit=200")).alerts.find((x) => x.feed_id === pA && !x.simulation && x.kind === "risk_event"), "a real-model risk alert", 90000).catch(() => null);
  if (!realAlert) note("NOT_EXERCISED: the real model produced no risk event in the full-frame zone within the wait; the real-incident explanation path is covered by the unit tests only");
  else {
    await nav("eventos");
    await waitFor(() => ev(`!!document.querySelector('[data-alert-id=${JSON.stringify(realAlert.alert_id)}] [data-testid=alert-detail]')`), "real alert row", 30000);
    await ev(`document.querySelector('[data-alert-id=${JSON.stringify(realAlert.alert_id)}] [data-testid=alert-detail]').click()`);
    await waitFor(() => ev("!!document.querySelector('[data-testid=incident-drawer]')"), "real incident drawer", 20000);
    const rb = await jget(`/api/incidents/${realAlert.alert_id}`);
    check("U2b the incident of the real-model alert is classified as live-feed model inference (not synthetic)", rb.classification === "live_feed_metadata" && rb.provenance.provider.kind === "model_inference" && rb.provenance.simulation === false, `${rb.classification} ${rb.provenance.provider.kind}`);
    await click("explanation-ask");
    await waitFor(() => ev("!!document.querySelector('[data-testid=explanation-result]') || !!document.querySelector('[data-testid=explanation-rejected]')"), "real explanation", 20000);
    const rsum = await text("explanation-summary");
    check("U2b the SIMULATED explanation of a REAL-model incident names the local model origin, not a scripted one, and keeps the engine's severity", (await text("explanation-badge")).includes("SIMULADA") && rsum.includes("analisis local del modelo") && !rsum.includes("no es inferencia real") && (await text("explanation-result")).includes(rb.risk.severity), rsum);
    const ra = await jget(`/api/incidents/${realAlert.alert_id}`);
    check("U2b the real incident is unchanged by asking for the explanation", JSON.stringify({ ...rb, generated_utc: 0 }) === JSON.stringify({ ...ra, generated_utc: 0 }));
    await shot("u2b_real_incident_explanation.png");
    await click("incident-close");
    await nav("feeds");
  }
  await click("feeds-stop-all");
  await waitFor(async () => (await panelAttr(pA, "feed-state", "data-state")) !== "playing", "stopped", 30000);

  // ---- U3: stored single-model run (the base of the scripted drill) is model inference, not a simulation
  const vid = (await jget("/api/videos"))[0].video_id;
  const job = JSON.parse((await jpost(`/api/videos/${vid}/analyze`, { stride: 30, threshold: 0.3 })).body).job_id;
  const done = await waitFor(async () => { const j = await jget(`/api/jobs/${job}`); return ["done", "failed", "cancelled"].includes(j.status) ? j : null; }, "stored analysis", 300000);
  check("U3 the stored analysis of the registered MP4 completed with the real model", done.status === "done" && !!done.run_id, JSON.stringify(done).slice(0, 160));
  const exp = JSON.stringify(await jget(`/api/runs/${done.run_id}/export`));
  check("U3 the stored run is labelled model_inference and never synthetic_scripted", exp.includes("model_inference") && !exp.includes("synthetic_scripted"));

  // ---- U4: the scripted (SYNTHETIC) drill -> incident -> SIMULATED explanation that cannot change the incident
  await nav("simulaciones");
  await waitFor(() => ev("document.querySelector('[data-testid=sim-red-view]')?.disabled===false"), "lab ready", 30000);
  await click("sim-red-view");
  await waitFor(() => ev("!!document.querySelector('[data-testid=sim-result]')"), "lab result", 60000);
  check("U4 the drill is labelled SINTÉTICO", (await text("sim-banner")).includes("SINTÉTICO"));
  await waitFor(() => ev("document.querySelectorAll('[data-testid=sim-alert]').length>=1"), "derived alert", 15000);
  await click("sim-alert-detail");
  await waitFor(() => ev("!!document.querySelector('[data-testid=incident-drawer]')"), "incident drawer");
  aid = await attr("incident-drawer", "data-alert-id");
  const before = await jget(`/api/incidents/${aid}`);
  check("U4 before asking, the drawer states that a real-model explanation is NOT available and shows no explanation", (await text("explanation-panel")).includes("NO disponible") && (await ev("!document.querySelector('[data-testid=explanation-result]')")));
  await click("explanation-ask");
  await waitFor(() => ev("!!document.querySelector('[data-testid=explanation-result]') || !!document.querySelector('[data-testid=explanation-rejected]')"), "explanation", 20000);
  const sevShown = await ev("document.querySelector('[data-testid=incident-drawer] .badge')?.className||''");
  check("U4 the explanation is accepted, badged SIMULADA, states the severity is decided by the deterministic engine, and names the scripted origin", (await text("explanation-badge")).includes("SIMULADA") && (await text("explanation-result")).includes(before.risk.severity) && (await text("explanation-summary")).includes("simulacion con guion"), (await text("explanation-summary")));
  check("U4 the explanation says it did not inspect video/images and that validation is structural/lexical only", (await text("explanation-result")).includes("no se llamo a ningun modelo") && (await text("explanation-validation")).includes("estructural y léxica"));
  const after = await jget(`/api/incidents/${aid}`);
  const strip = (o) => JSON.stringify({ ...o, generated_utc: 0 });
  check("U4 asking for the explanation did not change the incident (same severity and record)", strip(before) === strip(after) && sevShown.includes(`badge-${before.risk.severity}`));
  // Recovery 02 / step 7: the incident evidence export (structured JSON, no footage) keeps the SCRIPTED origin and carries no explanation text
  const iexp = await jget(`/api/incidents/${aid}/export`);
  const iexpTxt = JSON.stringify(iexp);
  check("U4 the incident evidence export is mg.incident.export/1, keeps the synthetic classification, states it is not proof of authenticity, and contains no explanation", iexp.schema === "mg.incident.export/1" && iexp.incident?.classification === "synthetic" && iexp.incident?.risk?.severity === before.risk.severity && iexpTxt.includes("not_independently_verified") && !iexpTxt.includes("Resumen simulado"), iexpTxt.slice(0, 160));
  await ev("document.querySelector('[data-testid=explanation-panel]').scrollIntoView()"); await sleep(300);
  await shot("u4_explanation.png");
  // integration review 1: a LATE explanation response for incident A must not appear under incident B
  const idA = await attr("incident-drawer", "data-alert-id");
  await ev(`(()=>{window.__f9=window.fetch;window.fetch=async(u,o)=>{if(String(u).includes('/api/incidents/${idA}/explanation')){await new Promise(r=>setTimeout(r,2500));}return window.__f9(u,o);};})()`);
  await click("explanation-ask");
  const moved = (await click("incident-next")) || (await click("incident-prev"));
  await waitFor(async () => (await attr("incident-drawer", "data-alert-id")) !== idA, "the drawer switched to the other incident", 15000);
  await sleep(3500);
  check("a late explanation response for another incident does not appear under the selected one (and the button is usable again)", moved && (await ev("!document.querySelector('[data-testid=explanation-result]') && !document.querySelector('[data-testid=explanation-rejected]')")) && (await ev("document.querySelector('[data-testid=explanation-ask]').disabled")) === false);
  await ev("window.fetch=window.__f9");
  await click("incident-close");

  // ---- U5: no request left the origin; no page errors; privacy ON and no camera
  const foreign = await ev("performance.getEntriesByType('resource').map(r=>new URL(r.name).origin).filter(o=>o!==location.origin)");
  check("U5 no request left the local server origin", foreign.length === 0, JSON.stringify([...new Set(foreign)]));
  const errs = await ev("window.__mgErrors||[]");
  check("U5 no uncaught page errors were recorded", errs.length === 0, JSON.stringify(errs.slice(0, 3)));
  await nav("camara");
  await waitFor(() => ev("!!document.querySelector('[data-testid=privacy-on]')"), "privacy box");
  check("U5 Privacy stays ON by default and no camera stream was opened", (await ev("document.querySelector('[data-testid=privacy-on]').checked")) && (await ev("(window.__streams||[]).length")) === 0);
} catch (e) {
  check("unified acceptance ran to completion", false, e.message + " @ " + String(e.stack).split("\n").slice(1, 3).join(" | "));
} finally {
  const failed = results.filter((r) => !r.ok).length;
  fs.writeFileSync(path.join(OUT, "results.json"), JSON.stringify({ base: BASE, when: new Date().toISOString(), alert_id: aid, notes, results }, null, 1));
  console.log(`\n${results.length - failed}/${results.length} checks passed`);
  try { ws?.close(); } catch { /* ignore */ }
  chrome.kill("SIGTERM");
  await sleep(500);
  process.exit(failed ? 1 : 0);
}
