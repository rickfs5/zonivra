// MG-EVIDENCE-10: browser acceptance of the EXTERNAL SAM result import and review, English and Spanish, over an ISOLATED server (empty storage).
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
const PORT = 9373;
const profile = fs.mkdtempSync(path.join(os.tmpdir(), "mg-ext10-"));
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

const FIX = path.resolve(process.argv[4] ?? "");
const runsList = () => api("/api/runs");
const apij = async (p) => JSON.parse((await api(p)).body);
const panelText = () => ev("document.querySelector('[data-testid=external-results]')?.innerText||''");
const setFile = async (tid, file) => {
  const { root } = await send("DOM.getDocument", { depth: 0 });
  const { nodeId } = await send("DOM.querySelector", { nodeId: root.nodeId, selector: `[data-testid=${tid}]` });
  await send("DOM.setFileInputFiles", { files: [file], nodeId });
};
const toConfig = async () => { await nav("configuracion"); await waitFor(() => ev("!!document.querySelector('[data-testid=external-results]')"), "external panel"); await sleep(300); };
try {
  await connect();
  await send("Page.enable"); await send("DOM.enable");
  await send("Page.addScriptToEvaluateOnNewDocument", { source: "window.__mgErrors=[];window.addEventListener('error',e=>window.__mgErrors.push(String(e.message)));window.addEventListener('unhandledrejection',e=>window.__mgErrors.push(String(e.reason)));" });
  await send("Emulation.setDeviceMetricsOverride", { width: 1500, height: 1300, deviceScaleFactor: 1, mobile: false });

  await goto("/?lang=en");
  const runs0 = JSON.stringify(await runsList());
  await toConfig();
  check("X1 the panel exists in Settings and the four-way evidence map is shown", (await ev("document.querySelectorAll('[data-testid^=external-map-]').length")) === 4, await text("external-map"));
  check("X2 nothing is imported yet: empty-state message, no review", (await ev("!!document.querySelector('[data-testid=external-none]') && !document.querySelector('[data-testid=external-review]')")) === true);
  check("X3 the pending live integration is stated as pending / disabled / no genuine result yet", /Pending.*disabled and unverified.*no genuine API result/s.test(await text("external-map-pending")), await text("external-map-pending"));

  await click("external-demo-btn");
  await waitFor(() => ev("!!document.querySelector('[data-testid=external-review]')"), "demo review");
  const det = (await apij("/api/external-results")).results[0];
  const rec = await apij(`/api/external-results/${det.import_id}`);
  check("X4 the review is labelled EXTERNAL_MANUAL_PROVIDER_RESULT", (await text("external-origin")).includes("EXTERNAL_MANUAL_PROVIDER_RESULT") && (await attr("external-review", "data-origin")) === "EXTERNAL_MANUAL_PROVIDER_RESULT");
  check("X5 external, synthetic and no-risk banners are all visible (English)", /EXTERNAL MANUAL PROVIDER RESULT.*not live in-app inference/.test(await text("external-banner-external")) && /SYNTHETIC FIXTURE.*NOT produced by any Meta model/.test(await text("external-banner-synthetic")) && /No risk assessment/.test(await text("external-banner-no_risk")));
  check("X6 provider, model, verification state are explicit", (await text("external-provider")) === "meta_sam31" && (await text("external-model")) === "sam-3.1" && (await text("external-state")) === "SYNTHETIC FIXTURE", `${await text("external-provider")}|${await text("external-model")}|${await text("external-state")}`);
  check("X7 input digest and response digest shown equal the server's recomputed digests", (await text("external-input-digest")) === rec.verification.input_image_sha256 && (await text("external-response-digest")) === rec.verification.result_sha256 && /^[0-9a-f]{64}$/.test(await text("external-response-digest")));
  check("X8 image linkage is verified against the image bytes", /verified against the image bytes/.test(await text("external-linkage")), await text("external-linkage"));
  check("X9 two segments are listed and EVERY class cell says UNCLASSIFIED", (await ev("document.querySelectorAll('[data-testid^=external-seg-]').length")) === 2 && (await text("external-class-1")) === "UNCLASSIFIED" && (await text("external-class-2")) === "UNCLASSIFIED");
  check("X10 the prompt concept is labelled as requested, not as a detection", /Requested concept \(the prompt, not a detection\)/i.test(await panelText()) && (await text("external-concept")) === "truck");
  check("X11 masks are declared not rendered (raster format undocumented)", /not rendered because the raster format is undocumented/.test(await text("external-segments")));
  check("X12 verified boxes are drawn over the stored image (image decoded, 2 boxes)", (await ev("document.querySelectorAll('[data-testid^=external-box-]').length")) === 2 && (await ev("(()=>{const i=document.querySelector('[data-testid=external-overlay] image');return !!i && i.getAttribute('href').includes('/image')})()")) === true);
  const imgOk = await ev(`(async()=>{const tok=document.querySelector('meta[name=mg-session-token]').content;const r=await fetch(${JSON.stringify("/api/external-results/" + det.import_id + "/image")},{headers:{'X-MG-Token':tok}});const b=new Uint8Array(await r.arrayBuffer());const d=await crypto.subtle.digest('SHA-256',b);return [...new Uint8Array(d)].map(x=>x.toString(16).padStart(2,'0')).join('')})()`);
  check("X13 the served image hashes to the manifest's input digest", imgOk === rec.verification.input_image_sha256);
  const panelNow = await panelText();
  check("X14 no risk level word appears anywhere in the external panel (RED/YELLOW/GREEN)", !/\b(RED|YELLOW|GREEN|ROJO|AMARILLO|VERDE)\b/.test(panelNow), panelNow.match(/\b(RED|YELLOW|GREEN)\b/)?.[0] ?? "");
  check("X15 no risk chip, badge or alert element exists inside the panel", (await ev("document.querySelectorAll('[data-testid=external-results] [class*=risk], [data-testid=external-results] [class*=badge], [data-testid=external-results] [class*=sev]').length")) === 0);
  await ev("document.querySelector('[data-testid=external-review]').scrollIntoView()"); await sleep(200); await shot("x_en_review.png");

  // ---- the real UI import path: tampered and truncated inputs are rejected and nothing is stored
  const listBefore = JSON.stringify(await apij("/api/external-results"));
  const tryImport = async (manifest, result, image) => {
    await ev("(()=>{const d=document.querySelector('[data-testid=external-import-form]');d.open=true})()");
    await setFile("external-file-manifest", path.join(FIX, manifest)); await setFile("external-file-result", path.join(FIX, result));
    if (image) await setFile("external-file-image", path.join(FIX, image));
    await sleep(200); await click("external-import-btn"); await waitFor(() => ev("!!document.querySelector('[data-testid=external-note]')"), "import note"); await sleep(400);
    return text("external-note");
  };
  const n1 = await tryImport("manifest.json", "tampered.sse");
  check("X16 a tampered result (hash mismatch) is rejected with an English reason", /Rejected, nothing stored: .*SHA-256 of the saved result does not match/.test(n1), n1);
  const n2 = await tryImport("manifest_truncated.json", "truncated.sse");
  check("X17 a truncated stream (hash consistent) is rejected as cut off", /Rejected, nothing stored: .*cut off/.test(n2), n2);
  const n3 = await tryImport("manifest_badmodel.json", "result.sse");
  check("X18 a wrong model identity is rejected", /Rejected, nothing stored: .*meta_sam31 and model sam-3\.1/.test(n3), n3);
  const n4 = await tryImport("manifest_declared_real.json", "result.sse");
  check("X19 bundled synthetic bytes cannot be relabelled as a provider result", /Rejected, nothing stored: .*bundled synthetic fixture/.test(n4), n4);
  check("X20 none of the rejected imports changed the stored list", JSON.stringify(await apij("/api/external-results")) === listBefore);
  const n5 = await tryImport("manifest.json", "result.sse", "image.png");
  check("X21 importing the genuine-format SYNTHETIC bytes via the file form reports 'already imported' (idempotent, nothing written)", /Already imported/.test(n5), n5);

  // ---- Spanish
  await goto("/?lang=es"); await toConfig();
  await click(`external-open-${det.import_id}`); await waitFor(() => ev("!!document.querySelector('[data-testid=external-review]')"), "es review");
  check("X22 Spanish: banners say external manual provider result / synthetic fixture / no risk", /RESULTADO MANUAL EXTERNO DE PROVEEDOR/.test(await text("external-banner-external")) && /FIXTURE SINTÉTICO/.test(await text("external-banner-synthetic")) && /No se hizo ninguna evaluación de riesgo/.test(await text("external-banner-no_risk")));
  check("X23 Spanish: state, linkage and class labels", (await text("external-state")) === "FIXTURE SINTÉTICO" && /verificado contra los bytes de la imagen/.test(await text("external-linkage")) && (await text("external-class-1")) === "SIN CLASIFICAR");
  check("X24 Spanish: digests and origin are language-independent (identical)", (await text("external-response-digest")) === rec.verification.result_sha256 && (await text("external-origin")).includes("EXTERNAL_MANUAL_PROVIDER_RESULT"));
  check("X25 Spanish: refusal reasons are Spanish", /Rechazado, no se guardó nada: .*no coincide con el manifiesto/.test(await tryImport("manifest.json", "tampered.sse")));
  await ev("document.querySelector('[data-testid=external-review]').scrollIntoView()"); await sleep(200); await shot("x_es_review.png");

  // ---- isolation and health
  check("X26 canonical runs listing is byte-identical before and after (RF-DETR runs untouched)", JSON.stringify(await runsList()) === runs0);
  check("X27 the provider status is unchanged: Meta still disabled, no external call possible", await ev(`(async()=>{const tok=document.querySelector('meta[name=mg-session-token]').content;const d=await (await fetch('/api/providers',{headers:{'X-MG-Token':tok}})).json();return d.invariants.external_calls_possible===false && d.providers.some(p=>p.states.includes('META_DISABLED'))})()`));
  check("X28 no script errors in the page", (await ev("window.__mgErrors.length")) === 0, await ev("JSON.stringify(window.__mgErrors)"));
} catch (e) {
  check("script completed without an exception", false, String(e.message).slice(0, 300));
} finally {
  const fail = results.filter((r) => !r.ok);
  fs.writeFileSync(path.join(OUT, "external_import_acceptance.result.json"), JSON.stringify({ base: BASE, pass: results.length - fail.length, fail: fail.length, results, notes }, null, 1));
  console.log(`\n${results.length - fail.length} PASS, ${fail.length} FAIL`);
  try { await send("Browser.close"); } catch { /* closing */ }
  try { ws.close(); } catch { /* closed */ }
  chrome.kill("SIGTERM"); await sleep(800);
  try { fs.rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 300 }); } catch { /* temp profile left in os tmp */ }
  process.exit(fail.length ? 1 : 0);
}
