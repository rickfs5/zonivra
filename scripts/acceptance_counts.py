#!/usr/bin/env python3
"""Generates / validates the count-bearing part of docs/MG-MVP1-ACCEPTANCE-001.md (cycle-20 MG-EVIDENCE-019).
No count in the acceptance record is typed by hand: module totals come from a REAL `pytest --collect-only` of the current tree, executed results come from an evidence transcript
(run_cycle_tests.py output) and browser results from the E2E transcripts. `check` fails when the document and the current inventory disagree, so an obsolete figure cannot survive.
Usage:
  acceptance_counts.py generate [--evidence TEST_EVIDENCE.txt] [--e2e label=path ...] [--doc DOC]
  acceptance_counts.py check [--doc DOC]
It never claims that the full gate passed: the gate line is taken from an ACCEPTANCE_GATE= marker in the evidence, otherwise it says NOT RUN."""
import argparse
import hashlib
import json
import re
import time
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "MG-MVP1-ACCEPTANCE-001.md"
BEGIN, END = "<!-- GENERATED:TEST_INVENTORY BEGIN (scripts/acceptance_counts.py; do not edit by hand) -->", "<!-- GENERATED:TEST_INVENTORY END -->"
PY = ROOT / ".venv" / "bin" / "python"


def collected() -> dict[str, int]:
    out = subprocess.run([str(PY), "-m", "pytest", "backend/tests", "--collect-only", "-q", "-p", "no:cacheprovider"], cwd=ROOT, capture_output=True, text=True).stdout
    counts: dict[str, int] = {}
    for line in out.splitlines():
        if "::" in line:
            m = Path(line.split("::")[0]).stem
            counts[m] = counts.get(m, 0) + 1
    return dict(sorted(counts.items()))


def parse_evidence(path: Path) -> dict:
    txt = path.read_text()
    head = re.search(r"Commit under test: ([0-9a-f]{40})", txt)
    rows, cur = {}, None
    for line in txt.splitlines():
        if line.startswith("## "):
            cur = line[3:].strip()
            rows[cur] = {"exit": None, "passed": None}
        elif cur and line.startswith("exit code:"):
            rows[cur]["exit"] = int(line.split()[2])
        elif cur and "|" in line:
            m = re.search(r"(\d+) passed", line) or re.search(r"Tests\s+(\d+) passed", line)
            if m:
                rows[cur]["passed"] = int(m.group(1))
    gate = re.search(r"ACCEPTANCE_GATE=(\w+)", txt)
    return {"commit": head.group(1) if head else None, "rows": rows, "gate": gate.group(1) if gate else None,
            "all_zero": "ALL_EXIT_CODES_ZERO=yes" in txt, "sha": hashlib.sha256(path.read_bytes()).hexdigest()[:16]}


def parse_e2e(path: Path) -> dict:
    t = path.read_text()
    return {"pass": len(re.findall(r"^PASS", t, re.M)), "fail": len(re.findall(r"^FAIL", t, re.M)), "exit0": "MVP_E2E_EXIT=0" in t, "sha": hashlib.sha256(path.read_bytes()).hexdigest()[:16]}


def render(col: dict[str, int], ev: dict | None, e2e: dict[str, dict], ev_name: str) -> str:
    L = [BEGIN, "", "Module inventory = `pytest --collect-only` of the tree this block was generated from. Executed results = the named evidence transcript (git-ignored, identified by hash).", ""]
    L += ["| Module | Collected | Executed passed | Exit |", "|---|---:|---:|---:|"]
    mism = []
    for m, n in col.items():
        row = (ev or {}).get("rows", {}).get("be_" + m.removeprefix("test_"))
        if row is None:
            L.append(f"| {m.removeprefix('test_')} | {n} | not executed | n/a |")
        else:
            L.append(f"| {m.removeprefix('test_')} | {n} | {row['passed']} | {row['exit']} |")
            if row["passed"] != n:
                mism.append(m)
    L.append(f"| **Total ({len(col)} modules)** | **{sum(col.values())}** | **{sum((v['passed'] or 0) for k, v in (ev or {}).get('rows', {}).items() if k.startswith('be_'))}** | |")
    L.append("")
    if ev:
        fe = ev["rows"].get("fe_vitest", {})
        L.append(f"Evidence: `{ev_name}` (sha256 prefix `{ev['sha']}`), commit under test `{ev['commit']}`; all exit codes zero: **{'yes' if ev['all_zero'] else 'NO'}**; Vitest passed: **{fe.get('passed')}** (exit {fe.get('exit')}); typecheck+build exit: **{ev['rows'].get('fe_build_typecheck', {}).get('exit')}**.")
        if mism:
            L.append(f"WARNING: executed count differs from the collected count for: {', '.join(mism)}.")
    else:
        L.append("Evidence: none supplied for this generation (inventory only; nothing is claimed as executed).")
    for label, r in sorted(e2e.items()):
        L.append(f"Browser `{label}`: **{r['pass']} PASS / {r['fail']} FAIL**, exit0={'yes' if r['exit0'] else 'NO'} (transcript sha256 prefix `{r['sha']}`).")
    g = (ev or {}).get("gate")
    L.append("")
    L.append(f"Full acceptance gate (`scripts/acceptance_gate.sh`): **{g if g else 'NOT RUN in this evidence'}**. Targeted module runs are never a gate result.")
    L += ["", END]
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["generate", "check"])
    ap.add_argument("--evidence")
    ap.add_argument("--e2e", action="append", default=[])
    ap.add_argument("--doc", default=str(DOC))
    a = ap.parse_args()
    doc = Path(a.doc)
    text = doc.read_text()
    if BEGIN not in text or END not in text:
        print("the document has no generated block markers", file=sys.stderr)
        return 2
    col = collected()
    if a.cmd == "check":
        block = text[text.index(BEGIN):text.index(END) + len(END)]
        listed = {m.group(1): int(m.group(2)) for m in re.finditer(r"^\| (\S+) \| (\d+) \|", block, re.M)}
        want = {m.removeprefix("test_"): n for m, n in col.items()}
        bad = {k: (listed.get(k), v) for k, v in want.items() if listed.get(k) != v} | {k: (v, None) for k, v in listed.items() if k not in want}
        tot = re.search(r"\*\*Total \((\d+) modules\)\*\* \| \*\*(\d+)\*\*", block)
        if bad or not tot or int(tot.group(2)) != sum(col.values()) or int(tot.group(1)) != len(col):
            print("STALE acceptance inventory:", bad, "total in doc:", tot.groups() if tot else None, "current:", (len(col), sum(col.values())))
            return 1
        stray = [l for l in text.replace(block, "").splitlines() if re.search(r"\b\d+ tests?\b|\b\d+/\d+ (?:checks|PASS)|\bVitest \d+", l)]
        if stray:
            print("count-bearing text outside the generated block:", *stray[:5], sep="\n  ")
            return 1
        print(f"acceptance inventory is current: {len(col)} modules, {sum(col.values())} tests")
        return 0
    ev = parse_evidence(Path(a.evidence)) if a.evidence else None
    e2e = {}
    for item in a.e2e:
        label, path = item.split("=", 1)
        e2e[label] = parse_e2e(Path(path))
    block = render(col, ev, e2e, Path(a.evidence).name if a.evidence else "")
    new = text[:text.index(BEGIN)] + block + text[text.index(END) + len(END):]
    doc.write_text(new)
    state = {"schema": "mg.acceptance-state.v1", "commit": (ev or {}).get("commit"), "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
             "gate": (ev or {}).get("gate") or "NOT RUN", "evidence_sha256_prefix": (ev or {}).get("sha"), "all_exit_codes_zero": bool((ev or {}).get("all_zero")),
             "totals": {"collected": sum(col.values()), "modules": len(col), "executed_passed": sum((v["passed"] or 0) for k, v in (ev or {}).get("rows", {}).items() if k.startswith("be_"))},
             "modules": [{"module": m.removeprefix("test_"), "collected": n, "passed": ((ev or {}).get("rows", {}).get("be_" + m.removeprefix("test_")) or {}).get("passed"),
                          "exit": ((ev or {}).get("rows", {}).get("be_" + m.removeprefix("test_")) or {}).get("exit")} for m, n in col.items()],
             "vitest": ((ev or {}).get("rows", {}).get("fe_vitest") or {}).get("passed"), "e2e": {k: {"pass": v["pass"], "fail": v["fail"]} for k, v in e2e.items()},
             "note": "informational copy of the generated block in MG-MVP1-ACCEPTANCE-001; a targeted module run is never a gate result"}
    (doc.parent / "MG-ACCEPTANCE-STATE.json").write_text(json.dumps(state, indent=1) + "\n")
    print(f"generated: {len(col)} modules, {sum(col.values())} tests collected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
