import shutil, subprocess, sys, pathlib
"""Mutation check of the unified additions (explain.py, providers.py, costgov.py). Usage: python scripts/mutation_check_unified.py <empty scratch dir>.
Works on PRIVATE copies; never edits the worktree. The UNMUTATED copy must pass first (otherwise every failure would look like a kill). A survivor, an invalid or an unapplied mutant exits nonzero."""
ROOT = pathlib.Path(__file__).resolve().parents[1]
WORK = pathlib.Path(sys.argv[1]) / "mut"
PY = str(ROOT / ".venv/bin/python")
TESTS = ["test_unified_explain_unit.py", "test_costgov_base.py", "test_costgov_hardening.py"]
M = [
 ("E01 explanation may change the state", "explain.py", "    if rep.deterministic_state != st:\n        codes.append(\"state_changed\")\n", ""),
 ("E02 explanation may name people/vehicles", "explain.py", "    if any(_ENTITY.search(t) for t in free):\n        codes.append(\"unsupported_entity_claim\")", "    if False:\n        codes.append(\"unsupported_entity_claim\")"),
 ("E03 collision claims allowed", "explain.py", "    if _COLLISION.search(blob):\n        codes.append(\"collision_claim\")\n", ""),
 ("E04 'video inspected' claims allowed", "explain.py", "    if _VIDEO.search(blob):\n        codes.append(\"video_inspection_claim\")\n", ""),
 ("E05 invented numbers allowed", "explain.py", "if any(n not in allowed for n in _NUM.findall(blob)):", "if False:"),
 ("E06 action language allowed", "explain.py", "    if _ACTION.search(\"\\n\".join(texts)):\n        codes.append(\"action_language\")\n", ""),
 ("E07 fact text not bound to the incident", "explain.py", "codes.append(\"fact_text_not_bound_to_incident\")", "pass"),
 ("E08 provenance of the response not checked", "explain.py", "codes.append(\"provenance_mismatch\")", "pass"),
 ("E09 scripted incident labelled as a local-model analysis", "explain.py", "\"synthetic\": \"simulacion con guion (sintetica, no es inferencia real)\"", "\"synthetic\": \"analisis local del modelo sobre un archivo de video registrado\""),
 ("E14 webcam incident described as a registered video file", "explain.py", "\"webcam_model_inference\": \"analisis local del modelo sobre una camara declarada por el navegador (origen no verificado)\"", "\"webcam_model_inference\": \"analisis local del modelo sobre un archivo de video registrado\""),
 ("E15 'video was inspected' claim with the noun first (passive/participle order) allowed", "explain.py", "|\\b{_VIDEO_NOUN}\\b[^.]{{0,40}}\\b{_VIDEO_VERB}\\b\"", "\""),
 ("E16 closed narrative templates not enforced", "explain.py", "codes.append(\"narrative_not_template\")", "pass"),
 ("E17 uncertainties may carry free narrative", "explain.py", "or list(rep.incertidumbres) != expected_uncertainties(incident)", "or False"),
 ("E18 human-review steps may carry free narrative", "explain.py", "or list(rep.pasos_revision_humana) != STEPS", "or False"),
 ("E19 restored record labelled as a live local-model analysis", "explain.py", "\"restored\": \"registro restaurado del almacen local (historia, no en vivo)\"", "\"restored\": \"analisis local del modelo sobre un archivo de video registrado\""),
 ("E20 repeated-source record labelled as a registered video file", "explain.py", "\"repeated_source_simulation\": \"analisis local del modelo sobre un archivo repetido (no son dos camaras)\"", "\"repeated_source_simulation\": \"analisis local del modelo sobre un archivo de video registrado\""),
 ("E22 uncertainties accept any known template sentence regardless of the incident", "explain.py", "or list(rep.incertidumbres) != expected_uncertainties(incident)", "or any(u not in TEMPLATE_SENTENCES for u in rep.incertidumbres)"),
 ("E21 decision sentence may be free narrative", "explain.py", "or rep.explicacion_decision != expected_decision(incident)", "or False"),
 ("C06 absent ledger accepts a non-initial head hash", "costgov.py", "if head is not None and head != (0, \"0\" * 64):", "if head is not None and head[0] > 0:"),
 ("C07 absent ledger with a genuine head accepted", "costgov.py", "if head is not None and head != (0, \"0\" * 64):", "if False:"),
 ("C05 head marker one-behind accepted with any hash", "costgov.py", "if h != (hashes[n - 1] if n >= 1 else \"0\" * 64):", "if False:"),
 ("E10 the view shows GREEN instead of the incident severity", "explain.py", "\"zone_id\": str(ident[\"zone_id\"]), \"final_state\": sev,", "\"zone_id\": str(ident[\"zone_id\"]), \"final_state\": \"GREEN\","),
 ("E11 output says a model decided", "explain.py", "\"decided_by\": \"deterministic_policy\"", "\"decided_by\": \"model\""),
 ("E12 a UNKNOWN-to-green narrative is not detected", "explain.py", "    if st in (\"UNKNOWN\", \"YELLOW\", \"RED\") and _OK_WORDS.search(blob):\n        codes.append(\"risk_downplayed\")\n", ""),
 ("E13 missing lifecycle not declared", "explain.py", "*([] if inc[\"lifecycle_recorded\"] else [NO_LIFE_SENTENCE])", "*[]"),
 ("P01 zero-charge guarantee claimed", "providers.py", "\"zero_charge_guaranteed\": False", "\"zero_charge_guaranteed\": True"),
 ("P02 UI said to be able to enable spending", "providers.py", "\"ui_can_enable_spending\": False", "\"ui_can_enable_spending\": True"),
 ("P03 local model always ready", "providers.py", "states = [\"LOCAL_READY\"] if verified is True else ([\"LOCAL_UNAVAILABLE\"] if verified is False else [\"ERROR\"])", "states = [\"LOCAL_READY\"]"),
 ("P04 credentials reported as read", "providers.py", "\"credentials_read\": False", "\"credentials_read\": True"),
 ("P05 provider status consults the environment", "providers.py", "SpendGovernor(GovernorConfig())", "SpendGovernor(GovernorConfig.from_env())"),
 ("P06 external provider reported as able to make a call", "providers.py", "\"external_call_possible\": False, \"note_id\": note}", "\"external_call_possible\": True, \"note_id\": note}"),
 ("P07 billing state dropped for external providers", "providers.py", "\"BILLING_NOT_APPROVED\", *extra]", "*extra]"),
 ("P08 Muse reported as a real model", "providers.py", "\"states\": [\"SIMULATED\"]", "\"states\": [\"LOCAL_READY\"]"),
 ("C01 live flag check removed", "costgov.py", "        if not c.live_provider_enabled:\n            raise Denied(\"live_provider_disabled\")\n", ""),
 ("C02 request budget off-by-one", "costgov.py", "if len(self._res) >= c.max_real_requests:", "if len(self._res) > c.max_real_requests:"),
 ("C03 paid fallback check removed", "costgov.py", "if request.get(\"tier\") == \"paid_fallback\" and not c.paid_fallback_enabled:", "if False:"),
 ("C04 request not copied (TOCTOU)", "costgov.py", "        request = copy.deepcopy(request)   # what is authorized is what is sent: later mutation of the caller's object cannot reach the transport\n", ""),
]
def prepare():
    if WORK.exists(): shutil.rmtree(WORK)
    (WORK / "backend" / "tests").mkdir(parents=True)
    shutil.copytree(ROOT / "backend" / "mineguard", WORK / "backend" / "mineguard", ignore=shutil.ignore_patterns("__pycache__"))
    for t in ["conftest.py", *TESTS]: shutil.copy(ROOT / "backend" / "tests" / t, WORK / "backend" / "tests" / t)
    (WORK / "models").mkdir(); shutil.copy(ROOT / "models" / "MANIFEST.json", WORK / "models" / "MANIFEST.json")
    (WORK / "pytest.ini").write_text("[pytest]\npythonpath = backend\ntestpaths = backend/tests\nfilterwarnings =\n    ignore::FutureWarning\n")
def run_tests():
    r = subprocess.run([PY, "-m", "pytest", "backend/tests", "-q", "-x", "-p", "no:cacheprovider", "--rootdir", str(WORK)], cwd=WORK, capture_output=True, text=True,
                       env={"PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin"}, timeout=400)
    return r, r.stdout + r.stderr
prepare()
base, base_out = run_tests()
if base.returncode != 0:
    print("BASELINE_FAILED: the unmutated scratch copy does not pass; no mutant result is meaningful\n" + base_out[-1500:]); sys.exit(2)
print("baseline passed:", base_out.strip().splitlines()[-1])
out = []
for name, f, old, new in M:
    prepare()
    p = WORK / "backend" / "mineguard" / f
    t = p.read_text()
    if t.count(old) != 1:
        out.append((name, "MUTATION_NOT_APPLIED")); continue
    p.write_text(t.replace(old, new, 1))
    r, txt = run_tests()
    bad = any(k in txt for k in ("SyntaxError", "IndentationError", "ImportError", "ModuleNotFoundError", "NameError", "error during collection"))
    out.append((name, "INVALID_MUTANT" if bad else ("KILLED" if r.returncode != 0 else "SURVIVED")))
for n, s in out: print(f"{s:22} {n}")
print("killed", sum(s == "KILLED" for _, s in out), "of", len(out))
sys.exit(0 if all(s == "KILLED" for _, s in out) else 1)
