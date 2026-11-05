"""The gate's approved start conditions are evaluated by a pure function: limits are fixed, an unmeasured value is never a pass, and every violated condition is reported."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("gate_preconditions", Path(__file__).resolve().parents[2] / "scripts" / "gate_preconditions.py")
gp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gp)

GOOD = {"pressure_level": 1, "swap_used_mb": 200.0, "free_percent": 62, "disk_free_gb": 90, "busy_ports": [], "mineguard_server_running": False, "git_clean": True}


def failing(m):
    return [r["condition"] for r in gp.evaluate(m) if not r["ok"]]


def test_all_conditions_pass_only_when_every_one_does():
    assert failing(GOOD) == []


def test_each_condition_can_block_on_its_own():
    cases = {"kernel memory pressure level": {"pressure_level": 2}, "system swap used (MB)": {"swap_used_mb": 1024.0}, "system free memory (%)": {"free_percent": 59},
             "free disk (GB)": {"disk_free_gb": 14}, "test ports in use": {"busy_ports": [8000]}, "a MineGuard server is running": {"mineguard_server_running": True}, "git working tree clean": {"git_clean": False}}
    for name, edit in cases.items():
        assert failing({**GOOD, **edit}) == [name], name


def test_a_missing_measurement_is_never_a_pass():
    for k in ("pressure_level", "swap_used_mb", "free_percent", "disk_free_gb"):
        assert len(failing({**GOOD, k: None})) == 1


def test_the_boundaries_are_the_approved_ones_and_are_constants():
    assert gp.LIMITS == {"pressure_level": 1, "swap_used_mb_max": 1024, "free_percent_min": 60, "disk_free_gb_min": 15}
    assert failing({**GOOD, "free_percent": 60, "swap_used_mb": 1023.9}) == []
    assert failing({**GOOD, "pressure_level": 4}) == ["kernel memory pressure level"]


def test_a_real_measurement_has_every_field():
    m = gp.measure()
    assert set(m) >= set(GOOD) and len(gp.evaluate(m)) == 7
