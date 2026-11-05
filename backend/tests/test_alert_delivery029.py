"""MG-ALERT-029 (cycle-29 review): `delivered` is never assumed. With every client channel disabled the alert exists only in the alert center: it is `pending` until the operator view has
DISPLAYED it and `in_app_only` afterwards; `delivered` needs a client channel that actually reported delivery. Synthetic events only."""
import copy

from mineguard.alerts import AlertEngine, valid_alert
from test_vertical2_alerts import Clock, evt, src


def engine(tmp_path, red_browser=False, red_sound=False):
    e = AlertEngine(tmp_path, wall=Clock(), mono=Clock())
    s = copy.deepcopy(e.settings)
    s["red"].update(browser=red_browser, sound=red_sound)
    e.set_settings(s)
    return e


def test_review_counterexample_all_client_channels_disabled_is_not_delivered(tmp_path):
    e = engine(tmp_path)
    e.observe_source(src(), [evt("evt_1", "RED")], {})
    (a,) = e.alerts.values()
    assert a["delivery"]["in_app"]["status"] == "stored" and a["delivery"]["browser"]["status"] == "disabled" and a["delivery"]["sound"]["status"] == "disabled"
    assert a["delivery_state"] == "pending" and e.public(a)["delivery_state"] == "pending"          # stored, never shown to anyone: not «Entregada»
    e.mark_seen([a["alert_id"]])                                                                       # the operator view displays it
    assert a["delivery"]["in_app"]["status"] == "displayed" and a["delivery_state"] == "in_app_only"
    assert valid_alert(a)


def test_unknown_diagnostics_under_the_default_policy_are_not_delivered_either(tmp_path):
    e = AlertEngine(tmp_path, wall=Clock(), mono=Clock())
    e.observe_source(src(), [], {"Z1": {"state": "UNKNOWN", "pending": [], "causes": ["source_gap"], "t_us": 1}}, final=False)
    for a in e.alerts.values():
        assert a["delivery"]["browser"]["status"] == "disabled" and a["delivery_state"] != "delivered"


def test_delivered_needs_a_client_channel_that_reported_delivery(tmp_path):
    e = engine(tmp_path, red_browser=True)
    e.observe_source(src(), [evt("evt_1", "RED")], {})
    (a,) = e.alerts.values()
    assert a["delivery_state"] == "pending"                                                           # browser enabled but not yet reported
    e.report_delivery(a["alert_id"], "browser", "delivered")
    assert a["delivery_state"] == "delivered"
    e.mark_seen([a["alert_id"]])
    assert a["delivery_state"] == "delivered"                                                         # displaying never downgrades a real delivery


def test_denied_failed_and_unsupported_keep_their_earlier_meaning(tmp_path):
    e = engine(tmp_path, red_browser=True, red_sound=True)
    e.observe_source(src(), [evt("evt_1", "RED")], {})
    (a,) = e.alerts.values()
    e.report_delivery(a["alert_id"], "browser", "denied", "Notification.permission = denied")
    e.report_delivery(a["alert_id"], "sound", "delivered")
    assert a["delivery_state"] == "delivered"                                                         # a real delivery is reported (MG-ALERT-030; earlier this test expected «in_app_only»)
    f = engine(tmp_path / "f", red_browser=True, red_sound=True)
    f.observe_source(src(), [evt("evt_2", "RED")], {})
    (b,) = f.alerts.values()
    f.report_delivery(b["alert_id"], "browser", "delivered")
    f.report_delivery(b["alert_id"], "sound", "failed", "audio blocked")
    assert b["delivery_state"] == "failed"


# ---------------------------------------------------------------- MG-ALERT-030 (cycle-30 review): the aggregate is computed from ALL outcomes
def test_review_counterexample_denied_client_channel_before_display_is_not_in_app_only(tmp_path):
    e = engine(tmp_path, red_browser=True, red_sound=False)
    e.observe_source(src(), [evt("evt_1", "RED")], {})
    (a,) = e.alerts.values()
    e.report_delivery(a["alert_id"], "browser", "denied", "Notification.permission = denied")
    assert a["delivery"]["in_app"]["status"] == "stored" and a["delivery_state"] == "pending"          # nobody has seen it yet
    e.mark_seen([a["alert_id"]])
    assert a["delivery_state"] == "in_app_only"


def test_review_counterexample_a_real_client_delivery_is_not_hidden_by_a_denied_channel(tmp_path):
    e = engine(tmp_path, red_browser=True, red_sound=True)
    e.observe_source(src(), [evt("evt_1", "RED")], {})
    (a,) = e.alerts.values()
    e.report_delivery(a["alert_id"], "browser", "denied", "Notification.permission = denied")
    assert a["delivery_state"] == "pending"                                                           # sound still pending
    e.report_delivery(a["alert_id"], "sound", "delivered")
    assert a["delivery_state"] == "delivered"
