// FINAL-ENGINEERING-15-R1: browser acceptance of the EXTERNAL SAM result import and review, English and Spanish, over an ISOLATED server (empty storage).
// Usage: node e2e/external_import_acceptance.mjs http://127.0.0.1:<isolated port> <outdir> <fixture dir>
// Uses only the bundled SYNTHETIC fixture and files derived from it. Makes no provider call, starts no model, talks only to the loopback origin. Nothing here is a Meta result.
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const BASE = process.argv[2];
if (!BASE || !/^http:\/\/127\.0\.0\.1:\d+$/.test(BASE)) { console.error("usage: node e2e/external_import_acceptance.mjs http://127.0.0.1:<port> <outdir> <fixture dir>"); process.exit(3); }
const OUT = path.resolve(process.argv[3] ?? "logs/ext10");
fs.mkdirSync(OUT, { recursive: true });
const PORT = 9376;
const profile = fs.mkdtempSync(path.join(os.tmpdir(), "mg-simcam-"));
const chrome = spawn("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", ["--headless=new", `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`, "--window-size=1500,1200",
  "--no-first-run", "--no-default-browser-check", "--disable-background-networking", "--disable-component-update", "--disable-sync", "--no-pings", "--disable-domain-reliability",
  "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1 , EXCLUDE localhost", "about:blank"], { stdio: "ignore" });

const results = [], notes = [];
const check = (name, ok, detail = "") => { results.push({ name, ok: !!ok, detail: String(detail) }); console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  — " + detail : ""}`); };
const note = (n) => { notes.push(n); console.log(`NOTE  ${n}`); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function waitFor(fn, what, timeout = 20000) { const t0 = Date.now(); for (;;) { try { const v = await fn(); if (v) return v; } catch { /* retry */ } if (Date.now() - t0 > timeout) throw new Error(`timeout waiting for ${what}`); await sleep(150); } }
let ws, nextId = 1; const pending = new Map();
async function connect() {
  const page = await waitFor(async () => (await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()).find((t) => t.type === "page"), "chrome");
  ws = new WebSocket(page.webSocketDebuggerUrl); await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
  ws.onmessage = (m) => { const d = JSON.parse(m.data); if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); } };
}
const send = (method, params = {}) => new Promise((res, rej) => { const id = nextId++; pending.set(id, (d) => (d.error ? rej(new Error(JSON.stringify(d.error))) : res(d.result))); ws.send(JSON.stringify({ id, method, params })); });
const ev = async (expression) => { const r = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true }); if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description ?? "eval error"); return r.result.value; };
const shot = async (name) => { const r = await send("Page.captureScreenshot", { format: "png" }); fs.writeFileSync(path.join(OUT, name), Buffer.from(r.data, "base64")); };
const click = (tid) => ev(`(()=>{const b=document.querySelector('[data-testid=${tid}]');if(!b||b.disabled)return false;b.click();return true})()`);
const text = (tid) => ev(`document.querySelector('[data-testid=${tid}]')?.innerText||''`);
const attr = (tid, a) => ev(`document.querySelector('[data-testid=${tid}]')?.getAttribute(${JSON.stringify(a)})`);
const api = (p) => ev(`(async()=>{const tok=document.querySelector('meta[name=mg-session-token]').content;const r=await fetch(${JSON.stringify(p)},{headers:{'X-MG-Token':tok}});return {status:r.status,body:await r.text()}})()`);
const goto = async (url) => { await send("Page.navigate", { url: BASE + url }); await waitFor(() => ev("document.readyState==='complete' && !!document.querySelector('h1')"), "page " + url); await sleep(400); };
const nav = (a) => click(`nav-${a}`);
const AREAS = [["resumen", "view-resumen"], ["video", "view-video"], ["camara", "webcam-panel"], ["feeds", "view-feeds"], ["sesiones", "view-sesiones"], ["eventos", "view-eventos"], ["simulaciones", "view-simulaciones"], ["estadisticas", "view-estadisticas"], ["desarrollador", "view-desarrollador"], ["configuracion", "view-configuracion"]];
const SPANISH_CHARS = /[áéíóúñ¿¡]/i;
const SPANISH_WORDS = /\b(el|la|los|las|de|del|con|sin|para|por|una|un|que|se|es|no|esta|este|fuente|video|evento|eventos|zona|zonas|simulaci[oó]n|explicaci[oó]n|configuraci[oó]n|desarrollador|c[aá]mara|sesiones|estad[ií]sticas)\b/i;
const visibleStrings = () => ev(`(()=>{const out=[];const w=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);let n;while(n=w.nextNode()){const s=n.nodeValue.trim();if(!s||!n.parentElement)continue;const st=getComputedStyle(n.parentElement);if(st.display==='none'||st.visibility==='hidden')continue;const tag=n.parentElement.tagName;if(tag==='SCRIPT'||tag==='STYLE')continue;const holder=n.parentElement.closest('[data-testid]');out.push([holder?holder.dataset.testid:'',s.slice(0,160)])}return out})()`);
const labels = () => ev(`[...document.querySelectorAll('[aria-label],[title],[alt]')].map(e=>[e.dataset.testid||e.tagName,e.getAttribute('aria-label')||e.getAttribute('title')||e.getAttribute('alt')])`);
const residualSpanish = (strings) => strings.filter(([, s]) => SPANISH_CHARS.test(s));



const apij = async (p) => JSON.parse((await api(p)).body);
const helps = [];
const live = () => ev("window.__tracks.filter(t=>t.readyState==='live').length");
const st = () => ev("(()=>{const e=document.querySelector('[data-testid=cam-status]');return e?e.dataset.state+'|tracks='+e.dataset.tracks:''})()");
const view = () => ev("document.querySelector('[data-testid=cam-view]')?.dataset.view||''");
const analysing = () => ev("document.querySelector('[data-testid=cam-view]')?.dataset.analysing||''");
const waitView = (v, ms = 15000) => waitFor(async () => (await view()) === v, "view " + v, ms);
const toCam = async () => { await goto("/?lang=" + LANG); await nav("camara"); await waitFor(() => ev("!!document.querySelector('[data-testid=webcam-panel]')"), "webcam panel"); };
let LANG = "en";
try {
  await connect();
  await send("Page.enable");
  // SIMULATED_DEVICE_TEST: navigator.mediaDevices is replaced by a canvas-backed fake. No physical camera is ever requested.
  await send("Page.addScriptToEvaluateOnNewDocument", { source: `
    window.__tracks = []; window.__gum = 0; window.__mode = "ok";
    const fake = async () => { window.__gum++;
      const err = { denied: ["Permission denied", "NotAllowedError"], nodevice: ["Requested device not found", "NotFoundError"], inuse: ["Could not start video source", "NotReadableError"] }[window.__mode];
      if (err) throw new DOMException(err[0], err[1]);
      const c = document.createElement("canvas"); c.width = 640; c.height = 360; const x = c.getContext("2d");
      setInterval(() => { x.fillStyle = "#345"; x.fillRect(0, 0, 640, 360); x.fillStyle = "#fc0"; x.fillRect((Date.now() / 20) % 500, 150, 100, 60); }, 50);
      const s = c.captureStream(10); s.getVideoTracks().forEach((t) => { Object.defineProperty(t, "label", { value: "SIMULATED synthetic canvas" }); window.__tracks.push(t); }); return s; };
    Object.defineProperty(navigator, "mediaDevices", { value: { getUserMedia: fake, enumerateDevices: async () => { window.__enum = (window.__enum || 0) + 1; return []; } }, configurable: true });
    window.__errs = []; window.addEventListener("error", (e) => window.__errs.push(String(e.message)));` });
  await send("Emulation.setDeviceMetricsOverride", { width: 1500, height: 1400, deviceScaleFactor: 1, mobile: false });
  await toCam();
  check("C1 initial view is 'stopped', zero tracks, camera not requested", (await view()) === "stopped" && /^off\|tracks=0/.test(await st()) && (await ev("window.__gum")) === 0, `${await view()} ${await st()}`);
  check("C2 the model-weights line is shown before any analysis (honest: weights status, not 'model loaded')", /Model weights/.test(await text("cam-model")) && /verified|NOT verified|unknown/i.test(await text("cam-model")), await text("cam-model"));
  await click("cam-start"); await waitView("preview_only");
  check("C3 PREVIEW ONLY: 1 live track, view says Preview only, analysing=no, banner says nothing is analysed", (await live()) === 1 && /Preview only/.test(await text("cam-view")) && (await analysing()) === "no" && /nothing is being analysed/i.test(await text("cam-preview-banner")), await text("cam-view"));
  check("C4 preview does NOT start inference: no session, no frames, no /api/webcam request, no live counters", (await ev("document.querySelector('[data-testid=cam-live]')===null")) && (await ev("performance.getEntriesByType('resource').filter(r=>r.name.includes('/api/webcam')).length")) === 0);
  check("C5 accessibility: status is a polite live region; the video has a label saying it is not analysis; the overlay is hidden from assistive tech", (await ev("document.querySelector('[data-testid=cam-view]').getAttribute('role')==='status' && document.querySelector('[data-testid=cam-view]').getAttribute('aria-live')==='polite'")) && /not analysis/i.test(await ev("document.querySelector('[data-testid=cam-video]').getAttribute('aria-label')")) && (await ev("document.querySelector('[data-testid=cam-overlay]').getAttribute('aria-hidden')")) === "true");
  await shot("c_preview_only.png");
  // D1: the model cannot be started (first creation fails in this server)
  await click("cam-analyze"); await waitView("analysis_unavailable");
  const un = await text("cam-error");
  check("C6 D1: analysis start with an unavailable model: camera stays on as PREVIEW, view 'Analysis unavailable', analysing=no, no session, no live counters", (await st()).startsWith("on|") && (await analysing()) === "no" && (await ev("document.querySelector('[data-testid=cam-live]')===null")) && /unavailable/i.test(await text("cam-view")), `${await view()} ${await st()}`);
  check("C7 D1: the error is the model-unavailable message, not a bare HTTP 500 or a stack trace", /could not be started/i.test(un) && !/Internal Server Error|Traceback|RuntimeError|simulated/.test(un + (await text("cam-unavailable-banner"))), JSON.stringify(un));
  check("C8 D1: no risk is shown anywhere for the failed start (no risk badge, no GREEN)", (await ev("document.querySelectorAll('[data-testid^=cam-risk-]').length")) === 0 && !/GREEN/.test(await text("webcam-panel")));
  await shot("c_analysis_unavailable.png");
  // recovery: the second creation succeeds (test double that sees nothing)
  await click("cam-analyze"); await waitView("analysis_active", 20000);
  await waitFor(() => ev("!!document.querySelector('[data-testid=cam-live]')"), "live counters", 20000);
  check("C9 RECOVERY: a new attempt starts the analysis; view 'Analysis active', analysing=yes, analysis banner, live counters appear", (await analysing()) === "yes" && /analysed locally/i.test(await text("cam-analysis-banner")) && (await ev("!!document.querySelector('[data-testid=cam-live]')")) === true, await view());
  await waitFor(async () => Number(await ev("document.querySelector('[data-testid=cam-counters]')?.dataset.received||0")) >= 2, "frames received", 20000);
  check("C10 frames reach the (test double) detector: received counter rises; the preview-only banner is gone", Number(await ev("document.querySelector('[data-testid=cam-counters]').dataset.received")) >= 2 && (await ev("document.querySelector('[data-testid=cam-preview-banner]')===null")));
  await shot("c_analysis_active.png");
  await click("cam-stop"); await waitFor(async () => /^off\|/.test(await st()), "stopped", 30000);
  check("C11 stop and save: camera released (0 live tracks), view 'stopped', stored session shown", (await live()) === 0 && (await view()) === "stopped" && (await ev("!!document.querySelector('[data-testid=cam-stored]')")) === true, await st());
  // D3: three different device failures
  for (const [mode, want, re] of [["denied", "permission_denied", /permission denied/i], ["nodevice", "no_device", /No camera device found/i], ["inuse", "device_error", /Camera device error/i]]) {
    await ev(`window.__mode='${mode}'`); await click("cam-start"); await waitView(want, 10000);
    helps.push(await text("cam-denied-help"));
    check(`C12 D3: ${mode} -> view '${want}' with its own text and help; zero live tracks`, re.test(await text("cam-view")) && !!(await text("cam-denied-help")) && (await live()) === 0, `${await text("cam-view")} | ${await text("cam-denied-help")}`);
  }
  check("C13 D3: the three failure helps are three different texts", new Set(helps).size === 3, JSON.stringify(helps.map((h) => h.slice(0, 30))));
  await ev("window.__mode='ok'"); await click("cam-start"); await waitView("preview_only");
  await ev("window.__tracks[window.__tracks.length-1].stop(); window.__tracks[window.__tracks.length-1].dispatchEvent(new Event('ended'))"); await sleep(600);
  check("C14 device disconnect while previewing: view 'stopped' with a notice, 0 live tracks", (await view()) === "stopped" && !!(await text("cam-notice")) && (await live()) === 0);
  await click("cam-start"); await waitView("preview_only"); await nav("resumen"); await sleep(600);
  check("C15 leaving the camera page releases the camera", (await live()) === 0);
  // Spanish camera page
  LANG = "es"; await toCam(); await click("cam-start"); await waitView("preview_only");
  check("C16 Spanish: 'Solo vista previa' and 'no se analiza nada'", /Solo vista previa/.test(await text("cam-view")) && /no se analiza nada/i.test(await text("cam-preview-banner")), await text("cam-view"));
  await click("cam-release"); await sleep(300); LANG = "en";
  // scenarios
  await goto("/?lang=en"); await nav("simulaciones"); await waitFor(() => ev("!!document.querySelector('[data-testid=scenarios-panel]')"), "scenarios panel");
  const apiScn = async (id) => JSON.parse((await api("/api/scenarios/" + id)).body);
  const openScn = async (id) => { await waitFor(() => ev(`!!document.querySelector('[data-testid=scn-open-${id}]')`), "list " + id); await click("scn-open-" + id); await waitFor(async () => (await attr("scn-detail", "data-scenario")) === id, "scenario " + id); await sleep(150); };
  check("S1 scenarios panel: SYNTHETIC banner says not footage / not RF-DETR / no camera or model ran", /SYNTHETIC SCENARIO REPLAY/.test(await text("scn-banner")) && /not real footage and not RF-DETR/.test(await text("scn-banner")) && /no camera and no model ran/.test(await text("scn-banner")), await text("scn-banner"));
  await openScn("case01_haul_truck_pedestrian"); const c1 = await apiScn("case01_haul_truck_pedestrian");
  check("S2 CASE 01: provenance rows (SYNTHETIC_SCENARIO_REPLAY, real footage: no, model inference: no), synthetic tag, human review pending", (await text("scn-prov-kind")) === "SYNTHETIC_SCENARIO_REPLAY" && (await text("scn-prov-footage")) === "no" && (await text("scn-prov-inference")) === "no" && /SYNTHETIC/.test(await text("scn-synthetic-tag")) && (await attr("scn-review", "data-pending")) === "yes" && /pending human review/i.test(await text("scn-review")));
  const states = await ev("[...document.querySelectorAll('[data-testid=scn-transitions] tbody tr')].map(r=>r.dataset.state)");
  check("S3 CASE 01 timeline shows GREEN, UNKNOWN, YELLOW, RED, ... in the kernel's order, equal to the API's recomputation", JSON.stringify(states) === JSON.stringify(c1.transitions.map((x) => x.to)) && states.includes("RED") && states.indexOf("YELLOW") < states.indexOf("RED") && states.includes("UNKNOWN"), JSON.stringify(states));
  check("S4 CASE 01: events list shows the YELLOW, RED, YELLOW rules; strip contains red; zone drawn; caveats listed", (await ev("[...document.querySelectorAll('[data-testid=scn-events] li')].map(l=>l.dataset.severity).join(',')")) === "YELLOW,RED,YELLOW" && (await ev("!!document.querySelector('[data-testid=scn-strip] [data-state=RED]')")) && (await ev("!!document.querySelector('[data-testid=scn-zone] polygon')")) && (await ev("document.querySelectorAll('[data-testid=scn-caveats] li').length")) >= 3);
  check("S5 the digest shown is the API's digest prefix and the result is stable across two reads", (await text("scn-digest")).startsWith(c1.digest.slice(0, 16)) && (await apiScn("case01_haul_truck_pedestrian")).digest === c1.digest);
  await shot("s_case01_en.png");
  await openScn("case03a_excavator_alone");
  check("S6 CASE 03a: equipment alone -> no events, no RED/YELLOW in the strip, caveat says it stays GREEN", (await ev("!!document.querySelector('[data-testid=scn-no-events]')")) && !(await ev("!!document.querySelector('[data-testid=scn-strip] [data-state=RED], [data-testid=scn-strip] [data-state=YELLOW]')")) && /stays GREEN/.test(await text("scn-caveats")));
  await openScn("case03b_excavator_and_person");
  check("S7 CASE 03b: person + excavator -> a RED event in the labelled synthetic replay", (await ev("[...document.querySelectorAll('[data-testid=scn-events] li')].some(l=>l.dataset.severity==='RED')")) && (await attr("scn-detail", "data-synthetic")) === "yes");
  await openScn("case02b_forklift_not_recognised");
  check("S8 CASE 02b: unrecognised forklift -> YELLOW only, never RED, caveat names the false negative", !(await ev("[...document.querySelectorAll('[data-testid=scn-events] li')].some(l=>l.dataset.severity==='RED')")) && /false negative/.test(await text("scn-caveats")) && /no forklift class/.test(await text("scn-caveats")));
  await openScn("control_source_silent");
  check("S9 CONTROL: a silent source shows hatched 'no observation' time and UNKNOWN, never GREEN for the silence", (await ev("!!document.querySelector('[data-testid=scn-strip] [data-state=NO_SAMPLE]')")) && /never GREEN/.test(await text("scn-caveats")) && (await ev("(()=>{const c=document.querySelector('[data-testid=scn-coverage]').innerText;const n=(k)=>parseFloat((c.match(new RegExp(k+' ([0-9.]+) s'))||[0,0])[1]);return n('UNKNOWN')>n('GREEN')})()")));
  LANG = "es"; await goto("/?lang=es"); await nav("simulaciones"); await waitFor(() => ev("!!document.querySelector('[data-testid=scenarios-panel]')"), "scenarios panel es");
  await waitFor(() => ev("!!document.querySelector('[data-testid=scn-open-case01_haul_truck_pedestrian]')"), "es scenario list");
  await click("scn-open-case01_haul_truck_pedestrian"); await waitFor(() => ev("!!document.querySelector('[data-testid=scn-detail]')"), "es detail"); await sleep(200);
  check("S10 Spanish: synthetic banner, tag and 'pendiente de revisión humana'", /REPETICIÓN DE ESCENARIO SINTÉTICA/.test(await text("scn-banner")) && /SINTÉTICO/.test(await text("scn-synthetic-tag")) && /pendiente de revisión humana/i.test(await text("scn-review")));
  await shot("s_case01_es.png");
  check("S11 no scenario call created a run, session or provider request: canonical runs listing is empty and /api/providers still reports no external calls", (await apij("/api/runs")).length === 0 && (await ev(`(async()=>{const tok=document.querySelector('meta[name=mg-session-token]').content;const d=await (await fetch('/api/providers',{headers:{'X-MG-Token':tok}})).json();return d.invariants.external_calls_possible===false})()`)));
  check("C17 no script errors in the page", (await ev("window.__errs.length")) === 0, await ev("JSON.stringify(window.__errs)"));
} catch (e) {
  check("script completed without an exception", false, String(e.message).slice(0, 300));
} finally {
  const fail = results.filter((r) => !r.ok);
  fs.writeFileSync(path.join(OUT, "simulated_camera_acceptance.result.json"), JSON.stringify({ label: "SIMULATED_DEVICE_TEST", base: BASE, pass: results.length - fail.length, fail: fail.length, results }, null, 1));
  console.log(`\n${results.length - fail.length} PASS, ${fail.length} FAIL`);
  try { await send("Browser.close"); } catch { /* closing */ }
  try { ws.close(); } catch { /* closed */ }
  chrome.kill("SIGTERM"); await sleep(800);
  try { fs.rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 300 }); } catch { /* temp */ }
  process.exit(fail.length ? 1 : 0);
}
