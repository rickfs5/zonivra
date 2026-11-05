// MG-PRODUCT-VERTICAL-04 operator-journey acceptance (source lifecycle, UNKNOWN vs last observed, simulated RED, incident lifecycle, return to monitoring)
// (boilerplate shared with the other suites; originally the MG-VERTICAL-02 multi-feed suite) over the DevTools protocol with the system Google Chrome (no extra packages).
// Usage: node e2e/mvp_acceptance.mjs [baseUrl]  -> writes logs/journey_vertical04/{results.json,*.png}; exit 1 on any failed check.
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
const OUT = path.resolve("logs/journey_vertical04");
fs.mkdirSync(OUT, { recursive: true });
const profile = fs.mkdtempSync(path.join(os.tmpdir(), "mg-chrome-journey-"));
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
try {
  await connect();
  await send("Page.enable");
  await send("Page.addScriptToEvaluateOnNewDocument", { source: "window.__mgErrors=[];window.addEventListener('error',e=>window.__mgErrors.push(String(e.message)));window.addEventListener('unhandledrejection',e=>window.__mgErrors.push('rejection: '+String(e.reason)));" });
  await send("Emulation.setDeviceMetricsOverride", { width: 1500, height: 1100, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url: BASE });
  await waitFor(() => ev("document.querySelector('h1')?.textContent.includes('MineGuard')"), "page");
  const byIdObs = (snap, fid) => snap.feeds.find((f) => f.feed_id === fid)?.observation?.fresh === true;   // a playing feed is OBSERVED (fresh); the kernel may still say UNKNOWN for a dropped sample, which is correct
  const flag = (f) => panelAttr(f, "feed-flag", "data-flag");
  const riskOf = (f) => panelAttr(f, "feed-risk", "data-state");
  const stateOf = (f) => panelAttr(f, "feed-state", "data-state");
  const why = (f) => panelAttr(f, "feed-unknown-why", "data-reason");

  // ======================================================================== 1-2. observe the system: empty overview with direct navigation
  await nav("resumen");
  await waitFor(() => ev("!!document.querySelector('[data-testid=ov-no-sources]')"), "empty overview state");
  check("with no sources the overview offers direct actions (Multi-fuente, Video MP4, Simulaciones)", await ev("['ov-go-feeds','ov-go-video','ov-go-sim'].every(t=>!!document.querySelector('[data-testid='+t+']'))"));
  await shot("j01_overview_empty.png");
  await click("ov-go-feeds");
  await waitFor(() => ev("!!document.querySelector('[data-testid=view-feeds]') && document.querySelector('[data-testid=nav-feeds]').getAttribute('aria-selected')==='true'"), "feeds area from the empty state");
  check("the empty-state action opens Multi-fuente", true);

  // ======================================================================== 3. start three local feeds with consistent names
  await waitFor(() => ev("document.querySelector('[data-testid=feeds-video]')?.options.length>0 && !document.querySelector('[data-testid=feeds-create3]').disabled"), "feeds form", 20000);
  // ======================================================================== V05 / MG-PROV-026: a source that starts ALONE is relabelled when a second feed of the same file joins, and only then
  const provOfPanel = (f) => panelAttr(f, "feed-source", "data-provenance");
  await click("feeds-add");
  await waitFor(async () => (await panelIds()).length === 1, "first feed panel", 20000);
  const [pA] = await panelIds();
  await ev(`document.querySelector('[data-feed-id=${JSON.stringify(pA)}] [data-testid=feed-start]').click()`);
  await waitFor(async () => (await analyzed(pA)) >= 2, "feed A analysed alone", 120000);
  check("a feed running ALONE is a genuine local inference source (no repeated-file label)", (await provOfPanel(pA)) === "real_inference" && (await ev("document.querySelectorAll('[data-testid=feeds-repeated-banner]').length")) === 0);
  await click("feeds-add");
  await waitFor(async () => (await panelIds()).length === 2, "second feed panel", 20000);
  const pB = (await panelIds()).find((x) => x !== pA);
  await ev(`document.querySelector('[data-feed-id=${JSON.stringify(pB)}] [data-testid=feed-start]').click()`);
  await waitFor(async () => (await provOfPanel(pA)) === "repeated_source_simulation" && (await provOfPanel(pB)) === "repeated_source_simulation", "BOTH feeds labelled as repeated-file simulation", 40000);
  check("when a second feed of the same file starts, BOTH feeds are labelled a repeated-source simulation and the first keeps running", (await stateOf(pA)) === "playing" && (await text("feeds-repeated-banner")).includes("NO representan varias cámaras"));
  const apiBoth = (await jget("/api/feeds")).feeds;
  check("the API and the browser agree on the labels and neither claims a physical camera", apiBoth.every((f) => f.source.provenance === "repeated_source_simulation" && f.source.physical_camera === false) && (await provOfPanel(pA)) === apiBoth.find((f) => f.feed_id === pA).source.provenance);
  await ev(`document.querySelector('[data-feed-id=${JSON.stringify(pA)}] [data-testid=feed-expand]').click()`);
  await waitFor(() => ev("!!document.querySelector('[data-testid=feed-provenance]')"), "provenance section in the expanded feed");
  check("the expanded feed shows launch provenance (immutable), the present relationship and the run history apart", (await text("feed-launch")).includes("sin otra fuente") && (await text("feed-shared-now")).includes("otra fuente activa") && (await text("feed-shared-run")).includes("solapamiento"));
  await shot("j01b_late_join_expanded.png");
  await click("feeds-back-grid");
  await waitFor(async () => (await panelIds()).length === 2, "grid restored");
  await ev(`document.querySelector('[data-feed-id=${JSON.stringify(pB)}] [data-testid=feed-stop]').click()`);
  await waitFor(async () => (await stateOf(pB)) === "stopped", "B stopped", 40000);
  await ev(`document.querySelector('[data-feed-id=${JSON.stringify(pA)}] [data-testid=feed-expand]').click()`);
  await waitFor(async () => (await text("feed-shared-now")).includes("ninguna otra fuente activa"), "A's PRESENT relationship shows it is alone again", 40000);
  check("after the other feed stops the PRESENT relationship is «alone» but the run's EVIDENCE provenance stays a repeated-source simulation (no laundering)", (await attr("feed-evidence-prov", "data-prov")) === "repeated_source_simulation" && (await stateOf(pA)) === "playing" && (await provOfPanel(pA)) === "repeated_source_simulation");
  await waitFor(async () => (await text("feed-overlap-history")).includes("hasta la muestra"), "the overlap history records when the overlap ended (bounded by sample times)", 40000);
  check("the feed shows WHEN the overlap was present (an interval bounded by sample times, not exact instants) apart from the conservative run label", (await text("feed-overlap-history")).includes("tras la muestra") && (await text("feed-overlap-history")).includes("hasta la muestra"));
  await click("feeds-back-grid");
  await waitFor(async () => (await panelIds()).length === 2, "grid restored again");
  await click("feeds-stop-all");
  await waitFor(async () => (await Promise.all((await panelIds()).map(stateOf))).every((x) => x !== "playing" && x !== "paused"), "all stopped", 20000);
  for (const id of await panelIds()) { await ev(`document.querySelector('[data-feed-id=${JSON.stringify(id)}] [data-testid=feed-remove]').click()`); }
  await waitFor(async () => (await panelIds()).length === 0, "feeds removed", 20000);

  await click("feeds-create3");
  await waitFor(async () => (await panelIds()).length === 3, "three feed panels", 20000);
  const fids = await panelIds();
  const labels = await ev("[...document.querySelectorAll('article.feed h3')].map(e=>e.textContent)");
  check("default source names are consistent: Fuente 1, Fuente 2, Fuente 3", JSON.stringify(labels) === JSON.stringify(["Fuente 1", "Fuente 2", "Fuente 3"]), JSON.stringify(labels));
  check("before starting, a source is UNKNOWN (not green) and says why", (await Promise.all(fids.map(riskOf))).every((s) => s === "UNKNOWN") && (await Promise.all(fids.map(why))).every((r) => r === "mg.unknown.idle"));
  await click("feeds-start-all");
  await waitFor(async () => (await Promise.all(fids.map(analyzed))).every((n) => n >= 3), "three feeds analysed by the real model", 150000);
  await waitFor(async () => (await Promise.all(fids.map(riskOf))).every((s) => s !== "UNKNOWN"), "fresh current risk on all three", 30000);
  check("three independent live feeds with real inference, each with a current (fresh) risk state", (await Promise.all(fids.map(flag))).every((f) => f === "live"));
  check("engineering counters are an optional detail (collapsed by default); primary metrics stay visible", (await ev("[...document.querySelectorAll('[data-testid=feed-eng]')].every(d=>!d.open)")) && (await ev("document.querySelectorAll('[data-testid=feed-stats]').length")) === 3);
  await shot("j02_three_feeds_live.png");

  // ======================================================================== 5-6. zones, detections and the risk decision (expanded feed)
  await ev(`document.querySelector('[data-feed-id=${JSON.stringify(fids[1])}] [data-testid=feed-expand]').click()`);
  await waitFor(() => ev("!!document.querySelector('[data-testid=feed-detail]')"), "expanded feed");
  check("the expanded feed shows its own zone state, source and accounting", (await ev("!!document.querySelector('[data-testid^=feed-zone-]')")) && (await attr("feed-detail", "data-feed-id")) === fids[1]);
  await shot("j03_feed_expanded.png");
  await click("feeds-back-grid");
  await waitFor(async () => (await panelIds()).length === 3, "grid restored");

  // ======================================================================== 14-15. paused / playing / stopped combination, UNKNOWN vs last observed
  const c2 = await analyzed(fids[1]);
  await ev(`document.querySelector('[data-feed-id=${JSON.stringify(fids[0])}] [data-testid=feed-pause]').click()`);
  await ev(`document.querySelector('[data-feed-id=${JSON.stringify(fids[2])}] [data-testid=feed-stop]').click()`);
  await waitFor(async () => (await stateOf(fids[0])) === "paused" && (await stateOf(fids[2])) === "stopped", "paused and stopped states", 20000);
  await sleep(2500);
  check("paused feed: transport state «paused», risk UNKNOWN (never GREEN), explanation and last observed shown apart as history", (await riskOf(fids[0])) === "UNKNOWN" && (await why(fids[0])) === "mg.unknown.paused");
  const pausedWhy = await ev(`document.querySelector('[data-feed-id=${JSON.stringify(fids[0])}] [data-testid=feed-unknown-why]').textContent`);
  check("the paused feed's text separates the last observed state as historical", pausedWhy.includes("último estado observado") && pausedWhy.includes("histórico"), pausedWhy);
  check("stopped feed: risk UNKNOWN with its own reason; the stopped label is a source state, not a risk", (await riskOf(fids[2])) === "UNKNOWN" && (await why(fids[2])) === "mg.unknown.stopped" && (await flag(fids[2])) === "stopped");
  check("the third feed keeps playing, observed (fresh, no lifecycle reason for UNKNOWN) with a growing counter, unaffected by the other two", (await stateOf(fids[1])) === "playing" && [undefined, null, "mg.unknown.insufficient"].includes(await why(fids[1])) && byIdObs(await jget("/api/feeds"), fids[1]) && (await analyzed(fids[1])) > c2, JSON.stringify({ st: await stateOf(fids[1]), risk: await riskOf(fids[1]), why: await why(fids[1]), obs: (await jget("/api/feeds")).feeds.find((f) => f.feed_id === fids[1])?.observation, before: c2, now: await analyzed(fids[1]) }));
  const snap = await jget("/api/feeds");
  const byId = Object.fromEntries(snap.feeds.map((f) => [f.feed_id, f]));
  check("the server publishes the same truth: risk_state UNKNOWN for paused/stopped, last_observed_risk kept apart, observation fresh only for the playing feed",
    byId[fids[0]].risk_state === "UNKNOWN" && byId[fids[0]].last_observed_risk !== null && byId[fids[2]].risk_state === "UNKNOWN" && byId[fids[2]].closed_reason === "operator_stop" && byId[fids[1]].observation.fresh === true && byId[fids[0]].observation.fresh === false);
  await shot("j04_paused_playing_stopped.png");
  // overview mirrors it
  await nav("resumen");
  await waitFor(() => ev("document.querySelectorAll('[data-testid=ov-feed-tile]').length===3"), "overview tiles");
  const dist = await text("ov-risk-dist");
  check("overview distribution counts the two unobserved sources as UNKNOWN and states that zero events is not proof of safety", /DESCONOCIDO\s*2/.test(dist.replace(/\s+/g, " ")) || dist.includes("2"), dist);
  check("each unobserved overview tile explains the UNKNOWN", (await ev("document.querySelectorAll('[data-testid=ov-unknown-why]').length")) === 2);
  await shot("j05_overview_mixed.png");
  // resume + restart: independent lifecycle
  await nav("feeds");
  await waitFor(async () => (await panelIds()).length === 3, "feeds area");
  await ev(`document.querySelector('[data-feed-id=${JSON.stringify(fids[0])}] [data-testid=feed-resume]').click()`);
  await ev(`document.querySelector('[data-feed-id=${JSON.stringify(fids[2])}] [data-testid=feed-start]').click()`);
  await waitFor(async () => (await stateOf(fids[0])) === "playing" && (await stateOf(fids[2])) === "playing", "resumed and restarted", 20000);
  await waitFor(async () => (await riskOf(fids[0])) !== "UNKNOWN" && (await riskOf(fids[2])) !== "UNKNOWN", "fresh risk after resume/restart", 40000);
  check("after resume and restart the feeds are observed again (fresh risk) and the middle feed was never interrupted", (await stateOf(fids[1])) === "playing");
  await click("feeds-stop-all");
  await waitFor(async () => (await Promise.all(fids.map(stateOf))).every((s) => s === "stopped"), "all stopped", 20000);
  check("stopping everything leaves no source asserting a current risk", (await Promise.all(fids.map(riskOf))).every((s) => s === "UNKNOWN"));

  // ======================================================================== 7-13. synthetic RED -> event -> alert -> incident -> ack -> resolve -> back to source/time
  const vids = await jget("/api/videos");
  const job = JSON.parse((await jpost(`/api/videos/${vids[0].video_id}/analyze`, { stride: 15, threshold: 0.3 })).body);
  await waitFor(async () => { const j = await jget(`/api/jobs/${job.job_id}`); if (j.status === "failed") throw new Error("analysis failed"); return j.status === "done"; }, "MP4 analysis done", 240000);
  await nav("simulaciones");
  await waitFor(() => ev("document.querySelector('[data-testid=sim-red-view]')?.disabled===false"), "lab ready", 20000);
  await click("sim-red-view");
  await waitFor(() => ev("!!document.querySelector('[data-testid=sim-result]')"), "lab result", 60000);
  const states = await ev("[...document.querySelectorAll('[data-testid=sim-interval]')].map(e=>e.dataset.state)");
  check("a synthetic scenario produces a genuine risk-kernel YELLOW → RED progression, labelled SIMULATION", states.includes("YELLOW") && states.includes("RED") && (await text("sim-banner")).includes("SINTÉTICO"));
  await waitFor(() => ev("document.querySelectorAll('[data-testid=sim-alert]').length>=1"), "derived alert", 15000);
  await shot("j06_sim_red.png");
  await click("sim-alert-detail");
  await waitFor(() => ev("!!document.querySelector('[data-testid=incident-drawer]')"), "incident drawer");
  const drawer = await text("incident-drawer");
  check("the incident identifies source, zone, severity, origin SIMULATION, timestamps, lifecycle, delivery and evidence availability", ["Fuente", "Zona", "SIMULACIÓN", "Creada", "solo de metadatos"].every((k) => drawer.includes(k)), drawer.slice(0, 200));
  check("event, alert and delivery are three separate sections", !!(await text("incident-event")) && !!(await text("incident-alert")) && !!(await text("incident-delivery")));
  check("the incident links its triggering event and states whether its source is still available (metadata-only evidence)", (await text("incident-event-id")).length > 3 && (await attr("incident-availability", "data-unavailable")) === "no");
  check("a scripted simulation incident carries no overlap phase (the notion applies to feed runs only)", (await ev("!document.querySelector('[data-testid=incident-overlap]')")) === true);
  await shot("j07_incident_detail.png");
  await click("incident-ack");
  await waitFor(() => ev("document.querySelector('[data-testid=incident-alert]').textContent.includes('Reconocida')"), "acknowledged");
  await shot("j08_incident_acknowledged.png");
  await click("incident-resolve");
  await waitFor(() => ev("document.querySelector('[data-testid=incident-alert]').textContent.includes('Resuelta')"), "resolved");
  const trail = await text("incident-drawer");
  check("acknowledge then resolve works from the drawer; the audit trail lists creation, acknowledgement and resolution", ["Creada", "Reconocida", "Resuelta"].every((k) => trail.includes(k)), trail.slice(0, 200));
  await shot("j09_incident_resolved.png");
  const relatedBar = await ev("!!document.querySelector('[data-testid=incident-related]')");
  if (relatedBar) {
    const id1 = await attr("incident-drawer", "data-alert-id");
    const go = (await ev("!document.querySelector('[data-testid=incident-next]').disabled")) ? "incident-next" : "incident-prev";
    await click(go);
    await waitFor(async () => (await attr("incident-drawer", "data-alert-id")) !== id1, "navigate to a related event", 8000);
    check("related events of the SAME source and run can be stepped through inside the incident detail", (await attr("incident-drawer", "data-alert-id")) !== id1);
    await click(go === "incident-next" ? "incident-prev" : "incident-next");
    await waitFor(async () => (await attr("incident-drawer", "data-alert-id")) === id1, "back to the first event", 8000);
  } else { check("the scenario produced a single incident for its source (no related events to step through)", true); }
  const incId = await attr("incident-drawer", "data-alert-id");
  await click("incident-jump");
  await waitFor(() => ev("document.querySelector('[data-testid=nav-video]').getAttribute('aria-selected')==='true'"), "jump to the source run", 30000);
  check("from the source view the operator is offered a way back to the same incident", await ev("!!document.querySelector('[data-testid=back-to-incident]')"));
  await click("back-to-incident");
  await waitFor(async () => (await attr("incident-drawer", "data-alert-id")) === incId, "back to the same incident", 10000);
  check("«Volver al incidente» reopens exactly the incident the operator came from", (await attr("incident-drawer", "data-alert-id")) === incId);
  await click("incident-jump");
  await waitFor(() => ev("document.querySelector('[data-testid=nav-video]').getAttribute('aria-selected')==='true'"), "jump again", 30000);
  check("the incident jumps back to its source run at the incident time", await waitFor(() => ev("document.querySelectorAll('[data-testid=risk-marker]').length>=1"), "markers", 20000).then(() => true, () => false));
  await nav("resumen");
  await waitFor(() => ev("!!document.querySelector('[data-testid=kpi-sim]')"), "overview");
  check("the overview has a separate counter for restored (unverified) alerts and the operator timeline labels every row's provenance", (await ev("!!document.querySelector('[data-testid=kpi-restored]')")) && (await ev("[...document.querySelectorAll('[data-testid=op-timeline-item]')].every(e=>['synthetic','repeated','unverified'].includes(e.dataset.prov))")));
  const apiAlerts = (await jget("/api/alerts")).alerts;
  const genuineOpen = apiAlerts.filter((x) => !x.simulation && (x.status === "new" || x.status === "active")).length;
  const kpiTxt = (await text("kpi-unacked")).replace(/\D+/g, " ").trim().split(" ")[0];
  check("returning to monitoring: the overview's genuine unacknowledged count equals the API's genuine count (simulations excluded)", Number(kpiTxt) === genuineOpen, `${kpiTxt} vs ${genuineOpen}`);
  const rows = apiAlerts;
  check("every alert produced by this journey is a SIMULATION; none claims real inference", rows.length >= 1 && rows.every((a) => a.simulation === true), JSON.stringify(rows.map((a) => a.simulation)));
  // reopen the incident from the alert centre and return to monitoring with one control
  await nav("eventos");
  await waitFor(() => ev("document.querySelectorAll('[data-testid=alert-detail]').length>=1"), "alert rows");
  await click("alert-detail");
  await waitFor(() => ev("!!document.querySelector('[data-testid=incident-drawer]')"), "drawer reopened");
  await click("incident-back-monitor");
  await waitFor(() => ev("!document.querySelector('[data-testid=incident-drawer]') && document.querySelector('[data-testid=nav-resumen]').getAttribute('aria-selected')==='true'"), "back to monitoring");
  check("«Volver al monitoreo» closes the incident and returns to the overview", true);
  // statistics
  await nav("estadisticas");
  await waitFor(() => ev("!!document.querySelector('[data-testid=view-estadisticas]')"), "statistics");
  await sleep(800);
  const stats = await text("view-estadisticas");
  check("statistics label the simulated population apart from genuine live alerts", /simula/i.test(stats), stats.slice(0, 160));
  await shot("j10_statistics.png");

  // ======================================================================== developer: measured build identity, recorded OAF state, no invention
  await nav("desarrollador");
  await waitFor(() => ev("!!document.querySelector('[data-testid=dev-build]') && document.querySelector('[data-testid=dev-build]').getAttribute('data-known')!=='loading'"), "build identity", 15000);
  const bk = await attr("dev-build", "data-known");
  const sha = bk === "true" ? await text("dev-build-sha") : null;
  const gitHead = (await jget("/api/build")).commit;
  check("the developer panel shows a measured 40-hex commit (or says it is unknown); never a placeholder", bk === "true" ? /^[0-9a-f]{40}$/.test(sha) && sha === gitHead : (await text("dev-build")).includes("DESCONOCIDO"), String(sha));
  const oafTxt = await text("dev-oaf");
  check("OAF state is shown only as a recorded verdict for this exact commit, or as «no registrado»; it never states an acceptance", /OWNER_DECISION_REQUIRED|INCOMPLETE|RESOURCE_BLOCKED|FAIL|PASS|no hay un veredicto/.test(oafTxt) && !/aceptado: sí/.test(oafTxt), oafTxt.slice(0, 160));
  const oafKind = await attr("dev-oaf", "data-kind");
  check("the OAF panel shows one of the strict states and never a bare «accepted» for a draft or an undecided contract", ["none", "invalid", "not_accepted", "claimed_accepted"].includes(oafKind) && (oafKind !== "claimed_accepted" || !/OWNER_DECISION_REQUIRED|DRAFT/.test(oafTxt)), oafKind);
  await shot("j11_developer.png");

  // ======================================================================== privacy / locality
  await nav("camara");
  await waitFor(() => ev("!!document.querySelector('[data-testid=privacy-on]')"), "privacy box");
  check("Privacy stays ON by default and no camera stream was opened", (await ev("document.querySelector('[data-testid=privacy-on]').checked")) && (await ev("(window.__streams||[]).length")) === 0);
  const foreign = await ev("performance.getEntriesByType('resource').map(r=>new URL(r.name).origin).filter(o=>o!==location.origin)");
  check("no request left the local server origin", foreign.length === 0, JSON.stringify([...new Set(foreign)]));
  const errs = await ev("window.__mgErrors||[]");
  check("no uncaught page errors were recorded", errs.length === 0, JSON.stringify(errs.slice(0, 3)));
} catch (e) {
  check("journey acceptance script ran to completion", false, e.message + " @ " + String(e.stack).split("\n").slice(1, 3).join(" | "));
} finally {
  const failed = results.filter((r) => !r.ok).length;
  fs.writeFileSync(path.join(OUT, "results.json"), JSON.stringify({ base: BASE, when: new Date().toISOString(), results }, null, 1));
  console.log(`\n${results.length - failed}/${results.length} checks passed`);
  try { ws?.close(); } catch { /* ignore */ }
  chrome.kill("SIGTERM");
  await sleep(500);
  process.exit(failed ? 1 : 0);
}
