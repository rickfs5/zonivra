"""MG-DOC-026: version statements in the documentation are checked against the code, and the console doc does not contradict the build/OAF behaviour it describes."""
import re
from pathlib import Path

from mineguard.risk import evaluation, tracker

DOCS = Path(__file__).resolve().parents[2] / "docs"


def test_every_documented_current_tracker_version_equals_the_code():
    ref = (DOCS / "MG-RISK-002-kernel-reference.md").read_text()
    assert re.findall(r"CURRENT tracker version is `(\d+\.\d+\.\d+)`", ref) == [tracker.TRACKER_VERSION]
    for name in ("MG-PRODUCT-001-operating-guide.md", "MG-VERTICAL2-001-multi-source-monitoring.md", "MG-ACCEPTANCE-CHECKLIST-001.md"):
        found = re.findall(r"(?:IoU )?tracker,? `?(\d+\.\d+\.\d+)`?", (DOCS / name).read_text())
        assert found and found[0] == tracker.TRACKER_VERSION, (name, found)      # the first statement is the current one; later mentions may be historical (e.g. «made by tracker 0.2.0»)


def test_the_documented_kernel_version_equals_the_code():
    text = (DOCS / "MG-RISK-002-kernel-reference.md").read_text()
    assert evaluation.ENGINE_VERSION in text


def test_the_console_doc_does_not_deny_what_the_build_endpoint_does():
    text = (DOCS / "MG-CONSOLE-001-operations-console.md").read_text()
    assert "the server does not record it" not in text and "OAF state is not read by the app" not in text
    assert "/api/build" in text and "strict validation" in text
