// MG-PRODUCT-VERTICAL-03 Operations Console browser acceptance (journeys, keyboard/accessibility, responsive layout, evidence screenshots)
// (boilerplate shared with the other suites; originally the MG-VERTICAL-02 multi-feed suite) over the DevTools protocol with the system Google Chrome (no extra packages).
// Usage: node e2e/mvp_acceptance.mjs [baseUrl]  -> writes logs/console_vertical03/{results.json,*.png}; exit 1 on any failed check.
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
const PORT = 9335;
const OUT = path.resolve("logs/console_vertical03");
fs.mkdirSync(OUT, { recursive: true });
const profile = fs.mkdtempSync(path.join(os.tmpdir(), "mg-chrome-console-"));
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
  await send("Page.addScriptToEvaluateOnNewDocument", { source: "window.__mgErrors=[];window.addEventListener('error',e=>window.__mgErrors.push(String(e.message)));window.addEventListener('unhandledrejection',e=>window.__mgErrors.push('rejection: '+String(e.reason)));window.__streams=[];const g=navigator.mediaDevices&&navigator.mediaDevices.getUserMedia&&navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);if(g)navigator.mediaDevices.getUserMedia=async(c)=>{const s=await g(c);window.__streams.push(s);return s;};" });
  await send("Emulation.setDeviceMetricsOverride", { width: 1500, height: 1000, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url: BASE });
  await waitFor(() => ev("document.querySelector('h1')?.textContent.includes('MineGuard')"), "page");

  // ======================================================================== landmarks, honesty, no outbound requests
  check("document language is Spanish, one h1, a skip link, a nav landmark and one main content region", await ev("document.documentElement.lang==='es' && document.querySelectorAll('h1').length===1 && !!document.querySelector('a.skip-link[href=\"#contenido\"]') && !!document.querySelector('nav[aria-label]') && !!document.getElementById('contenido')"));
  check("exactly ten product areas in the navigation (nav role=tab)", (await ev("document.querySelectorAll('nav [role=tab]').length")) === 10);
  check("the limits are visible from the first screen (experimental, not a certified safety system, no collision prediction)", (await text("limits")).includes("No es un sistema de seguridad certificado") && (await text("limits")).includes("no es una colisión"));
  check("a persistent footer repeats the not-a-safety-system statement", (await ev("document.querySelector('footer.legal')?.textContent||''")).includes("No es un sistema de seguridad certificado"));
  await shot("00_first_screen.png");

  // ======================================================================== keyboard and focus
  await ev("document.activeElement && document.activeElement.blur(); window.scrollTo(0,0)");
  await key("Tab");
  check("the first Tab stop is the skip link", await ev("document.activeElement?.classList.contains('skip-link')"));
  await key("Tab"); await key("Tab");
  const ring = await ev("(()=>{const s=getComputedStyle(document.activeElement);return {tag:document.activeElement.tagName, w:parseFloat(s.outlineWidth), st:s.outlineStyle}})()");
  check("keyboard focus is visible (an outline is drawn on the focused control)", ring.st !== "none" && ring.w >= 2, JSON.stringify(ring));
  await ev("document.querySelector('[data-testid=nav-estadisticas]').focus()");
  await key("Enter");
  check("Enter on a focused navigation item opens that area", await waitFor(() => ev("document.querySelector('[data-testid=nav-estadisticas]').getAttribute('aria-selected')==='true' && !!document.querySelector('[data-testid=view-estadisticas]')"), "estadisticas via keyboard", 8000).then(() => true, () => false));
  await nav("resumen");

  // ======================================================================== real MP4 run for the video / simulation journeys (small stride: fast)
  const vids = await jget("/api/videos");
  const job = JSON.parse((await jpost(`/api/videos/${vids[0].video_id}/analyze`, { stride: 15, threshold: 0.3 })).body);
  await waitFor(async () => { const j = await jget(`/api/jobs/${job.job_id}`); if (j.status === "failed") throw new Error("analysis failed: " + j.error); return j.status === "done"; }, "MP4 analysis done", 240000);
  const runs = await jget("/api/runs");
  check("a real model-inference run exists for the review journeys (registered nonpersonal clip)", runs.length >= 1, JSON.stringify(runs.map((r) => r.origin)));

  // ======================================================================== Simulation Lab -> incident detail -> lifecycle
  await nav("simulaciones");
  await waitFor(() => ev("document.querySelector('[data-testid=sim-red-view]')?.disabled===false"), "lab ready", 20000);
  check("before running, the lab explains the scenario, says it is synthetic, and shows an empty result state", (await text("sim-banner")).includes("SINTÉTICO") && !!(await text("sim-empty")) && (await text("view-simulaciones")).includes("AMARILLO"));
  await click("sim-red-view");
  await waitFor(() => ev("!!document.querySelector('[data-testid=sim-result]')"), "lab result", 60000);
  const states = await ev("[...document.querySelectorAll('[data-testid=sim-interval]')].map(e=>e.dataset.state)");
  check("the lab shows the YELLOW → RED progression decided by the real risk engine", states.includes("YELLOW") && states.includes("RED") && states.indexOf("YELLOW") < states.lastIndexOf("RED"), states.join(","));
  await waitFor(() => ev("document.querySelectorAll('[data-testid=sim-alert]').length>=1"), "derived alerts", 15000);
  check("derived alerts are labelled SIMULACIÓN", (await text("sim-derived")).includes("SIMULACIÓN"));
  await shot("simlab_result.png");
  await click("sim-alert-detail");
  await waitFor(() => ev("!!document.querySelector('[data-testid=incident-drawer]')"), "incident drawer", 10000);
  check("the incident drawer opens from the lab, states it is a simulation, and keeps event / alert / delivery state apart", (await text("incident-sim")).includes("SIMULACIÓN") && !!(await text("incident-event")) && !!(await text("incident-alert")) && !!(await text("incident-delivery")));
  check("evidence is honest: metadata only, no invented thumbnail", (await text("incident-evidence")).includes("solo de metadatos") && (await ev("document.querySelectorAll('[data-testid=incident-drawer] img').length")) === 0);
  await shot("incident_drawer.png");
  await click("incident-ack");
  await waitFor(() => ev("document.querySelector('[data-testid=incident-alert]').textContent.includes('Reconocida')"), "acknowledged", 8000);
  check("acknowledging in the drawer changes the ALERT state and not the delivery or event state", (await text("incident-alert")).includes("Reconocida") && !(await text("incident-delivery")).includes("Reconocida"));
  await key("Escape");
  check("Escape closes the incident drawer opened from the lab", await waitFor(() => ev("!document.querySelector('[data-testid=incident-drawer]')"), "drawer closed", 5000).then(() => true, () => false));
  await nav("eventos");
  await waitFor(() => ev("document.querySelectorAll('[data-testid=alert-row]').length>=1"), "alert rows", 10000);
  await ev("document.querySelector('[data-testid=alert-detail]').focus()");
  await sleep(200);
  check("the alert row's Detalle button can be focused and activated from the keyboard (Enter)", await ev("document.activeElement?.getAttribute('data-testid')==='alert-detail'"));
  await key("Enter");
  await waitFor(() => ev("!!document.querySelector('[data-testid=incident-drawer]')"), "drawer from the center", 8000);
  await key("Escape");
  await sleep(500);
  const afterEsc = await ev("({drawer: !!document.querySelector('[data-testid=incident-drawer]'), active: (document.activeElement?.getAttribute('data-testid')||document.activeElement?.tagName)})");
  check("Escape closes the drawer and focus returns to the control that opened it", !afterEsc.drawer && afterEsc.active === "alert-detail", JSON.stringify(afterEsc));
  await click("alert-detail");
  await waitFor(() => ev("!!document.querySelector('[data-testid=incident-drawer]')"), "drawer again", 8000);
  await click("incident-resolve");
  await waitFor(() => ev("document.querySelector('[data-testid=incident-alert]').textContent.includes('Resuelta')"), "resolved", 8000);
  await click("incident-jump");
  await waitFor(() => ev("document.querySelector('[data-testid=nav-video]').getAttribute('aria-selected')==='true'"), "jump to the run at the incident instant", 30000);
  check("from the incident the operator can jump to the source run (simulation → Video MP4)", true);

  // ======================================================================== Video review: event markers, jump and back
  await waitFor(() => ev("document.querySelectorAll('[data-testid=risk-marker]').length>=1"), "event markers on the timeline", 20000);
  check("the risk timeline has clickable event markers and the events list has a jump control", (await ev("document.querySelectorAll('[data-testid=risk-marker]').length"))>=1 && (await ev("document.querySelectorAll('[data-testid=risk-event-jump]').length"))>=1);
  const before = await text("readout");
  await ev("document.querySelector('[data-testid=risk-event-jump]').click()");
  await sleep(1200);
  check("jumping to an event moves the playhead and offers a way back", (await text("readout")) !== before && (await ev("!!document.querySelector('[data-testid=event-back]')")));
  await click("event-back");
  await sleep(1000);
  check("going back restores the previous instant", (await text("readout")).split("·")[0] === before.split("·")[0], before.split("·")[0] + " vs " + (await text("readout")).split("·")[0]);
  await shot("video_review.png");

  // ======================================================================== three live feeds: grid, expand, back, isolation
  await nav("feeds");
  await waitFor(() => ev("document.querySelector('[data-testid=feeds-video]')?.options.length>0 && !document.querySelector('[data-testid=feeds-create3]').disabled"), "feeds form", 20000);
  await click("feeds-create3");
  await waitFor(async () => (await panelIds()).length === 3, "three feed panels", 20000);
  const fids = await panelIds();
  await click("feeds-start-all");
  await waitFor(async () => (await Promise.all(fids.map(analyzed))).every((n) => n >= 3), "three feeds analysed (real model)", 120000);
  check("three feeds with distinct identities, each with its own live flag and separate rate metrics (sampling / measured inference / latency / queue)", new Set(fids).size === 3 && (await Promise.all(fids.map((f) => panelAttr(f, "feed-flag", "data-flag")))).every((x) => x === "live") && (await ev("document.querySelectorAll('[data-testid=feed-stats] .metric').length")) === 6 && (await ev("document.querySelectorAll('[data-testid=feed-eng] .metric').length")) === 6);   // V04: sampling + measured inference are primary; latency + queue sit in the engineering detail
  check("a repeated file is declared a concurrency simulation, never several cameras", (await text("feeds-repeated-banner")).includes("NO representan varias cámaras"));
  await shot("feeds_grid.png");
  const analyzedBefore = await Promise.all(fids.map(analyzed));
  await ev(`document.querySelector('[data-feed-id=${JSON.stringify(fids[0])}] [data-testid=feed-expand]').click()`);
  await waitFor(() => ev("!!document.querySelector('[data-testid=feeds-back-grid]') && !!document.querySelector('[data-testid=feed-detail]')"), "expanded feed", 8000);
  check("expanding one feed shows only that feed and its own detail (configuration, frame accounting, tracks, events)", (await panelIds()).length === 1 && (await attr("feed-detail", "data-feed-id")) === fids[0]);
  await shot("feed_expanded.png");
  await click("feeds-back-grid");
  await waitFor(async () => (await panelIds()).length === 3, "grid restored", 8000);
  const analyzedAfter = await Promise.all(fids.map(analyzed));
  check("returning to the grid mixes no state: all three feeds kept running and their counters only moved forward", analyzedAfter.every((n, i) => n >= analyzedBefore[i]) && (await Promise.all(fids.map((f) => panelAttr(f, "feed-state", "data-state")))).every((s) => s === "playing"));
  await ev(`document.querySelector('[data-feed-id=${JSON.stringify(fids[2])}] [data-testid=feed-stop]').click()`);
  await waitFor(async () => (await panelAttr(fids[2], "feed-flag", "data-flag")) === "stopped", "feed 3 stopped label", 10000);
  check("stopping one feed labels only that feed as stopped; the others stay live", (await panelAttr(fids[0], "feed-flag", "data-flag")) === "live" && (await panelAttr(fids[1], "feed-flag", "data-flag")) === "live");

  // ======================================================================== overview reflects real state and drills down
  await nav("resumen");
  await waitFor(() => ev("document.querySelectorAll('[data-testid=ov-feed-tile]').length===3"), "overview feed tiles", 15000);
  check("the overview shows the three real sources, genuine and synthetic alerts apart, and the not-proof-of-safety note", (await text("kpi-sources")).includes("3") && !!(await text("kpi-unacked")) && !!(await text("kpi-sim")) && (await text("ov-risk-dist")).length > 0 && (await text("view-resumen")).includes("no demuestra seguridad"));
  await shot("overview_populated.png");
  await ev(`document.querySelector('[data-testid=ov-feed-tile][data-feed-id=${JSON.stringify(fids[1])}]').click()`);
  await waitFor(() => ev("!!document.querySelector('[data-testid=feed-detail]')"), "drill-down to the feed", 8000);
  check("an overview source tile drills down to that exact feed", (await attr("feed-detail", "data-feed-id")) === fids[1]);
  await click("feeds-stop-all");
  await sleep(1500);

  // ======================================================================== privacy defaults
  await nav("camara");
  await waitFor(() => ev("!!document.querySelector('[data-testid=privacy-on]')"), "privacy box");
  check("Privacy is ON by default and the camera is not started by opening the area", (await ev("document.querySelector('[data-testid=privacy-on]').checked")) && (await ev("(window.__streams||[]).length")) === 0);
  await nav("configuracion");
  check("Settings states metadata is not anonymous, lists future integrations as disabled, and the webhook as off", (await text("view-configuracion")).includes("no son anónimos") && (await text("view-configuracion")).includes("No disponible en esta versión") && !!(await text("webhook-status")));

  // ======================================================================== responsive: every area at 1500, 820 and 420 px (overflow, screenshots)
  for (const [w, h, label] of [[1500, 1000, "1500"], [820, 1100, "820"], [420, 900, "420"]]) {
    await send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: w < 600 });
    await sleep(400);
    if (w <= 820) {
      const btnShown = await ev("getComputedStyle(document.querySelector('.menu-btn')).display!=='none'");
      await ev("document.querySelector('.menu-btn').click()"); await sleep(300);
      const opened = await ev("document.getElementById('sidebar').classList.contains('open')");
      await key("Escape"); await sleep(300);
      const closed = await ev("!document.getElementById('sidebar').classList.contains('open')");
      check(`at ${w}px the navigation collapses behind a menu button that opens and closes (Escape)`, btnShown && opened && closed, JSON.stringify({ btnShown, opened, closed }));
    } else if (w < 1200) {
      check(`at ${w}px the sidebar is a compact icon rail`, (await ev("document.querySelector('.sidebar').getBoundingClientRect().width")) < 100);
    }
    for (const [a, view] of AREAS) {
      await nav(a);
      await waitFor(() => ev(`!!document.querySelector('[data-testid=${view}]') && getComputedStyle(document.querySelector('[data-testid=${view}]').closest('div[style*="display: none"]')||document.body).display!=='none'`), view, 15000).catch(() => undefined);
      await sleep(350);
      const over = await overflow();
      const offenders = over > 1 ? await ev("[...document.querySelectorAll('body *')].filter(e=>e.getBoundingClientRect().right>window.innerWidth+1 && getComputedStyle(e).position!=='fixed').slice(0,5).map(e=>e.tagName+'.'+(e.className&&e.className.baseVal===undefined?e.className:'')).join(' | ')") : "";
      check(`${a}: no horizontal page overflow at ${w}px`, over <= 1, `overflow ${over}px ${offenders}`);
      fs.mkdirSync(path.join(OUT, "shots", label), { recursive: true });
      await shot(path.join("shots", label, `${a}.png`));
    }
  }

  // ======================================================================== accessibility sweep + no outbound traffic
  await send("Emulation.setDeviceMetricsOverride", { width: 1500, height: 1000, deviceScaleFactor: 1, mobile: false });
  for (const [a] of AREAS) {
    await nav(a);
    await sleep(300);
    const bad = await ev("[...document.querySelectorAll('button, input:not([type=hidden]), select, textarea, a[href]')].filter(e=>e.offsetParent!==null || getComputedStyle(e).position==='fixed').filter(e=>{const n=(e.getAttribute('aria-label')||e.getAttribute('title')||e.textContent||'').trim(); const lab=e.labels&&e.labels.length; const wrapped=e.closest('label'); return !n && !lab && !wrapped && e.tagName!=='A';}).slice(0,6).map(e=>e.tagName+'['+(e.getAttribute('data-testid')||e.type||'')+']')");
    check(`${a}: every visible control has an accessible name`, bad.length === 0, JSON.stringify(bad));
  }
  const foreign = await ev("performance.getEntriesByType('resource').map(r=>new URL(r.name).origin).filter(o=>o!==location.origin)");
  check("no request left this machine's local server origin during the session", foreign.length === 0, JSON.stringify([...new Set(foreign)]));
  const errs = await ev("window.__mgErrors||[]");
  check("no uncaught page errors were recorded", errs.length === 0, JSON.stringify(errs.slice(0, 3)));
} catch (e) {
  check("console acceptance script ran to completion", false, e.message + " @ " + String(e.stack).split("\n").slice(1, 3).join(" | "));
} finally {
  const failed = results.filter((r) => !r.ok).length;
  fs.writeFileSync(path.join(OUT, "results.json"), JSON.stringify({ base: BASE, when: new Date().toISOString(), results }, null, 1));
  console.log(`\n${results.length - failed}/${results.length} checks passed`);
  try { ws?.close(); } catch { /* ignore */ }
  chrome.kill("SIGTERM");
  await sleep(500);
  process.exit(failed ? 1 : 0);
}
