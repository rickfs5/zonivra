"""Integrated alert engine (MG-VERTICAL-02, checkpoint C).

Risk event (from the deterministic kernel) -> alert policy -> alert record -> notification dispatcher -> delivery status.
Alerts never decide severity: the severity of an alert is the severity of the kernel event that caused it. An AI observation alone never creates an alert.
UNKNOWN zone state is surfaced as a low-priority DIAGNOSTIC record (visible uncertainty); it is never shown as GREEN and never raised as an emergency.

Channels approved for this increment: in-app alert center (server-recorded), Browser Notification API and local sound (executed by the BROWSER with its own consent; the server only
records the outcome the browser reports). No SMS, WhatsApp, e-mail or any outbound network delivery exists: the webhook adapter below is a documented CONTRACT whose delivery is disabled.

Storm control: (1) an alert is keyed by (source, zone, event) so a repeated observation of the same episode is idempotent; (2) alerts of the same GROUP (source group, zone, severity) inside the
cooldown are MERGED into the open one (counted, no new notification); the group of a browser camera is its device label, so repeated reconnections do not multiply alerts."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from . import overlap, safedir
from .canon import digest, digest16

API_VERSION = "mg.alert-api/1"
ALERT_SCHEMA = "mg.alert.v1"
ALERTS_DIR = "alerts"
MAX_ALERTS = 500            # soft capacity: resolved alerts, then the oldest open DRILLS, make room before anything genuine is touched
HARD_MAX_ALERTS = 1000       # absolute ceiling: beyond it new alerts are NOT stored and the loss is counted and shown (degraded state), never silently discarded
MAX_OPEN_DRILLS = 50         # scripted drills stay open until the operator acts; the oldest are retired beyond this many
MAX_TRACKED_OPEN = 200       # open episodes tracked per alert (beyond it the alert stays open until its source closes)
MAX_OCCURRENCES = 20
STATUSES = ("new", "active", "acknowledged", "resolved")
SEVERITIES = ("RED", "YELLOW", "UNKNOWN")
CLIENT_CHANNELS = ("browser", "sound")
DELIVERY_STATUSES = ("delivered", "denied", "failed", "unsupported")
PROVENANCE = ("real_inference", "repeated_source_simulation", "synthetic_scripted")
WEBHOOK_DELIVERY_ENABLED = False   # a constant on purpose: this increment ships NO outbound delivery of any kind

DEFAULT_SETTINGS = {
    "red": {"browser": True, "sound": True, "cooldown_s": 10},
    "yellow": {"browser": True, "sound": False, "cooldown_s": 60},
    "unknown": {"browser": False, "sound": False, "cooldown_s": 120, "after_s": 10},
    "webhook": {"enabled": False},
}


def validate_settings(s) -> dict:
    """Strict: unknown keys, wrong types and out-of-range values are refused; the webhook can NOT be enabled in this increment."""
    if not isinstance(s, dict) or set(s) != set(DEFAULT_SETTINGS):
        raise ValueError("settings keys")
    out = {}
    for sev, spec in (("red", ("browser", "sound", "cooldown_s")), ("yellow", ("browser", "sound", "cooldown_s")), ("unknown", ("browser", "sound", "cooldown_s", "after_s"))):
        d = s[sev]
        if not isinstance(d, dict) or set(d) != set(spec):
            raise ValueError(f"{sev} keys")
        for k in ("browser", "sound"):
            if not isinstance(d[k], bool):
                raise ValueError(f"{sev}.{k}")
        for k in ("cooldown_s", "after_s"):
            if k in d and (isinstance(d[k], bool) or not isinstance(d[k], int) or not 0 <= d[k] <= 3600):
                raise ValueError(f"{sev}.{k}")
        out[sev] = dict(d)
    if s["webhook"] != {"enabled": False}:
        raise PermissionError("webhook delivery is disabled in this increment")
    out["webhook"] = {"enabled": False}
    return out


# ------------------------------------------------------------------ future webhook adapter: CONTRACT ONLY (delivery disabled)
def webhook_contract() -> dict:
    return {"status": "DISABLED in this increment: no outbound delivery exists", "adapter_interface": "deliver(alert_event_v1: dict, destination: Destination) -> DeliveryOutcome",
            "payload": "mg.alert.v1 minus provenance internals, JSON, UTF-8, at most 16 KiB", "destination_rules": [
                "explicit allowlist of exact hostnames; wildcards are not accepted", "https only (http only for an allowlisted loopback destination)", "no credentials in the URL; secrets live in a user-managed store and are redacted everywhere else",
                "literal private, loopback, link-local, multicast, reserved and unspecified IP addresses are refused; DNS names must be resolved ONCE and the connection pinned to the checked address (DNS-rebinding)",
                "no redirects are followed", "connect and read timeouts, at most 3 attempts with exponential backoff and a hard total deadline", "a per-destination rate limit and a circuit breaker"],
            "provider_limits": "Telegram bot, Home Assistant, n8n, SMTP and others impose their own rate limits, payload limits and authentication; they are provider dependent and UNVERIFIED here",
            "delivery_enabled": WEBHOOK_DELIVERY_ENABLED}


_SECRET_KEYS = re.compile(r"(token|secret|password|passwd|authorization|api[_-]?key|credential|cookie)", re.I)


def redact(obj):
    """Credential redaction for anything that is logged or exported: values under secret-looking keys, and userinfo inside URLs, are replaced."""
    if isinstance(obj, dict):
        return {k: ("<redacted>" if _SECRET_KEYS.search(str(k)) else redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    if isinstance(obj, str):
        return re.sub(r"(https?://)[^/@\s]+@", r"\1<redacted>@", obj)
    return obj


def validate_webhook_destination(url: str, allowlist: list[str]) -> tuple[bool, str]:
    """SSRF guard for a FUTURE adapter. Pure (no DNS, no network): the caller must still pin the resolved address when connecting."""
    try:
        u = urlsplit(url)
    except ValueError:
        return False, "unparseable"
    if u.username or u.password:
        return False, "credentials_in_url"
    host = (u.hostname or "").lower()
    if not host or host not in {h.lower() for h in allowlist} or "*" in "".join(allowlist):
        return False, "host_not_in_allowlist"
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            return False, "private_or_reserved_address"
    except ValueError:
        pass   # a hostname: must be resolved and pinned by the adapter
    if u.scheme == "https":
        return True, "ok"
    if u.scheme == "http" and host in ("localhost", "127.0.0.1", "::1"):
        return True, "ok_loopback_http"
    return False, "scheme_not_allowed"


_ALERT_REQ = {"schema", "alert_id", "kind", "event_id", "feed_id", "zone_id", "severity", "priority", "created_utc", "created_epoch", "source", "status", "acknowledged_utc", "resolved_utc",
              "resolved_by", "event", "causes", "occurrences", "occurrence_total", "suppressed_count", "delivery", "simulation", "rev", "delivery_state"}
_ALERT_OPT = {"reopened_count", "pinned_open", "loaded_from_store", "overlap_phase"}
_SRC_KEYS = {"source_id", "kind", "label", "provenance", "repeated_source", "physical_camera", "incident_class", "video_id"}
_PRIORITY = {"RED": "high", "YELLOW": "warning", "UNKNOWN": "diagnostic"}
_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _s(v, n, none=False):
    return (v is None and none) or (isinstance(v, str) and len(v) <= n)


def _int(v, hi=10**9):
    return isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= hi


def _delivery_coherent(a: dict) -> bool:
    """MG-ALERT-031: the stored aggregate must be exactly what the recorded channel outcomes derive, and every outcome must carry the timestamp its status implies
    (pending/disabled/stored: none; a reported outcome or a display: a UTC time). A record that fails this is not what this program writes."""
    d = a["delivery"]
    ia = d["in_app"]
    if (ia["status"] == "displayed") != (ia["at"] is not None) or (ia["at"] is not None and not _UTC.match(ia["at"])):
        return False
    for c in CLIENT_CHANNELS:
        ch = d[c]
        reported = ch["status"] in ("delivered", "denied", "failed", "unsupported")
        if reported != (ch["at"] is not None) or (ch["at"] is not None and not _UTC.match(ch["at"])):
            return False
    return a["delivery_state"] == AlertEngine.delivery_state(d)


def valid_alert(a, coherence: bool = True) -> bool:
    """Strict form of a record read back from disk (cycle-22 MG-STORE-022): exact member set, closed vocabularies, bounded values, the provenance invariants of this increment
    (a drill is scripted and simulated; nothing loaded may claim genuine inference of a kind this process never produced) and, for kernel-event alerts, the identity bound to its content."""
    try:
        if not isinstance(a, dict) or not _ALERT_REQ <= set(a) <= _ALERT_REQ | _ALERT_OPT or a["schema"] != ALERT_SCHEMA:
            return False
        sc = a["source"]
        if not isinstance(sc, dict) or set(sc) != _SRC_KEYS or sc["kind"] not in ("feed", "webcam_session", "simulation") or sc["provenance"] not in PROVENANCE:
            return False
        if not (_s(sc["source_id"], 120) and _s(sc["label"], 80) and _s(sc["incident_class"], 60) and _s(sc["video_id"], 60, True) and sc["physical_camera"] is False and isinstance(sc["repeated_source"], bool)):
            return False
        if a["simulation"] != (sc["provenance"] != "real_inference") or (sc["kind"] == "simulation" and (sc["provenance"] != "synthetic_scripted" or a["simulation"] is not True)):
            return False
        if a["event"] is not None and (not isinstance(a["event"], dict) or a["event"].get("event_id") != a["event_id"]):
            return False                                                              # MG-INCIDENT-037: the nested event is the one the alert identity is bound to
        if sc["kind"] != "simulation" and sc["provenance"] == "synthetic_scripted":
            return False
        if not (re.fullmatch(r"alt_[0-9a-f]{16}", a["alert_id"]) and a["kind"] in ("risk_event", "diagnostic") and a["severity"] in SEVERITIES and a["priority"] == _PRIORITY[a["severity"]]
                and a["status"] in STATUSES and a["delivery_state"] in ("pending", "delivered", "in_app_only", "failed") and _s(a["zone_id"], 32) and _s(a["event_id"], 40, True) and _s(a["feed_id"], 40, True)):
            return False
        if not (_UTC.match(a["created_utc"]) and isinstance(a["created_epoch"], (int, float)) and not isinstance(a["created_epoch"], bool) and 0 < a["created_epoch"] < 4e9
                and _int(a["rev"]) and _int(a["suppressed_count"]) and _int(a["occurrence_total"])):
            return False
        if not (_s(a["acknowledged_utc"], 24, True) and _s(a["resolved_utc"], 24, True) and _s(a["resolved_by"], 40, True)):
            return False
        if not (isinstance(a["causes"], list) and len(a["causes"]) <= 20 and all(_s(c, 60) for c in a["causes"])):
            return False
        occ = a["occurrences"]
        if not (isinstance(occ, list) and len(occ) <= MAX_OCCURRENCES and all(
                isinstance(o, dict) and set(o) in ({"event_id", "open"}, {"event_id", "open", "source_id"}) and _s(o["event_id"], 40) and isinstance(o["open"], bool)
                and ("source_id" not in o or (isinstance(o["source_id"], str) and 0 < len(o["source_id"]) <= 120)) for o in occ)):   # `source_id` is absent only in records written before cycle-24 MG-ALERT-024
            return False
        if "overlap_phase" in a and (a["overlap_phase"] not in overlap.ALERT_PHASES or sc["kind"] != "feed" or a["kind"] != "risk_event"):
            return False                                                                                                             # MG-PROV-028: a closed vocabulary, only for kernel-event alerts of a feed
        if not (("reopened_count" not in a or _int(a["reopened_count"])) and all(k not in a or isinstance(a[k], bool) for k in ("pinned_open", "loaded_from_store"))):
            return False                                                                                                             # cycle-24 MG-STORE-024: every optional member is an exact bounded primitive
        d = a["delivery"]
        if not (isinstance(d, dict) and set(d) == {"in_app", "browser", "sound"} and isinstance(d["in_app"], dict) and set(d["in_app"]) == {"status", "at"} and d["in_app"]["status"] in ("stored", "displayed")
                and _s(d["in_app"]["at"], 24, True)):
            return False
        for c in CLIENT_CHANNELS:
            ch = d[c]
            if not (isinstance(ch, dict) and set(ch) == {"status", "at", "detail"} and ch["status"] in ("pending", "disabled", "delivered", "denied", "failed", "unsupported")
                    and _s(ch["at"], 24, True) and _s(ch["detail"], 120, True)):
                return False
        if coherence and not _delivery_coherent(a):
            return False                                                                                                             # MG-ALERT-031: delivery is never assumed, also not by a record read back from disk
        if a["kind"] == "risk_event":
            if not _s(a["event_id"], 40) or a["event_id"] is None or a["alert_id"] != "alt_" + digest16(digest(["mg.alert.id.v1", sc["source_id"], a["zone_id"], a["event_id"]])):
                return False
        elif a["event_id"] is not None or sc["kind"] == "simulation":
            return False
        ev = a["event"]
        if ev is None:
            return a["kind"] == "diagnostic"
        return (isinstance(ev, dict) and set(ev) == {"event_id", "rule_id", "start_us", "end_us", "open", "tracks", "closed_reason", "right_censored"} and _s(ev["event_id"], 40) and _s(ev["rule_id"], 40)
                and isinstance(ev["start_us"], int) and not isinstance(ev["start_us"], bool) and (ev["end_us"] is None or isinstance(ev["end_us"], int) and not isinstance(ev["end_us"], bool))
                and isinstance(ev["open"], bool) and isinstance(ev["tracks"], list) and len(ev["tracks"]) <= 20 and all(_s(t, 60) for t in ev["tracks"]) and _s(ev["closed_reason"], 60, True)
                and (ev["right_censored"] is None or isinstance(ev["right_censored"], bool)))
    except (KeyError, TypeError, AttributeError, ValueError):
        return False


class AlertEngine:
    def __init__(self, store_dir: Path | None = None, wall=time.time, mono=time.monotonic):
        self.dir = store_dir
        self.wall, self.mono = wall, mono
        self.lock = threading.RLock()
        self.alerts: dict[str, dict] = {}
        self.rev = 0
        self.settings = validate_settings(DEFAULT_SETTINGS)
        self.seen: dict[str, tuple] = {}            # event key -> (open, end_us) already processed (bounded)
        self._alias: dict[str, str] = {}            # event key -> the alert that carries it (an event merged into another alert points there)
        self._open: dict[str, set] = {}             # alert id -> ids of its episodes that are STILL OPEN (independent of the capped occurrence history)
        self.overflow_dropped = 0
        self.rejected_on_load = 0
        self.preserved: dict | None = None             # MG-QUARANTINE-032: what was kept of a store that was not accepted as it stood
        self._drill_n = 0
        self.group_last: dict[tuple, tuple[str, float]] = {}
        self.unknown_since: dict[tuple, int] = {}
        self.unknown_alert: dict[tuple, str] = {}
        self.unknown_merged: dict[tuple, str] = {}   # (source, zone) whose UNKNOWN episode was folded into another source's diagnostic -> that alert (one occurrence per episode)
        self._dirty = False
        self.store_unsafe = False
        self._last_flush = 0.0
        self.counters = {"events_seen": 0, "alerts_created": 0, "merged": 0, "ignored_duplicates": 0}
        self._load()

    # ------------------------------------------------------------------ persistence (inside a PINNED, owned directory: a symlinked `alerts` directory is refused, never followed)
    def _load(self):
        if not self.dir:
            return
        try:
            with safedir.owned_dir(self.dir.parent, self.dir.name, create=False) as fd:
                try:
                    raw = safedir.read_regular_bytes(fd, "alerts.json")
                    try:
                        doc = json.loads(raw)
                        if not isinstance(doc, dict):
                            raise ValueError("not an object")
                    except ValueError:                                           # unparseable: the product starts empty, but the original bytes are kept first
                        self._preserve_rejected_store(fd, raw, 0)
                        raise
                    if doc.get("schema") != "mg.alerts.store.v1":
                        self._preserve_rejected_store(fd, raw, 0)
                    if doc.get("schema") == "mg.alerts.store.v1":
                        rows = doc["alerts"] if isinstance(doc.get("alerts"), list) else []
                        self.alerts = {a["alert_id"]: a for a in rows if valid_alert(a)}
                        self.rejected_on_load = len(rows) - len(self.alerts)    # anything that is not exactly what this program writes is discarded and counted, never trusted
                        if self.rejected_on_load or not isinstance(doc.get("alerts"), list):
                            self._preserve_rejected_store(fd, raw, self.rejected_on_load)
                        for a in self.alerts.values():
                            a["loaded_from_store"] = True                       # read back from disk: history, never counted as produced by this process
                        self.rev = max([int(doc.get("rev", 0))] + [a["rev"] for a in self.alerts.values()]) if isinstance(doc.get("rev", 0), int) else 0
                        for a in self.alerts.values():   # a live source that existed before a restart can never confirm or end its episodes now: close them with that reason (drills stay open for the operator)
                            if a["status"] != "resolved" and a["source"].get("kind") in ("feed", "webcam_session"):
                                a["status"], a["resolved_utc"], a["resolved_by"] = "resolved", self._utc(self.wall()), "server_restart"
                                self.rev += 1
                                a["rev"] = self.rev
                                self._dirty = True
                except FileNotFoundError:
                    pass
                try:
                    self.settings = validate_settings(json.loads(safedir.read_regular(fd, "settings.json")))
                except FileNotFoundError:
                    pass
        except FileNotFoundError:
            pass
        except safedir.UnsafeDir:
            self.store_unsafe = True
            self.alerts, self.rev = {}, 0
        except (OSError, ValueError, KeyError, TypeError, PermissionError):
            self.alerts, self.rev = {}, 0   # an unreadable store starts empty rather than failing the product

    def _preserve_rejected_store(self, fd, raw: bytes, rows_rejected: int):
        """MG-QUARANTINE-032: before anything is normalized or rewritten, the EXACT bytes of an alerts.json that was not accepted as it stands (parse failure, wrong schema, or any rejected row) are kept in
        `alerts.rejected.<sha256 of those bytes>.json`: created exclusively (never overwrites an earlier record), published only when complete, then read back and compared. The recovered records live apart,
        in the rewritten alerts.json. `self.preserved` states what is guaranteed: complete=True only after the read-back matched."""
        h = hashlib.sha256(raw).hexdigest()
        name = f"alerts.rejected.{h}.json"
        info = {"sha256": h, "file": name, "bytes": len(raw), "rows_rejected": rows_rejected, "complete": False}
        try:
            safedir.write_new_bytes(fd, name, raw)
            info["complete"] = hashlib.sha256(safedir.read_regular_bytes(fd, name)).hexdigest() == h     # an earlier file under this name must hold the very same bytes
        except (OSError, safedir.UnsafeDir):
            info["complete"] = False
        self.preserved = info

    def _write(self, name: str, doc: dict):
        try:
            with safedir.owned_dir(self.dir.parent, self.dir.name) as fd:
                safedir.write_atomic(fd, name, json.dumps(doc, indent=1, ensure_ascii=False))
        except safedir.UnsafeDir as e:
            self.store_unsafe = True
            raise OSError(str(e))

    def _flush(self, force: bool = False):
        if not self.dir or not self._dirty:
            return
        if not force and self.mono() - self._last_flush < 1.0:
            return
        try:
            self._write("alerts.json", {"schema": "mg.alerts.store.v1", "rev": self.rev, "alerts": list(self.alerts.values())})
            self._dirty, self._last_flush = False, self.mono()
        except OSError:
            pass

    def flush(self):
        with self.lock:
            self._flush(True)

    def _touch(self, a: dict):
        self.rev += 1
        a["rev"] = self.rev
        self._dirty = True

    def _drop(self, a: dict):
        aid = a["alert_id"]
        self.alerts.pop(aid, None)
        self._open.pop(aid, None)
        for k in [k for k, v in self._alias.items() if v == aid]:
            del self._alias[k]
        self._dirty = True

    def _make_room(self) -> bool:
        """Capacity policy. Below MAX_ALERTS nothing is removed. At the soft limit the oldest RESOLVED alerts go first, then the oldest open DRILLS (exercises, labelled simulation);
        a genuine open alert is never removed to make room. Between the soft and the hard limit genuine alerts are still stored; at the hard limit a new alert is refused and the
        loss is COUNTED (`overflow_dropped`) and shown (`degraded`)."""
        while len(self.alerts) >= MAX_ALERTS:
            pool = [a for a in self.alerts.values() if a["status"] == "resolved"] or [a for a in self.alerts.values() if a["source"]["kind"] == "simulation"]   # ONLY scripted drills: a repeated-source or any kernel alert is never evicted
            if not pool:
                break
            self._drop(min(pool, key=lambda a: a["created_epoch"]))
        if len(self.alerts) >= HARD_MAX_ALERTS:
            self.overflow_dropped += 1
            self.counters["overflow_dropped"] = self.overflow_dropped
            return False
        return True

    # ------------------------------------------------------------------ policy
    def _policy(self, sev: str) -> dict:
        return self.settings[sev.lower()]

    @staticmethod
    def _utc(t: float) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))

    def _delivery_init(self, sev: str) -> dict:
        p = self._policy(sev)
        d = {"in_app": {"status": "stored", "at": None}}   # STORED in the alert center; it becomes "displayed" only when the operator view reports it (seen / acknowledged / resolved)
        for ch in CLIENT_CHANNELS:
            d[ch] = {"status": "pending" if p[ch] else "disabled", "at": None, "detail": None}
        return d

    @staticmethod
    def delivery_state(d: dict) -> str:
        cl = [d[c]["status"] for c in CLIENT_CHANNELS]
        if "failed" in cl:
            return "failed"
        if "pending" in cl:
            return "pending"
        if "delivered" in cl:
            return "delivered"                 # a REAL client delivery is reported even when another channel was denied or unsupported (MG-ALERT-030)
        # no client channel delivered (all disabled, denied or unsupported) and none is pending: the alert exists only in the alert center. Until the operator view has DISPLAYED it, nothing was shown to anyone
        # and it is still pending; afterwards it is in-app only. «delivered» is never assumed from channels that were never attempted (MG-ALERT-029)
        return "in_app_only" if d["in_app"]["status"] == "displayed" else "pending"

    # ------------------------------------------------------------------ ingestion
    def _new_alert(self, src: dict, sev: str, zone_id: str, event: dict | None, kind: str, causes: list | None = None) -> dict | None:
        if not self._make_room():
            return None
        now = self.wall()
        eid = event["event_id"] if event else None
        aid = "alt_" + digest16(digest(["mg.alert.id.v1", src["source_id"], zone_id, eid or "diag", sev, round(now * 1000)] if kind == "diagnostic" else ["mg.alert.id.v1", src["source_id"], zone_id, eid]))
        a = {"schema": ALERT_SCHEMA, "alert_id": aid, "kind": kind, "event_id": eid, "feed_id": src.get("feed_id"), "zone_id": zone_id, "severity": sev,
             "priority": {"RED": "high", "YELLOW": "warning", "UNKNOWN": "diagnostic"}[sev], "created_utc": self._utc(now), "created_epoch": now,
             "source": {k: src.get(k) for k in ("source_id", "kind", "label", "provenance", "repeated_source", "physical_camera", "incident_class", "video_id")},
             "status": "new", "acknowledged_utc": None, "resolved_utc": None, "resolved_by": None,
             "event": ({k: event.get(k) for k in ("event_id", "rule_id", "start_us", "end_us", "open", "tracks", "closed_reason", "right_censored")} if event else None),
             "causes": list(causes or []), "occurrences": ([{"event_id": eid, "open": bool(event.get("open")), "source_id": src["source_id"]}] if event else []), "occurrence_total": 1 if event else 0, "suppressed_count": 0,
             "delivery": self._delivery_init(sev), "simulation": src.get("provenance") != "real_inference", "rev": 0}
        if event and src.get("kind") == "feed":
            a["overlap_phase"] = overlap.phase_of(event.get("start_us"), src.get("overlap"))      # where the episode started relative to the known overlap of its run (MG-PROV-028)
        a["delivery_state"] = self.delivery_state(a["delivery"])
        self.alerts[aid] = a
        self.counters["alerts_created"] += 1
        self._touch(a)
        return a

    @staticmethod
    def _can_merge(a: dict, src: dict) -> bool:
        """A cooldown merge may only join episodes that are the SAME KIND OF THING: same source kind, same provenance and simulation status, and — for a feed — the same RUN (a restarted feed, possibly
        with another provenance, is never folded into the alert of its previous run; cycle-23 MG-ALERT-023). Browser reconnections of one device (several session ids) still merge."""
        s = a["source"]
        return (s["kind"] == src["kind"] and s["provenance"] == src["provenance"] and a["simulation"] == (src["provenance"] != "real_inference")
                and (src["kind"] != "feed" or s["source_id"] == src["source_id"]))

    def _trim_seen(self):
        """Bounds the idempotence memory WITHOUT forgetting episodes that are still open (their later close must find its alert)."""
        for k in [k for k, v in self.seen.items() if not v[0]]:
            del self.seen[k]
            self._alias.pop(k, None)

    def _track(self, a: dict, ev: dict):
        ids = self._open.setdefault(a["alert_id"], set())
        if ev.get("open"):
            if len(ids) < MAX_TRACKED_OPEN:
                ids.add(ev["event_id"])
            else:
                a["pinned_open"] = True          # more simultaneous episodes than tracked: the alert stays open until its source closes (never resolved too early)
        else:
            ids.discard(ev["event_id"])

    def _on_event(self, src: dict, ev: dict, auto_resolve: bool = True):
        sev = ev["severity"]
        if sev not in ("RED", "YELLOW"):
            return
        key = f"{src['source_id']}|{ev['zone_id']}|{ev['event_id']}"
        state = (bool(ev.get("open")), ev.get("end_us"))
        prev = self.seen.get(key)
        if prev == state:
            self.counters["ignored_duplicates"] += 1
            return
        if len(self.seen) > 5000:
            self._trim_seen()
        self.seen[key] = state
        if prev is None:
            self.counters["events_seen"] += 1
            group = (src["group"], ev["zone_id"], sev)
            last = self.group_last.get(group)
            cool = self._policy(sev)["cooldown_s"]
            if (last and last[0] in self.alerts and self.mono() - last[1] < cool and self._can_merge(self.alerts[last[0]], src)
                    and len(self.alerts[last[0]]["occurrences"]) < MAX_OCCURRENCES):   # no merge once the carrier cannot name one more episode: a new alert keeps it attributable (cycle-25 MG-ALERT-024)
                a = self.alerts[last[0]]
                if a["status"] == "resolved":   # flapping (an episode ended and the next one began inside the cooldown): the SAME alert is reopened, no new notification is raised
                    a["status"], a["resolved_utc"], a["resolved_by"] = "active", None, None
                    a["reopened_count"] = a.get("reopened_count", 0) + 1
                if len(a["occurrences"]) < MAX_OCCURRENCES:
                    a["occurrences"].append({"event_id": ev["event_id"], "open": bool(ev.get("open")), "source_id": src["source_id"]})
                if src.get("kind") == "feed" and a.get("event"):
                    if overlap.phase_of(ev.get("start_us"), src.get("overlap")) != overlap.phase_of(a["event"].get("start_us"), src.get("overlap")):
                        a["overlap_phase"] = "mixed"            # the merged episode lies in another phase than the one that created the alert (MG-PROV-028)
                a["occurrence_total"] = a.get("occurrence_total", 1) + 1
                a["suppressed_count"] += 1
                self.counters["merged"] += 1
                self._alias[key] = a["alert_id"]
                self._track(a, ev)
                self._touch(a)
                if auto_resolve:
                    self._maybe_resolve(a)
                return
            a = self._new_alert(src, sev, ev["zone_id"], ev, "risk_event")
            if a is None:                       # hard ceiling: counted and visible, never silent
                del self.seen[key]
                return
            self.group_last[group] = (a["alert_id"], self.mono())
            self._alias[key] = a["alert_id"]
            self._track(a, ev)
        aid = self._alias.get(key)
        a = self.alerts.get(aid) if aid else None
        if a is None:
            return
        for o in a["occurrences"]:
            if o["event_id"] == ev["event_id"] and o.get("source_id", src["source_id"]) == src["source_id"]:
                o["open"] = bool(ev.get("open"))
        if a["event"] and a["event"]["event_id"] == ev["event_id"]:
            a["event"].update({k: ev.get(k) for k in ("end_us", "open", "closed_reason", "right_censored")})
        self._track(a, ev)
        self._touch(a)
        if auto_resolve:
            self._maybe_resolve(a)

    def _maybe_resolve(self, a: dict):
        if a["status"] != "resolved" and a["kind"] == "risk_event" and a["occurrences"] and not self._open.get(a["alert_id"]) and not a.get("pinned_open"):
            self._resolve(a, "event_closed")           # only when EVERY episode this alert carries has closed, however many were merged into it

    def _resolve(self, a: dict, by: str):
        if a["status"] == "resolved":
            return
        a["status"], a["resolved_utc"], a["resolved_by"] = "resolved", self._utc(self.wall()), by
        self._touch(a)

    def _on_zone_state(self, src: dict, zone_id: str, state: dict, final: bool):
        gk = (src["source_id"], zone_id)
        p = self.settings["unknown"]
        if final or state.get("state") != "UNKNOWN":
            self.unknown_since.pop(gk, None)
            self.unknown_merged.pop(gk, None)
            aid = self.unknown_alert.pop(gk, None)
            if aid and aid in self.alerts:
                self._resolve(self.alerts[aid], "source_closed" if final else "state_left_unknown")
            return
        t = state.get("t_us")
        if t is None:
            return
        since = self.unknown_since.setdefault(gk, t)
        if gk in self.unknown_alert or gk in self.unknown_merged or t - since < p["after_s"] * 1_000_000:
            return
        group = (src["group"], zone_id, "UNKNOWN")
        last = self.group_last.get(group)
        carrier = self.alerts.get(last[0]) if last else None
        if carrier is not None and self.mono() - last[1] < p["cooldown_s"] and self._can_merge(carrier, src) and len(carrier["occurrences"]) < MAX_OCCURRENCES:
            # the diagnostic of another source folded into the open one is NEVER silent: a bounded, attributable occurrence is kept (cycle-24 MG-ALERT-024)
            carrier["occurrences"].append({"event_id": "unk_" + digest16(digest(["mg.alert.unk.v1", src["source_id"], zone_id, since])), "open": False, "source_id": src["source_id"]})
            carrier["occurrence_total"] = carrier.get("occurrence_total", 0) + 1
            carrier["suppressed_count"] += 1
            self.unknown_merged[gk] = carrier["alert_id"]
            self.counters["merged"] += 1
            self._touch(carrier)
            return
        a = self._new_alert(src, "UNKNOWN", zone_id, None, "diagnostic", state.get("causes"))
        if a is None:
            return
        self.unknown_alert[gk] = a["alert_id"]
        self.group_last[group] = (a["alert_id"], self.mono())

    def _downgrade_run(self, src: dict):
        """A feed run that is (now) part of a repeated-file concurrency simulation can no longer have genuine live alerts: alerts of THAT run still labelled real_inference are re-labelled
        repeated_source_simulation. Monotonic: it never goes back (a record is never upgraded to genuine), and nothing else about the alert changes (cycle-26 MG-PROV-026)."""
        if src.get("kind") != "feed" or src.get("provenance") != "repeated_source_simulation":
            return
        for a in self.alerts.values():
            if a["source"]["source_id"] != src["source_id"]:
                continue
            changed = False
            if a["source"]["provenance"] == "real_inference":
                a["source"]["provenance"], a["source"]["repeated_source"], a["simulation"] = "repeated_source_simulation", True, True      # the run-level label stays conservative for EVERY alert of the run
                changed = True
            if a.get("event") and a["kind"] == "risk_event" and src.get("overlap") is not None:
                ph = overlap.phase_of(a["event"]["start_us"], src["overlap"])
                if ph != "unknown" and a.get("overlap_phase") not in (ph, "mixed"):                       # the phase only gets more precise as the history grows; it never erases the evidence of the pre-overlap period
                    a["overlap_phase"], changed = ph, True
            if changed:
                self._touch(a)

    def observe_source(self, src: dict, events: list[dict], zone_states: dict, final: bool = False):
        with self.lock:
            self._downgrade_run(src)
            for e in events:
                self._on_event(src, e)
            for zid, st in zone_states.items():
                self._on_zone_state(src, zid, st, final)
            if final:   # a source that ended can no longer confirm or end an episode: its open alerts are closed with that reason (never silently left "active")
                for a in self.alerts.values():
                    if a["source"]["source_id"] == src["source_id"] and a["status"] != "resolved":
                        self._resolve(a, "source_closed")
            self._flush(final)

    def observe_feed(self, feed, final: bool = False):
        sd = feed.source_doc()
        src = {"source_id": feed.fid + ":" + feed.run_uid, "group": feed.fid, "feed_id": feed.fid, "kind": "feed", "label": feed.label, "provenance": sd["provenance"],
               "repeated_source": sd["repeated_source"], "physical_camera": False, "incident_class": "scenario_file", "video_id": sd["video_id"], "overlap": feed.overlap_doc()}
        self.observe_source(src, feed.events(), feed.risk_now, final)

    def observe_session(self, sid: str, device_label: str, origin: str, events: list[dict], zone_states: dict, final: bool = False):
        src = {"source_id": "webcam:" + sid, "group": "webcam:" + (device_label or "device"), "feed_id": None, "kind": "webcam_session", "label": device_label or "Cámara", "provenance": "real_inference",
               "repeated_source": False, "physical_camera": False, "incident_class": "live_camera_declared_by_client" if origin == "getusermedia" else "test_source", "video_id": None}
        self.observe_source(src, events, zone_states, final)

    def ingest_simulation(self, run_id: str, events: list[dict]):
        """The scripted RED/YELLOW demonstration: alerts exist (to exercise the product) but are labelled SIMULACIÓN everywhere."""
        # every press of the drill is a NEW drill (its own source and cooldown group, stamped with the time): two drills never merge, and a drill is never mistaken for an incident
        with self.lock:
            self._drill_n += 1
            drill = f"{run_id}:{self._drill_n}:{secrets.token_hex(4)}"   # a counter and a nonce: two presses within the same millisecond are still two drills
        src = {"source_id": "sim:" + drill, "group": "sim:" + drill, "feed_id": None, "kind": "simulation", "label": "Simulación ROJO", "provenance": "synthetic_scripted", "repeated_source": False,
               "physical_camera": False, "incident_class": "simulation", "video_id": None}
        with self.lock:
            drills = sorted((a for a in self.alerts.values() if a["simulation"] and a["source"]["kind"] == "simulation"), key=lambda a: a["created_epoch"])
            for a in drills[: max(0, len(drills) - MAX_OPEN_DRILLS + len(events))]:
                self._drop(a)                                    # exercises are retired oldest-first; a drill never accumulates without bound
            for e in events:
                self._on_event(src, {**e, "open": False}, auto_resolve=False)   # a drill stays open until the operator acknowledges or resolves it: that is what it exercises
            self._flush(True)

    # ------------------------------------------------------------------ operator / client operations
    def get(self, aid: str) -> dict:
        a = self.alerts.get(aid)
        if a is None:
            raise KeyError(aid)
        return a

    def mark_seen(self, ids: list[str]) -> int:
        n = 0
        with self.lock:
            for i in ids:
                a = self.alerts.get(i)
                if a and a["status"] == "new":
                    a["status"] = "active"
                    self._display(a)
                    self._touch(a)
                    n += 1
            self._flush()
        return n

    def _display(self, a: dict):
        if a["delivery"]["in_app"]["status"] != "displayed":
            a["delivery"]["in_app"] = {"status": "displayed", "at": self._utc(self.wall())}
            a["delivery_state"] = self.delivery_state(a["delivery"])

    def acknowledge(self, aid: str) -> dict:
        with self.lock:
            a = self.get(aid)
            if a["status"] == "resolved":
                raise ValueError("already_resolved")
            self._display(a)                                    # an operator act presupposes that the alert was shown to them
            if a["status"] != "acknowledged":
                a["status"], a["acknowledged_utc"] = "acknowledged", self._utc(self.wall())
                self._touch(a)
            self._flush(True)
            return a

    def resolve(self, aid: str) -> dict:
        with self.lock:
            a = self.get(aid)
            self._display(a)
            self._resolve(a, "operator")
            self._flush(True)
            return a

    def report_delivery(self, aid: str, channel: str, status: str, detail: str | None = None) -> dict:
        if channel not in CLIENT_CHANNELS or status not in DELIVERY_STATUSES:
            raise ValueError("bad_delivery_report")
        with self.lock:
            a = self.get(aid)
            if a.get("loaded_from_store"):
                raise ValueError("restored_alert_not_deliverable")      # MG-ALERT-031: this process sent nothing for a record read back from disk
            slot = a["delivery"][channel]
            if slot["status"] == "disabled":
                raise ValueError("channel_disabled_by_policy")
            if slot["status"] != "pending" and slot["status"] != status:
                raise ValueError("already_reported")
            if slot["status"] == status:
                return a                                                # MG-ALERT-032: an identical terminal report is idempotent: no new timestamp, detail, revision or write
            slot.update({"status": status, "at": self._utc(self.wall()), "detail": (redact(detail)[:120] if isinstance(detail, str) else None)})
            a["delivery_state"] = self.delivery_state(a["delivery"])
            self._touch(a)
            self._flush(True)
            return a

    def set_settings(self, s: dict) -> dict:
        new = validate_settings(s)
        with self.lock:
            self.settings = new
            if self.dir:
                try:
                    self._write("settings.json", new)
                except OSError:
                    pass
        return new

    # ------------------------------------------------------------------ queries
    _PUBLIC = ("schema", "alert_id", "kind", "event_id", "feed_id", "zone_id", "severity", "priority", "created_utc", "status", "acknowledged_utc", "resolved_utc", "resolved_by", "causes",
               "occurrence_total", "suppressed_count", "simulation", "rev", "delivery_state", "reopened_count", "pinned_open", "loaded_from_store")

    def public(self, a: dict) -> dict:
        """A FIXED PROJECTION of a stored alert: only members this program defines, nested records rebuilt member by member, so nothing else that might sit in a record (it was validated, but
        records are also read from disk) can ever leave through the API (cycle-23 MG-STORE-023)."""
        out = {k: a[k] for k in self._PUBLIC if k in a and k not in ("reopened_count", "pinned_open", "loaded_from_store")}
        out["reopened_count"] = a["reopened_count"] if _int(a.get("reopened_count")) else 0                # exact primitives only, whatever a record on disk holds (cycle-24 MG-STORE-024)
        out["pinned_open"] = a.get("pinned_open") is True
        out["source"] = {k: a["source"].get(k) for k in ("source_id", "kind", "label", "provenance", "repeated_source", "physical_camera", "incident_class", "video_id")}
        out["occurrences"] = [{"event_id": o["event_id"], "open": o["open"], "source_id": o.get("source_id")} for o in a["occurrences"]]
        out["delivery"] = {"in_app": {"status": a["delivery"]["in_app"]["status"], "at": a["delivery"]["in_app"]["at"]},
                           **{c: {k: a["delivery"][c].get(k) for k in ("status", "at", "detail")} for c in CLIENT_CHANNELS}}
        out["event"] = None if a["event"] is None else {k: a["event"].get(k) for k in ("event_id", "rule_id", "start_us", "end_us", "open", "tracks", "closed_reason", "right_censored")}
        out["causes"] = list(a["causes"])
        out["loaded_from_store"] = bool(a.get("loaded_from_store"))
        out["overlap_phase"] = (a["overlap_phase"] if a.get("overlap_phase") in overlap.ALERT_PHASES else overlap.legacy_phase(bool(a["source"].get("repeated_source")))) if (a["source"].get("kind") == "feed" and a["kind"] == "risk_event") else None
        return out

    def list(self, status: str | None = None, severity: str | None = None, source_kind: str | None = None, simulated: bool | None = None, after: int = 0, limit: int = 200, order: str = "desc") -> dict:
        """`order=asc` is the PAGINATION mode of the incremental feed: records changed after revision `after`, oldest revision first, at most `limit`; `next_after` is the revision of the last
        record returned and `more` says whether another page exists, so a client that drains the pages and only then advances its cursor can never skip a record (cycle-21 MG-ALERT-022).
        `order=desc` (default) is the newest-first view for people."""
        with self.lock:
            rows = [a for a in self.alerts.values() if a["rev"] > after and (status is None or a["status"] == status) and (severity is None or a["severity"] == severity)
                    and (source_kind is None or a["source"]["kind"] == source_kind) and (simulated is None or a["simulation"] == simulated)]
            if order == "asc":
                rows.sort(key=lambda a: a["rev"])
                page = rows[:limit]
                return {"api_version": API_VERSION, "rev": self.rev, "alerts": [self.public(a) for a in page], "total_matching": len(rows), "more": len(rows) > limit,
                        "next_after": page[-1]["rev"] if page else after, "count": len(self.alerts)}
            rows.sort(key=lambda a: a["created_epoch"], reverse=True)
            return {"api_version": API_VERSION, "rev": self.rev, "alerts": [self.public(a) for a in rows[:limit]], "total_matching": len(rows), "more": len(rows) > limit,
                    "next_after": None, "count": len(self.alerts)}

    def ids(self) -> list[str]:
        """All alert ids currently stored (bounded): lets a client reconcile what the server has PRUNED."""
        with self.lock:
            return sorted(self.alerts)

    def summary(self) -> dict:
        with self.lock:
            al = list(self.alerts.values())
            by = lambda f: {k: sum(1 for a in al if f(a) == k) for k in sorted({f(a) for a in al})}
            return {"api_version": API_VERSION, "total": len(al), "open": sum(1 for a in al if a["status"] != "resolved"), "by_status": by(lambda a: a["status"]), "by_severity": by(lambda a: a["severity"]),
                    "by_provenance": {k: sum(1 for a in al if not a.get("loaded_from_store") and a["source"]["provenance"] == k) for k in sorted({a["source"]["provenance"] for a in al if not a.get("loaded_from_store")})},
                    "loaded_from_disk_unverified": sum(1 for a in al if a.get("loaded_from_store")), "rejected_on_load": self.rejected_on_load, "preserved_rejected_store": self.preserved, "by_delivery_state": {k: sum(1 for a in al if not a.get("loaded_from_store") and a["delivery_state"] == k) for k in sorted({a["delivery_state"] for a in al if not a.get("loaded_from_store")})}, "by_source": by(lambda a: a["source"]["label"]),
                    "active_unacknowledged": sum(1 for a in al if a["status"] in ("new", "active")), "acknowledged_open": sum(1 for a in al if a["status"] == "acknowledged"),
                    "merged_suppressed": sum(a["suppressed_count"] for a in al), "counters": dict(self.counters),
                    "store_status": "unsafe_not_persisted" if self.store_unsafe else ("memory_only" if not self.dir else "ok"),
                    "capacity": {"soft": MAX_ALERTS, "hard": HARD_MAX_ALERTS, "stored": len(al), "overflow_dropped": self.overflow_dropped, "degraded": self.overflow_dropped > 0 or len(al) > MAX_ALERTS,
                                 "open_drills": sum(1 for a in al if a["simulation"] and a["source"]["kind"] == "simulation")}}
