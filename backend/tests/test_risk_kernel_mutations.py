"""Mutation checks for the risk kernel (MG-RISK-001 section 12). Each mutant is applied to a COPY of the kernel in a temporary package and the golden
vectors (test_risk_kernel.py) are run against it in a subprocess; every mutant must make at least one vector fail (be KILLED)."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
PKG = HERE.parent / "mineguard"
# Documented equivalent mutant (not listed): HOLD(NV) returning F instead of U for an unknown NV cannot change any output, because a dwelling person (R2 open needs DWELL=T)
# already makes R1 (person AND vehicle) UNKNOWN whenever the vehicle is unknown.
MUTANTS = {
    "dwell >= becomes >": ("return T if t - tr.run_start >= self.p.dwell_us else F", "return T if t - tr.run_start > self.p.dwell_us else F"),
    "open hold >= becomes >": ("if t - m.cand_start >= m.open_hold:", "if t - m.cand_start > m.open_hold:"),
    "UNKNOWN mapped to GREEN": ("UNKNOWN if (unknown_causes or not analyzed) else GREEN", "GREEN"),
    "F does not end the dwell run (F bridged)": ("            tr.run_start = tr.u_since = None\n            tr.u_causes, tr.lost_run = set(), False\n            return F", "            return F"),
    "hole counted as T": ("            return (U, hole)\n        if tid in s.persons:", "            return (T, None)\n        if tid in s.persons:"),
    "incomplete inputs not recorded": ("    def _note_incomplete(self, ev: dict, t: int, idx: int, causes: list):\n", "    def _note_incomplete(self, ev: dict, t: int, idx: int, causes: list):\n        return\n"),
    "close hold ignored": ("expired = t - m.closing_start >= m.close_hold", "expired = True"),
    "absorption disabled": ("if sus == T and m.closing_start != t and not m.closing_disc and not expired:", "if False:"),
    "silence between samples ignored": ("if s.t_us - self._prev.t_us > self.p.max_sample_gap_us:", "if False:"),
    "sparse samples may confirm the open hold": ("            if o == T and dt is not None and dt > m.open_hold:", "            if False:"),
    "sparse samples may continue the dwell": ("if tr.run_start is not None and dt is not None and dt > self.p.dwell_us:", "if False:"),
    "dwell clock survives a pending bridge across a long spacing": ("if tr.run_start is not None and dt is not None and dt > self.p.dwell_us:", "if tr.run_start is not None and tr.u_since is None and dt is not None and dt > self.p.dwell_us:"),
    "vehicle-free hold in progress is not pending": ("(d == F or (d == T and nv == T and hold == F))", "(d == F)"),
    "NV hold survives a long silence": ("if self._nv_start is None or (dt is not None and dt > self.p.debounce_us):", "if self._nv_start is None:"),
    "ambiguous person creates no pseudo-instance": ("            ut = k_or(s.untracked_person[0], s.person_other[0])", "            ut = s.untracked_person[0]"),
    "discontinuity does not block absorption": ("and not m.closing_disc and not expired:", "and not expired:"),
    "absorption wins over close-hold expiry at the boundary": ("and not m.closing_disc and not expired:", "and not m.closing_disc:"),
    "unobserved prefix is not reported": ("            self._open_unknown_prefix()  # nothing was observed before the first sample: UNKNOWN, never silently absent", "            pass"),
    "empty stream is not reported": ("        if self._prev is None and self._cur is None and end_us > 0:\n            self._open_unknown_prefix()", "        if False:\n            self._open_unknown_prefix()"),
    "R1 dropped from the UNKNOWN determination (rule dropped from GREEN)": ("unknown_causes = sorted({c for (o, _, cs) in inst.values() if o == U for c in cs} | ({hole} if hole else set()))", "unknown_causes = sorted({c for k_, (o, _, cs) in inst.items() if o == U and k_ != (\"R1\",) for c in cs} | ({hole} if hole else set()))"),
    "incomplete ranges never close": ("            if c not in causes:\n                opened.pop(c)[\"to_us\"] = t", "            if False:\n                opened.pop(c)[\"to_us\"] = t"),
    "incomplete ranges are not closed at the episode end": ("        for r in ev.pop(\"_open\", {}).values():\n            r[\"to_us\"] = t", "        for r in ev.pop(\"_open\", {}).values():\n            pass"),
    "event id ignores the input digest": ("[\"mg.event.id.v2\", *self.id_context,", "[\"mg.event.id.v2\", \"\", \"\","),
    "event id ignores the epoch": ("m.rule[1], self.epoch, list(m.key[1:]), idx]", "m.rule[1], 0, list(m.key[1:]), idx]"),
    "event id ignores the key": ("m.rule[1], self.epoch, list(m.key[1:]), idx]", "m.rule[1], self.epoch, [], idx]"),
    "event id ignores the start index": ("m.rule[1], self.epoch, list(m.key[1:]), idx]", "m.rule[1], self.epoch, list(m.key[1:]), 0]"),
    "the closing sample is not recorded as incomplete": ("        for m in self.machines.values():\n            if m.live:\n                self._note_incomplete(m.ev, t, s.index, cs_for(inst, m))\n        # ---- machines", "        # ---- machines"),
    "class-level dwell replaces per-track dwell": ("        nv = k_not(vehicle)\n", "        nv = k_not(vehicle)\n        ct = self.__dict__.setdefault(\"_ct\", _Track())\n        cd = self._dwell(ct, person, None, t, first_t, dt)\n        dwell = {k_: cd for k_ in dwell}\n"),
}


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_mutant_is_killed_by_the_golden_vectors(name, tmp_path):
    old, new = MUTANTS[name]
    src = (PKG / "risk" / "kernel.py").read_text()
    assert src.count(old) == 1, f"mutation anchor not found exactly once: {name}"
    pkg = tmp_path / "mineguard"
    shutil.copytree(PKG, pkg, ignore=shutil.ignore_patterns("__pycache__"))
    (pkg / "risk" / "kernel.py").write_text(src.replace(old, new))
    vec = tmp_path / "test_risk_kernel.py"
    shutil.copy(HERE / "test_risk_kernel.py", vec)
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", "-c", "/dev/null", "--rootdir", str(tmp_path), str(vec)],
                       cwd=tmp_path, env={"PYTHONPATH": str(tmp_path), "PATH": "/usr/bin:/bin"}, capture_output=True, text=True)
    assert r.returncode == 1, f"mutant not killed by a FAILING test (exit {r.returncode}; 2 would be a collection error, 0 a survivor): {name}\n{r.stdout[-400:]}"


def test_unmutated_copy_passes(tmp_path):
    shutil.copytree(PKG, tmp_path / "mineguard", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(HERE / "test_risk_kernel.py", tmp_path / "test_risk_kernel.py")
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-c", "/dev/null", "--rootdir", str(tmp_path), str(tmp_path / "test_risk_kernel.py")],
                       cwd=tmp_path, env={"PYTHONPATH": str(tmp_path), "PATH": "/usr/bin:/bin"}, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-600:]
