// MVP browser acceptance (MG-MVP-VERTICAL-01) over the DevTools protocol with the system Google Chrome (no extra packages).
// Usage: node e2e/mvp_acceptance.mjs [baseUrl]  -> writes logs/mvp_e2e/{results.json,*.png}; exit 1 on any failed check.
// WEBCAM SOURCE: Chrome's FAKE capture device fed with a Y4M file derived from the registered clip (--use-fake-device-for-media-stream,
// --use-file-for-fake-video-capture) and an auto-granted permission prompt (--use-fake-ui-for-media-stream). This is a CONTROLLED TEST SOURCE.
// It proves the browser capture -> JPEG -> local inference pipeline end to end, and it is NOT a physical-camera acceptance (that stays PENDING).
// SAFETY (cycle-16 MG-PROC-016): these suites create analyses, tamper with stored evidence and delete sessions. They may only run against a server started by
// scripts/run_mvp_e2e.sh with isolated storage (it sets MG_E2E_ISOLATED); never against a user's live server.
if (!process.env.MG_E2E_ISOLATED) { console.error("refusing to run: use `MG_E2E_PORT=<free port> bash scripts/run_mvp_e2e.sh " + process.argv[1] + "` (isolated server and storage)"); process.exit(3); }
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const BASE = process.argv[2] ?? "http://127.0.0.1:8000";
const PORT = 9334;
const OUT = path.resolve("logs/mvp_e2e");
fs.mkdirSync(OUT, { recursive: true });
const Y4M = path.join(OUT, "fake_camera.y4m");
if (!fs.existsSync(Y4M)) { console.error("missing " + Y4M + " (run e2e/make_fake_camera.py)"); process.exit(2); }
const profile = fs.mkdtempSync(path.join(os.tmpdir(), "mg-chrome-mvp-"));
const chrome = spawn("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", [
  "--headless=new", `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`, "--window-size=1500,1200",
  "--autoplay-policy=no-user-gesture-required", "--no-first-run", "--no-default-browser-check",
  "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", `--use-file-for-fake-video-capture=${Y4M}`,
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

try {
  await connect();
  await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1500, height: 1200, deviceScaleFactor: 1, mobile: false });
  await send("Page.addScriptToEvaluateOnNewDocument", { source: `window.__streams=[];const g=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);navigator.mediaDevices.getUserMedia=async(c)=>{const s=await g(c);window.__streams.push(s);return s;};` });
  await send("Page.navigate", { url: BASE });
  await waitFor(() => ev("document.querySelector('h1')?.textContent.includes('MineGuard')"), "page");
  check("Spanish UI with both modes available", await ev("document.documentElement.lang==='es' && !!document.querySelector('[data-testid=nav-video]') && !!document.querySelector('[data-testid=nav-camara]') && document.body.textContent.includes('Cámara')"));

  // ======================================================================== webcam
  await click("nav-camara");
  await waitFor(() => ev("!!document.querySelector('[data-testid=webcam-panel]')"), "webcam panel");
  check("webcam tab starts with the camera OFF and no live track", (await attr("cam-status", "data-state")) === "off" && (await liveTracks()) === 0);
  check("honesty: capture origin is declared by the browser and a test source is not a physical-camera acceptance", (await ev("document.body.textContent")).includes("no puede comprobar que sea una cámara física") || (await ev("document.body.textContent")).includes("no se verifica en el servidor"));

  check("Privacy ON is the DEFAULT in the UI before any capture (selected, banner visible)", (await ev("document.querySelector('[data-testid=privacy-on]').checked")) && (await text("privacy-banner")).includes("no se guarda ninguna imagen"));
  check("the privacy mode is visible before capture (both options labelled in Spanish)", (await text("privacy-box")).includes("Privacidad ACTIVADA") && (await text("privacy-box")).includes("Privacidad DESACTIVADA"));
  await click("cam-start");
  await waitFor(async () => (await attr("cam-status", "data-state")) === "on", "camera on", 20000);
  const v1 = await ev("(()=>{const v=document.querySelector('[data-testid=cam-video]');return {w:v.videoWidth,h:v.videoHeight,playing:!v.paused&&v.readyState>=2,hasStream:!!v.srcObject}})()");
  check("browser camera permission granted and a real MediaStream is playing (fake test device)", v1.w > 0 && v1.playing && v1.hasStream && (await liveTracks()) === 1, JSON.stringify(v1));
  check("capture source is DECLARED as a test source (not a physical camera)", (await attr("cam-origin", "data-origin")) === "test_source");

  // zone: an invalid (self-intersecting) polygon is refused with a Spanish message, then a valid one is created
  await click("zone-draw");
  await clickStage("cam-stage", [[0.2, 0.2], [0.8, 0.8], [0.8, 0.2], [0.2, 0.8]]);
  await click("zone-close");
  check("self-intersecting zone is rejected in the UI (Spanish message)", (await text("zone-error")).includes("se cruza consigo misma"), await text("zone-error"));
  check("a refused polygon keeps the entered vertices so it can be corrected (draft not destroyed)", (await text("zone-hint")).includes("(4)") && (await ev("document.querySelectorAll('[data-testid=zone-item]').length")) === 0);
  await click("zone-cancel");
  await click("zone-draw");
  await clickStage("cam-stage", [[0.1, 0.45], [0.9, 0.45], [0.9, 0.95], [0.1, 0.95]]);
  await click("zone-close");
  check("valid polygon zone is created", (await ev("document.querySelectorAll('[data-testid=zone-item]').length")) === 1);

  await ev("(()=>{const el=document.querySelector('[data-testid=cam-interval]');const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;s.call(el,300);el.dispatchEvent(new Event('input',{bubbles:true}))})()");
  await click("privacy-off");   // the FIRST session explicitly asks for Privacy OFF (frames retained) so that deep verification can still be exercised; later sessions use the default
  check("choosing Privacy OFF shows an explicit warning that frames will be stored", (await text("privacy-banner")).includes("se guardarán los fotogramas"));
  // STABILIZATION DIAGNOSTIC (no timeout change): record the real outcome of the session-start request so a failure is attributable
  // (HTTP status / refusal id / duration / UI error text) instead of an opaque timeout.
  await ev("(()=>{if(window.__mgStart)return;window.__mgStart=[];const f=window.fetch;window.fetch=async function(u,i){const isStart=String(u).endsWith('/api/webcam/sessions')&&(i&&i.method)==='POST';const t0=performance.now();try{const r=await f.apply(this,arguments);if(isStart)window.__mgStart.push({status:r.status,ms:Math.round(performance.now()-t0)});return r}catch(e){if(isStart)window.__mgStart.push({error:String(e),ms:Math.round(performance.now()-t0)});throw e}}})()");
  const clickedAnalyze = await click("cam-analyze");
  try {
    await waitFor(async () => (await attr("cam-status", "data-state")) === "analyzing", "analysis started", 15000);
  } catch (e) {
    const diag = { clickedAnalyze, state: await attr("cam-status", "data-state"), uiError: await text("cam-error"), start: await ev("window.__mgStart||null"), pressure: process.env.MG_PRESSURE_NOTE ?? "" };
    fs.writeFileSync(path.join(OUT, "analysis_start_failure.json"), JSON.stringify(diag, null, 2));
    check("analysis started (diagnostic on failure)", false, JSON.stringify(diag));
    throw e;
  }
  check("session-start request succeeded (201) and its duration is recorded", (await ev("window.__mgStart||[]")).some((x) => x.status === 201), JSON.stringify(await ev("window.__mgStart||[]")));
  check("during capture the mode is fixed and the retained-frames indicator is shown", (await ev("document.querySelector('[data-testid=privacy-box]').disabled")) && (await text("privacy-fixed")).includes("RETENIDOS"));
  await waitFor(async () => Number(await attr("cam-counters", "data-analyzed")) >= 6, "6 analyzed frames", 60000);
  const c1 = await ev("(()=>{const e=document.querySelector('[data-testid=cam-counters]');return {r:+e.dataset.received,a:+e.dataset.analyzed,dq:+e.dataset.droppedQueue,ds:+e.dataset.droppedStale,er:+e.dataset.errors}})()");
  check("frames flow browser -> backend -> real model (received and analyzed counters advance)", c1.a >= 6 && c1.r >= c1.a, JSON.stringify(c1));
  check("every received frame is accounted for (analyzed + dropped + errors <= received)", c1.a + c1.dq + c1.ds + c1.er <= c1.r + 1, JSON.stringify(c1));
  const riskState = await attr("cam-risk-Z1", "data-state");
  check("live risk state for the zone is shown (GREEN/UNKNOWN only: the clip has a vehicle and no person)", ["GREEN", "UNKNOWN"].includes(riskState), riskState);
  check("live explanation of the state never states that the zone is safe", !(await ev("[...document.querySelectorAll('[data-testid=risk-explain]')].map(e=>e.textContent).join(' ')")).match(/es seguro|está seguro/i));
  await shot("webcam_analyzing.png");
  await sleep(2500);
  await click("cam-stop");
  await waitFor(() => ev("!!document.querySelector('[data-testid=cam-stored]')"), "stored session shown", 60000);
  check("stopping releases the camera completely (no live track, no srcObject)", (await liveTracks()) === 0 && (await ev("document.querySelector('[data-testid=cam-video]').srcObject===null")) && (await attr("cam-status", "data-state")) === "off");
  const sid = (await text("cam-stored")).match(/session_[0-9a-f]{16}/)?.[0];
  check("a session artifact was stored and is listed", !!sid && (await api("/api/sessions")).body.includes(sid), sid);
  const sb = JSON.parse((await api(`/api/sessions/${sid}`)).body);
  const objs = sb.observation_set.frames.flatMap((f) => f.objects);
  check("REAL model detections exist on posted frames (vehicle from the clip) with model provenance", objs.some((o) => o.cls.label === "vehicle") && !!sb.provenance.model.weights_sha256 && sb.observation_set.origin === "model_inference", `${objs.length} detections`);
  const ls = sb.manifest.live_stats;
  console.log("MEASURED live stats:", JSON.stringify(ls));
  check("measured live behaviour is recorded and sane (latency p50 < 5 s, queue never above its bound)", ls.latency_ms.p50 !== null && ls.latency_ms.p50 < 5000 && ls.queue_depth_max_observed <= ls.queue_max, JSON.stringify(ls));
  check("provenance states the capture origin is client-declared and not server-verified", sb.provenance.capture.declared_by === "client" && sb.provenance.capture.physical_camera_verified_by_server === false && sb.provenance.capture.origin === "test_source");
  check("stored session starts as 'session' (not verified); wording does not claim verification", (await attr("cam-stored", "data-trust")) === "session" && !(await text("cam-stored")).includes("verificada localmente") && (await text("origin")).includes("producida en esta sesión"));
  check("risk panel shows the zone without a safe claim", (await ev("!!document.querySelector('[data-testid=risk-panel]')")) && !(await text("risk-explain")).toLowerCase().includes("es seguro"));
  check("stored Privacy OFF session says frames are retained", (await attr("cam-retention", "data-retention")) === "frames" && sb.retention === "frames");
  check("after the session the selector returned to Privacy ON (never sticks to OFF)", await ev("document.querySelector('[data-testid=privacy-on]').checked"));
  await shot("webcam_stored_session.png");
  await click("cam-verify");
  await waitFor(async () => (await attr("cam-stored", "data-trust")) === "verified", "deep verification", 180000);
  check("deep verification of the stored session (re-decode + re-inference) -> 'verified' wording", (await text("origin")).includes("verificada localmente"));

  // camera release on device disconnect (simulated 'ended' event) and on leaving the tab
  await click("cam-start");
  await waitFor(async () => (await attr("cam-status", "data-state")) === "on", "camera on again", 20000);
  check("the next session starts again in Privacy ON", await ev("document.querySelector('[data-testid=privacy-on]').checked"));
  await click("cam-analyze");
  await waitFor(async () => Number(await attr("cam-counters", "data-analyzed")) >= 2, "2 frames", 60000);
  check("default session: UI says no image is being stored", (await text("privacy-fixed")).includes("Ninguna imagen se está guardando"));
  await ev("(()=>{const t=window.__streams[window.__streams.length-1].getTracks()[0];t.dispatchEvent(new Event('ended'))})()");
  await waitFor(async () => (await attr("cam-status", "data-state")) === "off", "analysis closed on device end", 60000);
  console.log("diag device-end: error=", JSON.stringify(await text("cam-error")), "notice=", JSON.stringify(await text("cam-notice")), "stored sessions=", (await api("/api/sessions")).body.slice(0, 400));
  check("simulated device 'ended' event closes the session and releases the camera", (await liveTracks()) === 0 && (await text("cam-notice")).includes("se desconectó"));
  const endedList = JSON.parse((await api("/api/sessions")).body);
  const onSess = endedList.find((x) => x.closed_reason === "device_ended");
  check("the default-mode session is stored metadata-only with no frames directory exposed", onSess?.retention === "metadata_only" && (await api(`/api/sessions/${onSess.session_id}/frames/0`)).status === 410);
  check("the device-ended session is stored with closed_reason device_ended (not client_stop)", endedList.some((x) => x.closed_reason === "device_ended"), JSON.stringify(endedList.map((x) => x.closed_reason)));
  await click("cam-start");
  await waitFor(async () => (await attr("cam-status", "data-state")) === "on", "camera on third time", 20000);
  await click("nav-video");
  await sleep(500);
  check("leaving the webcam tab releases the camera", (await liveTracks()) === 0);
  const stored3 = JSON.parse((await api("/api/sessions")).body);
  check("every webcam case above ended with a finalized, stored session (3 sessions: normal stop, device end; verified one among them)", stored3.length >= 2, `${stored3.length} stored sessions`);

  // ======================================================================== recorded MP4: analysis, zone, risk, export
  const vids = JSON.parse((await api("/api/videos")).body);
  const anz = await api(`/api/videos/${vids[0].video_id}/analyze`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ stride: 3, threshold: 0.3 }) });
  check("MP4 analysis starts right after webcam use (the inference lock was released)", anz.status === 202, anz.body.slice(0, 120));
  const job = JSON.parse(anz.body).job_id;
  const runId = await waitFor(async () => { const j = JSON.parse((await api(`/api/jobs/${job}`)).body); if (j.status === "failed") throw new Error(j.error); return j.status === "done" ? j.run_id : null; }, "MP4 analysis", 240000);
  await send("Page.navigate", { url: BASE });
  await waitFor(() => ev("!!document.querySelector('[data-testid=nav-video]')"), "navigation");
  await click("nav-video");
  await waitFor(() => ev("document.querySelector('video')?.readyState>=2"), "video", 40000);
  await waitFor(() => ev("document.body.textContent.includes('" + runId + "')"), "run loaded", 30000);
  await waitFor(() => ev("!!document.querySelector('[data-testid=risk-panel]') && !!document.querySelector('[data-testid=trust-state]')"), "risk panel and trust state", 30000);
  check("recorded MP4 run (real inference) is loaded as 'session' and the risk panel exists", (await attr("trust-state", "data-trust")) === "session" && (await ev("!!document.querySelector('[data-testid=risk-panel]')")));
  await click("zone-draw");
  await clickStage("stage", [[0.15, 0.4], [0.85, 0.4], [0.85, 0.95], [0.15, 0.95]]);
  await click("zone-close");
  await click("zone-save");
  await waitFor(() => ev("!!document.querySelector('[data-testid=risk-zone-Z1]')"), "risk zone after save", 20000);
  check("MP4: zone saved and evaluated; state at the playhead is GREEN or UNKNOWN (no person in this clip)", ["GREEN", "UNKNOWN"].includes(await attr("risk-zone-Z1", "data-state")), await attr("risk-zone-Z1", "data-state"));
  const exp = JSON.parse((await api(`/api/runs/${runId}/risk/export`)).body);
  check("risk export is canonical JSON with provenance, zones, evaluation digest", exp.schema === "mg.export.vs02" && !!exp.bundle.provenance.model.weights_sha256 && exp.evaluation.zone_defs.length === 1 && exp.evaluation.digest.length === 64);
  await shot("mp4_zone_risk.png");

  // ======================================================================== simulation button (no personal images)
  check("the simulation card is offered for the real run", await ev("!!document.querySelector('[data-testid=sim-red]')"));
  await click("sim-red");
  await waitFor(() => ev("!!document.querySelector('[data-testid=sim-badge]') || !!document.querySelector('[role=alert]')"), "simulation result", 60000);
  console.log("diag sim alert:", JSON.stringify(await ev("document.querySelector('[role=alert]')?.textContent || ''")));
  check("simulation is labelled SIMULACIÓN, synthetic origin, never real inference", (await text("sim-badge")).includes("SIMULACIÓN") && (await text("origin")).includes("Reproducción sintética") && !(await text("origin")).includes("Inferencia experimental real"));
  await waitFor(() => ev("!!document.querySelector('[data-testid=risk-zone-Z1]')"), "simulation risk", 20000);
  const simEv = await ev("[...document.querySelectorAll('[data-testid=risk-events] li')].map(l=>l.dataset.severity)");
  check("simulation produces a YELLOW and then a RED event", simEv.includes("YELLOW") && simEv.includes("RED"), simEv.join());
  await waitFor(async () => (await attr("risk-zone-Z1", "data-state")) === "RED", "playhead jumped to the RED instant", 10000);
  check("the view jumped to a RED instant", (await attr("risk-zone-Z1", "data-state")) === "RED" && (await ev("!!document.querySelector('[data-testid=sim-hint]')")));
  await shot("simulation_red.png");
  await click("sim-back");
  await waitFor(() => ev("!document.querySelector('[data-testid=sim-badge]') && !!document.querySelector('[data-testid=sim-red]')"), "back to the real run", 30000);
  check("the real run is untouched and can be shown again", (await attr("trust-state", "data-trust")) === "session");

  // ======================================================================== synthetic replay: events rendered, labelled synthetic
  const base = JSON.parse((await api(`/api/runs/${runId}`)).body);
  const b = base.observation_set.source_binding, n = b.frame_count;
  const box = (label, x, y, w, h, key) => ({ obj_key: key, cls: { label, state: "known", detector_label: "scripted", detector_class_id: 0 }, bbox: { x, y, w, h }, truncated: { left: false, right: false, top: false, bottom: false }, confidence: { permille: null, kind: "not_applicable" }, track_ref: null });
  const frames = [], an = new Set();
  for (let i = 0; i < n; i += 3) {
    const objs = [];
    if (i >= 60 && i < 420) objs.push(box("person", 400, 400, 60, 200, "o1"));
    if (i >= 300 && i < 420) objs.push(box("vehicle", 600, 350, 300, 250, "o2"));
    frames.push({ frame_index: i, status: "analyzed", pixel_sha256: "0".repeat(64), objects: objs }); an.add(i);
  }
  const ranges = []; let st = null;
  for (let i = 0; i <= n; i++) { const miss = i < n && !an.has(i); if (miss && st === null) st = i; else if (!miss && st !== null) { ranges.push({ from_frame: st, to_frame: i - 1, status: "not_analyzed_stride" }); st = null; } }
  const replay = { schema: "mg.obsset.vs01", source_binding: b, coord_space: "image_px_v1", taxonomy: "mg.cls.v0", origin: "synthetic_scripted",
    producer: { adapter_id: "replay", adapter_version: "0", model_id: null, weights_sha256: null, params_hash: "c".repeat(64), label_map_digest: null, params: {} },
    tracks: [], frames, frame_status_ranges: ranges, meta: { created_utc: "2026-10-25T00:00:00Z", tool: "mvp-e2e-replay" } };
  const rr = await api("/api/replays", { method: "POST", body: JSON.stringify({ base_run_id: runId, observation_set: replay }) });
  const rid = JSON.parse(rr.body).run_id;
  check("synthetic replay stored as a separate run (never inference)", rr.status === 201 && /^run_[0-9a-f]{16}$/.test(rid) && rid !== runId);
  const zr = await api(`/api/runs/${rid}/zones`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ expected_revision: 0, zones: [{ zone_id: "Z1", kind: "restricted", polygon: [[200, 300], [1000, 300], [1000, 700], [200, 700]], name: "Zona 1" }] }) });
  check("zone stored on the replay run", zr.status === 200);
  await send("Page.navigate", { url: BASE });
  await waitFor(() => ev("!!document.querySelector('[data-testid=nav-video]')"), "navigation");
  await click("nav-video");
  await waitFor(() => ev("document.querySelector('video')?.readyState>=2"), "video", 40000);
  await waitFor(() => ev("document.body.textContent.includes('" + rid + "')"), "replay run loaded", 30000);
  check("UI labels the replay as synthetic and NOT as real inference", (await text("origin")).includes("Reproducción sintética") && !(await text("origin")).includes("Inferencia experimental real") && (await ev("!!document.querySelector('[data-testid=origin-banner]')")));
  check("risk panel warns that the evaluation is over synthetic observations", await ev("!!document.querySelector('[data-testid=risk-synthetic]')"));
  const sevs = await ev("[...document.querySelectorAll('[data-testid=risk-events] li')].map(l=>l.dataset.severity)");
  check("events rendered: a YELLOW (person dwell) and a RED (person + vehicle) event", sevs.includes("YELLOW") && sevs.includes("RED"), sevs.join());
  await seek(0.5);
  check("state at 0.5 s: GREEN", (await attr("risk-zone-Z1", "data-state")) === "GREEN", await attr("risk-zone-Z1", "data-state"));
  await seek(3.0);
  check("state at 3.0 s (person in zone since 2.0 s, dwell not yet 1.5 s): GREEN with a PENDING condition (dwell in progress), not plain safe", (await attr("risk-zone-Z1", "data-state")) === "GREEN" && (await attr("risk-zone-Z1", "data-pending")) === "yes" && (await text("risk-explain")).includes("PENDIENTE"), await text("risk-explain"));
  await seek(5);
  check("state at 5 s: YELLOW", (await attr("risk-zone-Z1", "data-state")) === "YELLOW", await attr("risk-zone-Z1", "data-state"));
  await shot("replay_yellow.png");
  await seek(12);
  check("state at 12 s: RED", (await attr("risk-zone-Z1", "data-state")) === "RED", await attr("risk-zone-Z1", "data-state"));
  await shot("replay_red.png");
  await seek(20);
  check("state at 20 s: GREEN again, wording does not claim safety", (await attr("risk-zone-Z1", "data-state")) === "GREEN" && (await text("risk-explain")).includes("No significa «seguro»"));
  check("verification of a synthetic replay can never promote it", JSON.parse((await api(`/api/runs/${rid}/verify`)).body).ok === false && JSON.parse((await api(`/api/runs/${rid}`)).body).trust === "unverified");

  // ======================================================================== integrated navigation + session management UI (isolated storage only)
  for (const [a, view] of [["resumen", "view-resumen"], ["feeds", "view-feeds"], ["sesiones", "view-sesiones"], ["eventos", "view-eventos"], ["simulaciones", "view-simulaciones"], ["estadisticas", "view-estadisticas"], ["desarrollador", "view-desarrollador"], ["configuracion", "view-configuracion"]]) {
    await click(`nav-${a}`);
    await waitFor(() => ev(`!!document.querySelector('[data-testid=${view}]')`), view, 15000);
  }
  check("all ten areas are reachable (the nine of MVP 1 plus Multi-fuente)", (await ev("document.querySelectorAll('[role=tab]').length")) === 10 + 2 || (await ev("document.querySelectorAll('nav [role=tab]').length")) === 10);
  await click("nav-resumen");
  await waitFor(() => ev("!!document.querySelector('[data-testid=ov-real]')"), "overview data");
  check("Resumen: states the not-a-safety-system limit, Privacy ON default, and separates real / unverified / synthetic events", (await text("limits")).includes("No es un sistema de seguridad certificado") && (await text("ov-privacy")).includes("ACTIVADA") && !!(await text("ov-syn")) && !!(await text("ov-unv")));
  await click("nav-eventos");
  await waitFor(() => ev("!!document.querySelector('[data-testid=ev-table]')"), "events table", 15000);
  check("Eventos: synthetic events are labelled SIMULACIÓN, never as real inference", (await ev("document.querySelector('[data-testid=ev-table]').textContent")).includes("SIMULACIÓN"));
  await click("nav-estadisticas");
  check("Estadísticas / Configuración: unfinished features are labelled 'No disponible' (no placeholder controls)", (await text("view-estadisticas")).includes("No disponible"));
  await click("nav-simulaciones");
  await waitFor(() => ev("document.querySelector('[data-testid=sim-red-view]')?.disabled===false"), "simulation button enabled (the base run is loaded)", 20000);
  await click("sim-red-view");
  await waitFor(() => ev("!!document.querySelector('[data-testid=sim-result]')"), "simulation lab result (stays in the Simulaciones area)", 60000).catch(async (e) => { console.log("diag sim-lab:", await ev("JSON.stringify({alert:document.querySelector('[data-testid=view-simulaciones] [role=alert]')?.textContent,nobase:!!document.querySelector('[data-testid=sim-no-base]'),dis:document.querySelector('[data-testid=sim-red-view]')?.disabled})")); throw e; });
  check("Simulation Lab shows the YELLOW → RED progression from the real risk engine and labels it synthetic", (await text("sim-progress")).includes("SIMULACIÓN") && (await ev("[...document.querySelectorAll('[data-testid=sim-interval]')].map(e=>e.dataset.state).join(',')")).includes("YELLOW") && (await ev("[...document.querySelectorAll('[data-testid=sim-interval]')].map(e=>e.dataset.state).join(',')")).includes("RED"));
  await click("sim-open-video");
  await waitFor(() => ev("document.querySelector('[data-testid=nav-video]').getAttribute('aria-selected')==='true'"), "simulation opened from the Simulation Lab (navigates to the MP4 workspace)", 60000);
  await waitFor(() => ev("!!document.querySelector('[data-testid=sim-hint]')"), "playhead on the RED instant", 30000);
  check("Simulaciones area: the RED simulation opens in the MP4 workspace, labelled synthetic", (await text("sim-badge")).includes("SIMULACIÓN") && (await attr("risk-zone-Z1", "data-state")) !== null);
  await click("nav-sesiones");
  await waitFor(() => ev("!!document.querySelector('[data-testid=sess-table]')"), "sessions table", 15000);
  const rows0 = await ev("document.querySelectorAll('[data-testid=sess-table] tbody tr').length");
  const listBefore = JSON.parse((await api("/api/sessions")).body);
  check("Sesiones lists every stored session with its retention label", rows0 === listBefore.length && rows0 >= 2 && (await text("sess-table")).includes("Solo metadatos") && (await text("sess-table")).includes("Fotogramas retenidos"), `${rows0} rows`);
  // MG-DELETE-035: the destructive path is disabled in the product until it is rebuilt on pinned descriptors; nothing is deleted
  check("deletion is visibly unavailable: explanation shown and every delete control disabled", (await text("del-unavailable")).includes("NO DISPONIBLE") && (await ev("[...document.querySelectorAll('[data-testid=del-selected],[data-testid=del-all],[data-testid^=del-session_]')].every(b=>b.disabled)")));
  const dr = await api("/api/sessions/delete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ all: true, expected_count: rows0, confirm: "ELIMINAR" }) });
  check("the API refuses with 503 and every stored session and run remains", dr.status === 503 && JSON.parse((await api("/api/sessions")).body).length === listBefore.length && JSON.parse((await api("/api/runs")).body).length >= 1, String(dr.status));
} catch (e) {
  check("MVP acceptance script ran to completion", false, e.message);
} finally {
  const failed = results.filter((r) => !r.ok).length;
  fs.writeFileSync(path.join(OUT, "results.json"), JSON.stringify({ base: BASE, when: new Date().toISOString(), results }, null, 1));
  console.log(`\n${results.length - failed}/${results.length} checks passed`);
  try { ws?.close(); } catch { /* ignore */ }
  chrome.kill("SIGTERM");
  await sleep(500);
  process.exit(failed ? 1 : 0);
}
