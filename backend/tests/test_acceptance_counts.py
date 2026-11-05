"""MG-EVIDENCE-019: the acceptance record contains no count that disagrees with the current test inventory (scripts/acceptance_counts.py check)."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_the_acceptance_record_inventory_matches_the_collected_tests():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "acceptance_counts.py"), "check"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_stale_figure_is_rejected(tmp_path):
    doc = (ROOT / "docs" / "MG-MVP1-ACCEPTANCE-001.md").read_text()
    stale = doc.replace("| privacy | 14 |", "| privacy | 13 |", 1)
    assert stale != doc
    p = tmp_path / "doc.md"
    p.write_text(stale)
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "acceptance_counts.py"), "check", "--doc", str(p)], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 1 and "STALE" in r.stdout


def test_a_hand_typed_count_outside_the_generated_block_is_rejected(tmp_path):
    doc = (ROOT / "docs" / "MG-MVP1-ACCEPTANCE-001.md").read_text()
    p = tmp_path / "doc.md"
    p.write_text(doc + "\nVitest 35 passed and 22 tests in deletion.\n")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "acceptance_counts.py"), "check", "--doc", str(p)], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 1 and "outside the generated block" in r.stdout
