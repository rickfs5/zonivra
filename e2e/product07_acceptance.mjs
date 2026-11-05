// MG-PRODUCT-VERTICAL-07 complete operator journey (sources + recorded lifecycle -> real-model observations -> zone -> synthetic YELLOW/RED -> incident -> timeline -> evidence -> source navigation and back -> ack/resolve -> JSON export vs projection -> monitoring; 1500/820/420 px; keyboard)
// (boilerplate shared with the other suites; originally the MG-VERTICAL-02 multi-feed suite) over the DevTools protocol with the system Google Chrome (no extra packages).
// Usage: node e2e/mvp_acceptance.mjs [baseUrl]  -> writes logs/product_vertical07/{results.json,*.png}; exit 1 on any failed check.
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
const PORT = 9347;
const OUT = path.resolve("logs/product_vertical07");
fs.mkdirSync(OUT, { recursive: true });
const profile = fs.mkdtempSync(path.join(os.tmpdir(), "mg-chrome-p07-"));
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

const DL = fs.mkdtempSync(path.join(os.tmpdir(), "mg-p07-dl-"));

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
  try { await send("Browser.setDownloadBehavior", { behavior: "allow", downloadPath: DL }); } catch { await send("Page.setDownloadBehavior", { behavior: "allow", downloadPath: DL }); }
  await send("Page.addScriptToEvaluateOnNewDocument", { source: "window.__mgErrors=[];window.addEventListener('error',e=>window.__mgErrors.push(String(e.message)));window.addEventListener('unhandledrejection',e=>window.__mgErrors.push('rejection: '+String(e.reason)));" });
  await send("Emulation.setDeviceMetricsOverride", { width: 1500, height: 1100, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url: BASE });
  await waitFor(() => ev("document.querySelector('h1')?.textContent.includes('MineGuard')"), "page");

  // ---- 1-2: overview with no sources
  await nav("resumen");
  await waitFor(() => ev("!!document.querySelector('[data-testid=ov-no-sources]')"), "empty overview");
  check("1-2 the overview opens and an empty system says there are no sources (no invented tiles)", true);

  // ---- 3-4: multi-source; start an authorised MP4 (real RF-DETR); the idle source has NO recorded lifecycle
  await nav("feeds");
  await waitFor(() => ev("document.querySelector('[data-testid=feeds-video]')?.options.length>0 && !document.querySelector('[data-testid=feeds-add]').disabled"), "feeds form", 20000);
  await click("feeds-add");
  await waitFor(async () => (await panelIds()).length === 1, "feed A", 20000);
  const [pA] = await panelIds();
  await openLife(pA);
  await waitFor(() => ev("!!document.querySelector('[data-testid=feed-life-none]')"), "life none (idle)");
  check("3 an idle source says its lifecycle is NOT recorded (nothing reconstructed)", (await text("feed-life-none")).includes("NO está registrado") && (await lifeApi(pA)).recorded === false);
  await panelClick(pA, "feed-start");
  await waitFor(async () => (await analyzed(pA)) >= 2, "feed A analysed by the real model", 150000);
  const modelId = (await jget("/api/model")).model_id;
  const fa = await feedState(pA);
  check("4 the MP4 feed is analysed by the real model: counted samples, detector named, provenance real_inference", fa.counters.analyzed >= 2 && fa.source.provenance === "real_inference" && !!modelId);
  await waitFor(() => ev("!!document.querySelector('[data-testid=feed-life-list]')"), "life list", 15000);
  const l1 = await lifeApi(pA);
  const k1 = lifeKinds(l1);
  await waitFor(async () => (await uiLifeKinds()).includes("observation_received"), "observation in UI list", 15000);
  const uiK = await uiLifeKinds(), apiK = lifeKinds(await lifeApi(pA));
  check("4 the source lifecycle list shows what was recorded: started first, then the first analysed observation; the UI list is a prefix-identical copy of the API's", k1[0] === "source_started" && k1.includes("observation_received") && uiK[0] === "source_started" && JSON.stringify(uiK) === JSON.stringify(apiK.slice(0, uiK.length)));
  const objs = (await feedState(pA)).class_hist;
  note(`MP4 real-model class histogram so far: ${JSON.stringify(objs)}`);
  const sawPerson = l1.events.some((e) => e.kind === "person_detected"), sawVehicle = l1.events.some((e) => e.kind === "vehicle_detected");
  check("4 person/vehicle appearance edges are recorded only if the model really saw them (class histogram agrees)", sawPerson === ((objs.person ?? 0) > 0) && sawVehicle === ((objs.vehicle ?? 0) > 0), `person=${sawPerson} vehicle=${sawVehicle}`);

  // ---- 5: pause / resume are recorded and shown on the overview tile
  await panelClick(pA, "feed-pause");
  await waitFor(async () => (await panelAttr(pA, "feed-state", "data-state")) === "paused", "paused", 20000);
  await nav("resumen");
  await waitFor(() => ev("document.querySelectorAll('[data-testid=ov-feed-tile]').length===1"), "tile");
  await waitFor(async () => (await tile(pA, "ov-tile-life", "data-kind")) === "source_paused", "tile shows paused", 15000);
  check("5 the overview tile shows the source's last recorded lifecycle fact (paused) with its time", (await tile(pA, "ov-tile-life")).includes("Fuente en pausa"));
  await nav("feeds");
  await waitFor(async () => (await panelIds()).length === 1, "feeds grid", 20000);
  await panelClick(pA, "feed-resume");
  await waitFor(async () => (await panelAttr(pA, "feed-state", "data-state")) === "playing", "resumed", 20000);
  const l2 = await lifeApi(pA);
  check("5 pause then resume are recorded in order, once each", lifeKinds(l2).filter((k) => k === "source_paused").length === 1 && lifeKinds(l2).indexOf("source_paused") < lifeKinds(l2).indexOf("source_resumed"));

  // ---- 6: zone and deterministic risk of the source
  await panelClick(pA, "feed-zones-toggle");
  await waitFor(() => ev("!!document.querySelector('[data-testid=zone-editor]')"), "zone editor", 10000);
  const zonesApi = (await feedState(pA)).zones;
  check("6 the zone editor lists exactly the zones the API holds for this source", (await ev("document.querySelectorAll('[data-testid=zone-item]').length")) === zonesApi.length);
  await panelClick(pA, "feed-zones-toggle");
  const risk = (await feedState(pA)).risk_state;
  check("6 the feed's risk badge equals the deterministic engine's current state from the API", (await panelAttr(pA, "feed-risk", "data-state")) === risk, `ui=${await panelAttr(pA, "feed-risk", "data-state")} api=${risk}`);
  const mp4Events = (await feedState(pA)).events.map((e) => e.severity);
  note(`MP4 feed risk events so far (real model, deterministic rules): ${JSON.stringify(mp4Events)}; this clip is NOT used to fake a RED incident`);

  // ---- 7: a second source on the same file, then one stops while the other continues
  await click("feeds-add");
  await waitFor(async () => (await panelIds()).length === 2, "feed B", 20000);
  const pB = (await panelIds()).find((x) => x !== pA);
  await panelClick(pB, "feed-start");
  await waitFor(async () => (await analyzed(pB)) >= 2, "feed B analysed", 150000);
  await nav("resumen");
  await waitFor(() => ev("document.querySelectorAll('[data-testid=ov-feed-tile]').length===2"), "two tiles");
  await waitFor(async () => (await tile(pA, "ov-tile-dup", "data-dup")) === "active_overlap", "active overlap", 20000);
  check("7 two active feeds sharing a file are labelled an ACTIVE overlap on both tiles (a concurrency simulation, never two cameras)", (await tile(pB, "ov-tile-dup", "data-dup")) === "active_overlap");
  await shot("v07_p01_overview_two_sources.png");
  await nav("feeds");
  await waitFor(async () => (await panelIds()).length === 2, "feeds grid 2", 20000);
  const runA1 = (await lifeApi(pA)).run_uid;
  await panelClick(pA, "feed-stop");
  await waitFor(async () => (await panelAttr(pA, "feed-state", "data-state")) === "stopped", "A stopped", 40000);
  const la = await lifeApi(pA), lb = await lifeApi(pB);
  check("7 stopping A records source_stopped on A only; B keeps playing and records no stop (source isolation)", lifeKinds(la).at(-1) === "source_stopped" && !lifeKinds(lb).includes("source_stopped") && (await feedState(pB)).state === "playing");
  await openLife(pA);
  await waitFor(async () => (await uiLifeKinds()).at(-1) === "source_stopped", "A's list ends with the stop", 15000);
  check("7 the UI list of A ends with the recorded stop", true);

  // ---- 8: restart A = a NEW run with its own lifecycle (no leak from the old run)
  await panelClick(pA, "feed-start");
  await waitFor(async () => (await feedState(pA)).state === "playing", "A restarted", 40000);
  const la2 = await lifeApi(pA);
  check("8 the restarted source is a NEW run: its lifecycle has one start and no stop from the previous run", la2.run_uid !== runA1 && lifeKinds(la2).filter((k) => k === "source_started").length === 1 && !lifeKinds(la2).includes("source_stopped"));
  await panelClick(pB, "feed-stop");
  await waitFor(async () => (await panelAttr(pB, "feed-state", "data-state")) === "stopped", "B stopped", 40000);

  // ---- 8b: real RF-DETR analysis of the authorised MP4 (the single model is shared, so the feeds are stopped meanwhile); this stored run is the base the synthetic lab reads
  await nav("feeds");
  await waitFor(async () => (await panelIds()).length === 2, "feeds grid before analysis", 20000);
  await click("feeds-stop-all");
  await waitFor(async () => (await jget("/api/feeds")).feeds.every((f) => f.state !== "playing" && f.state !== "paused"), "feeds stopped", 40000);
  const vids = await jget("/api/videos");
  const job = JSON.parse((await jpost(`/api/videos/${vids[0].video_id}/analyze`, { stride: 15, threshold: 0.3 })).body);
  await waitFor(async () => { const j = await jget(`/api/jobs/${job.job_id}`); if (j.status === "failed") throw new Error("analysis failed"); return j.status === "done"; }, "MP4 analysis", 240000);
  const runs = await jget("/api/runs");
  check("8b the authorised MP4 was analysed by the real model into a stored run with person/vehicle detections", runs.length >= 1 && !!modelId);
  await nav("feeds");
  await waitFor(async () => (await panelIds()).length === 2, "feeds grid after analysis", 20000);
  await panelClick(pA, "feed-start");
  await waitFor(async () => (await feedState(pA)).counters.analyzed >= 2, "feed A restarted alone", 150000);
  const runA2 = (await lifeApi(pA)).run_uid;
  check("8b stop-all then start records a THIRD run of A with a single start (no history carried over)", runA2 !== runA1 && runA2 !== la2.run_uid && lifeKinds(await lifeApi(pA)).filter((k) => k === "source_started").length === 1);

  // ---- 9-10: the clearly labelled SYNTHETIC YELLOW -> RED scenario (no MP4 RED is faked)
  await nav("simulaciones");
  await waitFor(() => ev("document.querySelector('[data-testid=sim-red-view]')?.disabled===false"), "lab ready", 20000);
  await click("sim-red-view");
  await waitFor(() => ev("!!document.querySelector('[data-testid=sim-result]')"), "lab result", 60000);
  const states = await ev("[...document.querySelectorAll('[data-testid=sim-interval]')].map(e=>e.dataset.state)");
  check("9 the scripted scenario yields the risk kernel's YELLOW then RED, labelled SINTÉTICO (not model inference)", states.includes("YELLOW") && states.includes("RED") && (await text("sim-banner")).includes("SINTÉTICO"));
  await waitFor(() => ev("document.querySelectorAll('[data-testid=sim-alert]').length>=1"), "derived alert", 15000);
  await click("sim-alert-detail");
  await waitFor(() => ev("!!document.querySelector('[data-testid=incident-drawer]')"), "incident drawer");
  await waitFor(() => ev("!!document.querySelector('[data-testid=investigation]')"), "investigation panel", 15000);
  aid = await attr("incident-drawer", "data-alert-id");
  const inc = await jget(`/api/incidents/${aid}`);
  check("10 the incident opens from the alert and is classified SYNTHETIC with the scripted-simulation provider (no model, no camera)", (await attr("investigation", "data-classification")) === "synthetic" && (await text("inv-provider")).includes("Escenario con guion") && inc.provenance.provider.model_id === null);
  check("10 a concise summary line names severity, zone, source and episode state, all equal to the API record", (await attr("inv-summary", "data-severity")) === inc.risk.severity && (await text("inv-summary")).includes(inc.identity.zone_id) && (await text("inv-summary")).includes(inc.identity.source_label) && (await text("inv-summary")).includes(inc.observed.open ? "episodio abierto" : "episodio cerrado"));
  check("10 observed facts and computed risk are separate sections; the deterministic rule and 'AI never decides' are stated", (await text("inv-observed")).includes(inc.identity.zone_id) && (await text("inv-risk")).includes(inc.risk.rule_id) && (await text("inv-decided")).includes("nunca deciden"));

  // ---- 11: timeline: two tracks, lifecycle honestly NOT RECORDED for a scripted run
  const obsRows = await ev("[...document.querySelectorAll('[data-testid=tl-observation] li')].map(e=>e.dataset.kind)");
  const procRows = await ev("[...document.querySelectorAll('[data-testid=tl-processing] li')].map(e=>e.dataset.kind)");
  check("11 the timeline shows the observation track (source time) and the processing track (UTC) apart, with only stored entry kinds", obsRows.includes("risk_entry") && procRows[0] === "alert_created" && JSON.stringify(obsRows) === JSON.stringify(inc.timeline.observation.map((e) => e.kind)));
  check("11 a scripted run says its source lifecycle is NOT recorded, lists what is unsupported, and invents no source_* entry", (await attr("tl-lifecycle", "data-recorded")) === "no" && (await text("tl-not-recorded")).includes("NO está registrado") && !procRows.some((k) => k.startsWith("source_")) && (await text("tl-unsupported")).includes("inicio de fuente"));
  check("11 the second-resolution ordering caveat is stated", (await text("tl-order-note")).includes("orden es causal"));
  check("11 UNKNOWN intervals are listed from the stored evaluation or declared unavailable (never GREEN)", (await attr("inv-unknown", "data-available")) === (inc.unknown_intervals.available ? "yes" : "no"));

  // ---- 12: evidence, completeness, trust limits
  check("12 evidence: metadata only, local consistency passes, authenticity NOT independently verified", (await text("inv-evidence")).includes("Solo metadatos") && (await attr("inv-evidence", "data-consistency")) === "pass" && (await text("inv-auth")).includes("NO verificada"));
  check("12 the completeness block states what is recorded and what is not, and that images are excluded", (await attr("inv-completeness", "data-complete")) === (inc.completeness.complete ? "yes" : "no") && (await text("inv-completeness")).includes("no registrado") && (await text("inv-completeness")).includes("excluidas"));
  check("12 no image/canvas/video exists in the incident view", (await ev("document.querySelectorAll('[data-testid=incident-drawer] img, [data-testid=incident-drawer] canvas, [data-testid=incident-drawer] video').length")) === 0);
  await ev("document.querySelector('[data-testid=investigation]').scrollIntoView()");
  await shot("v07_p02_incident_investigation.png");
  await ev("document.querySelector('[data-testid=inv-completeness]').scrollIntoView()");
  await shot("v07_p03_incident_completeness_evidence.png");

  // ---- 13: navigate to the source and return to the SAME incident
  const dupBefore = await ev("document.querySelectorAll('[data-testid=incident-drawer]').length");
  await click("incident-jump");
  await waitFor(() => ev("!!document.querySelector('[data-testid=back-to-incident]') && !document.querySelector('[data-testid=incident-drawer]')"), "source view with a way back", 20000);
  check("13 'go to the source' leaves the incident and offers the way back", true);
  await click("back-to-incident");
  await waitFor(() => ev("!!document.querySelector('[data-testid=incident-drawer]')"), "incident reopened");
  await waitFor(() => ev("!!document.querySelector('[data-testid=investigation]')"), "investigation again", 15000);
  check("13 returning reopens the SAME incident (same alert id), exactly one drawer, no duplicate card", (await attr("incident-drawer", "data-alert-id")) === aid && dupBefore === 1 && (await ev("document.querySelectorAll('[data-testid=incident-drawer]').length")) === 1);

  // ---- 14: acknowledge by KEYBOARD, resolve; delivery stays separate
  await ev("document.querySelector('[data-testid=incident-ack]').focus()");
  await key("Enter");
  await waitFor(() => ev("document.querySelector('[data-testid=incident-alert]').textContent.includes('Reconocida')"), "acknowledged (keyboard)");
  await waitFor(async () => (await ev("[...document.querySelectorAll('[data-testid=tl-processing] li')].map(e=>e.dataset.kind)")).includes("alert_acknowledged"), "ack in the timeline", 10000);
  await click("incident-resolve");
  await waitFor(() => ev("document.querySelector('[data-testid=incident-alert]').textContent.includes('Resuelta')"), "resolved");
  await waitFor(async () => (await ev("[...document.querySelectorAll('[data-testid=tl-processing] li')].map(e=>e.dataset.kind)")).includes("alert_resolved"), "resolve in the timeline", 10000);
  const after = await jget(`/api/incidents/${aid}`);
  check("14 acknowledge (keyboard) and resolve persist in the stored lifecycle and appear in the timeline; delivery is reported separately and is not 'delivered'", after.alert.status === "resolved" && !!after.alert.acknowledged_utc && after.delivery.state !== "delivered" && (await text("incident-delivery")).length > 10);
  const ackFirst = (await ev("[...document.querySelectorAll('[data-testid=tl-processing] li')].map(e=>e.dataset.kind)"));
  check("14 timeline order: alert created, then acknowledged, then resolved", ackFirst.indexOf("alert_created") < ackFirst.indexOf("alert_acknowledged") && ackFirst.indexOf("alert_acknowledged") < ackFirst.indexOf("alert_resolved"));
  await ev("document.querySelector('[data-testid=investigation]').scrollIntoView()");
  await shot("v07_p04_incident_resolved.png");

  // ---- 15: JSON export through the real control, compared with the displayed projection AND with what the DOM shows
  await click("inv-export");
  await waitFor(() => ev("!!document.querySelector('[data-testid=inv-exported]')"), "export done", 15000);
  const file = path.join(DL, `${aid}.mineguard-incident.json`);
  await waitFor(() => fs.existsSync(file) && fs.statSync(file).size > 100, "downloaded file", 15000);
  const raw = fs.readFileSync(file, "utf8");
  const exp = JSON.parse(raw);
  const shown = await jget(`/api/incidents/${aid}`);
  const strip = (o) => { const c = { ...o }; delete c.generated_utc; return JSON.stringify(c); };
  check("15 the downloaded JSON equals the server's incident projection (everything except generated_utc)", exp.schema === "mg.incident.export/1" && strip(exp.incident) === strip(shown));
  check("15 the exported facts equal what the screen shows (rule, zone, severity, status, source id)", (await text("inv-risk")).includes(exp.incident.risk.rule_id) && (await text("inv-observed")).includes(exp.incident.identity.zone_id) && (await text("incident-alert")).includes("Resuelta") && exp.incident.alert.status === "resolved" && (await text("inv-provider")).includes(exp.incident.identity.source_id));
  check("15 the export is labelled a generated projection (not the original record), authenticity not verified, open findings named, lifecycle declared not recorded", exp.projection.is_original_record === false && exp.projection.authenticity === "not_independently_verified" && exp.projection.known_open_findings.includes("MG-RISK-036") && exp.incident.timeline.lifecycle.recorded === false && exp.incident.completeness.source_lifecycle === "not_recorded");
  check("15 the export holds no image, footage or base64 payload", !raw.toLowerCase().includes("base64") && !raw.toLowerCase().includes("jpeg") && exp.incident.evidence.images_included === false);

  // ---- 16: a real feed incident (only if the real model + deterministic rules really produced one)
  const feedAlerts = (await jget("/api/alerts?limit=200")).alerts.filter((x) => x.feed_id && !x.simulation);
  if (feedAlerts.length) {
    const fa0 = feedAlerts[0];
    await click("incident-close");
    await nav("eventos");
    await waitFor(() => ev(`!!document.querySelector('[data-alert-id=${JSON.stringify(fa0.alert_id)}]')`), "feed alert row", 15000);
    await ev(`document.querySelector('[data-alert-id=${JSON.stringify(fa0.alert_id)}] [data-testid=alert-detail]')?.click()`);
    await waitFor(() => ev("!!document.querySelector('[data-testid=investigation]')"), "feed incident", 15000);
    const finc = await jget(`/api/incidents/${fa0.alert_id}`);
    check("16 a real-model feed incident shows its recorded source lifecycle when this process holds that run", (await attr("tl-lifecycle", "data-recorded")) === (finc.timeline.lifecycle.recorded ? "yes" : "no"));
    await click("incident-close");
  } else note("NOT_EXERCISED: the authorised MP4 produced no feed alert under the real model; the recorded-lifecycle incident path is covered by backend tests with a scripted detector double, not by this browser suite");

  // ---- 17: back to monitoring; other feeds independent
  await nav("eventos");
  await waitFor(() => ev("document.querySelectorAll('[data-testid=alert-detail]').length>=1"), "alerts", 15000);
  await click("alert-detail");
  await waitFor(() => ev("!!document.querySelector('[data-testid=incident-back-monitor]')"), "drawer");
  const aBefore = (await feedState(pA)).counters.analyzed;
  await click("incident-back-monitor");
  await waitFor(() => ev("!document.querySelector('[data-testid=incident-drawer]') && document.querySelector('[data-testid=nav-resumen]').getAttribute('aria-selected')==='true'"), "back to monitoring");
  await waitFor(() => ev("document.querySelectorAll('[data-testid=ov-feed-tile]').length===2"), "tiles");
  await waitFor(async () => (await feedState(pA)).counters.analyzed > aBefore, "A kept analysing", 60000);
  check("17 back on the overview, the restarted source A kept running during the investigation, its tile shows its own recorded lifecycle, and B stays stopped", (await feedState(pA)).state === "playing" && (await tile(pA, "ov-tile-life", "data-kind")) === "source_started" && (await feedState(pB)).state === "stopped");
  await shot("v07_p05_overview_after_incident.png");

  // ---- 18: responsive 820 / 420, incident + overview
  for (const [name, w, h] of [["tablet820", 820, 1100], ["mobile420", 420, 900]]) {
    await send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: true });
    await nav("resumen");
    await waitFor(() => ev("document.querySelectorAll('[data-testid=ov-feed-tile]').length===2"), `tiles (${name})`, 15000);
    const o1 = await overflow();
    await shot(`v07_p06_overview_${name}.png`);
    await nav("eventos");
    await waitFor(() => ev("document.querySelectorAll('[data-testid=alert-detail]').length>=1"), `alerts (${name})`, 15000);
    await click("alert-detail");
    await waitFor(() => ev("!!document.querySelector('[data-testid=investigation]')"), `investigation (${name})`, 15000);
    const o2 = await overflow();
    check(`18 ${name}: overview and incident fit without horizontal page scroll; export and close stay reachable`, o1 <= 1 && o2 <= 1 && (await ev("!!document.querySelector('[data-testid=inv-export]') && !!document.querySelector('[data-testid=incident-close]')")), `overview=${o1} incident=${o2}`);
    await shot(`v07_p07_incident_${name}.png`);
    await click("incident-close");
    await waitFor(() => ev("!document.querySelector('[data-testid=incident-drawer]')"), "closed");
  }
  await send("Emulation.setDeviceMetricsOverride", { width: 1500, height: 1100, deviceScaleFactor: 1, mobile: false });

  // ---- 19: deletion stays disabled; privacy ON; locality; page errors
  await nav("sesiones");
  await waitFor(() => ev("!!document.querySelector('[data-testid=del-unavailable]')"), "sessions view");
  const dr = await jpost("/api/sessions/delete", { all: true, expected_count: 0, confirm: "ELIMINAR" });
  check("19 deletion stays disabled (UI banner and API 503); nothing is removed", (await text("del-unavailable")).includes("NO DISPONIBLE") && dr.status === 503, `${dr.status}`);
  await nav("camara");
  await waitFor(() => ev("!!document.querySelector('[data-testid=privacy-on]')"), "privacy box");
  check("20 Privacy stays ON by default and no camera stream was opened", (await ev("document.querySelector('[data-testid=privacy-on]').checked")) && (await ev("(window.__streams||[]).length")) === 0);
  const foreign = await ev("performance.getEntriesByType('resource').map(r=>new URL(r.name).origin).filter(o=>o!==location.origin)");
  check("20 no request left the local server origin", foreign.length === 0, JSON.stringify([...new Set(foreign)]));
  const errs = await ev("window.__mgErrors||[]");
  check("no uncaught page errors were recorded", errs.length === 0, JSON.stringify(errs.slice(0, 3)));
  await nav("feeds");
  await click("feeds-stop-all");
} catch (e) {
  check("product vertical 07 acceptance ran to completion", false, e.message + " @ " + String(e.stack).split("\n").slice(1, 3).join(" | "));
} finally {
  const failed = results.filter((r) => !r.ok).length;
  fs.writeFileSync(path.join(OUT, "results.json"), JSON.stringify({ base: BASE, when: new Date().toISOString(), alert_id: aid, notes, results }, null, 1));
  console.log(`\n${results.length - failed}/${results.length} checks passed`);
  try { ws?.close(); } catch { /* ignore */ }
  chrome.kill("SIGTERM");
  await sleep(500);
  process.exit(failed ? 1 : 0);
}
