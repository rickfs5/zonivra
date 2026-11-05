"""Fail-closed spending/quota governor, enforced at the outbound-request boundary (`GuardedTransport.send`), not in a UI toggle.

Defaults refuse everything. Configuration comes ONLY from the process environment at start-up (MG_META_*), never from a request body or frontend. Unknown price, unknown
usage and unknown outcome are all treated as SPEND (conservative), never as free. A local estimate is not a provider charge. No key or bearer value is ever stored in logs."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Protocol

from .rawjson import raw_digest


class Denied(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class TransportTimeout(Exception):
    pass


class RealTransportNotImplemented(Exception):
    pass


def _flag(env, name) -> bool:
    return env.get(name, "").strip().lower() == "true"


def _dec(env, name) -> Decimal:
    try:
        v = Decimal(env.get(name, "0").strip() or "0")
        return v if v.is_finite() and v >= 0 else Decimal(0)
    except InvalidOperation:
        return Decimal(0)


def _int(env, name) -> int:
    try:
        return max(int(env.get(name, "0").strip() or "0"), 0)
    except ValueError:
        return 0


def _map(env, name, conv) -> tuple:
    """`a=1,b=2` -> (('a', conv('1')), ...). Any malformed entry discards the WHOLE map (fail closed)."""
    raw = env.get(name, "").strip()
    out = []
    for part in [p for p in raw.split(",") if p.strip()]:
        k, sep, v = part.partition("=")
        try:
            if not sep or not k.strip():
                return ()
            val = conv(v.strip())
            if val is None:
                return ()
            out.append((k.strip(), val))
        except (InvalidOperation, ValueError):
            return ()
    return tuple(out)


def _pos_dec(v):
    d = Decimal(v)
    return d if d.is_finite() and d >= 0 else None


@dataclass(frozen=True)
class GovernorConfig:
    """Deny by default: every limit is zero/empty. A request needs ALL of: live enabled, credentials, a verified price, a provider budget, request/token/concurrency
    quota, an owner-authorized payload digest and no engaged kill switch."""
    live_provider_enabled: bool = False
    external_media_upload_enabled: bool = False
    paid_fallback_enabled: bool = False
    max_real_requests: int = 0
    max_authorized_spend_usd: Decimal = Decimal(0)
    price_usd_per_request: tuple = ()        # ((model_id, Decimal per billable unit), ...) VERIFIED prices only; empty = every price unknown
    provider_budget_usd: tuple = ()          # ((provider_id, Decimal), ...); provider not listed = no budget = denied
    max_tokens: int = 0
    max_concurrency: int = 0
    estimate_margin: Decimal = Decimal("1.25")   # conservative multiplier on the reserved estimate (>= 1)
    request_timeout_s: int = 30
    authorized_payload_digests: frozenset = frozenset()   # exact request bodies the owner approved offline
    kill_switch_file: str = ""               # existence of this file engages the kill switch; unreadable = engaged
    ledger_path: str = ""                    # durable hash-chained ledger; empty = in-memory only

    @classmethod
    def from_env(cls, env=None) -> "GovernorConfig":
        env = os.environ if env is None else env
        margin = _dec(env, "MG_META_ESTIMATE_MARGIN")
        return cls(_flag(env, "MG_META_LIVE_PROVIDER_ENABLED"), _flag(env, "MG_META_EXTERNAL_MEDIA_UPLOAD_ENABLED"), _flag(env, "MG_META_PAID_FALLBACK_ENABLED"),
                   _int(env, "MG_META_MAX_REAL_REQUESTS"), _dec(env, "MG_META_MAX_AUTHORIZED_SPEND_USD"),
                   _map(env, "MG_META_PRICES_USD_PER_UNIT", _pos_dec), _map(env, "MG_META_PROVIDER_BUDGETS_USD", _pos_dec),
                   _int(env, "MG_META_MAX_TOKENS"), _int(env, "MG_META_MAX_CONCURRENCY"), margin if margin >= 1 else Decimal("1.25"),
                   _int(env, "MG_META_REQUEST_TIMEOUT_S") or 30,
                   frozenset(x.strip() for x in env.get("MG_META_AUTHORIZED_PAYLOAD_DIGESTS", "").split(",") if x.strip()),
                   env.get("MG_META_KILL_SWITCH_FILE", "").strip(), env.get("MG_META_LEDGER_PATH", "").strip())

    def price(self, model_id: str) -> Decimal | None:
        return dict(self.price_usd_per_request).get(model_id)

    def provider_budget(self, provider: str) -> Decimal | None:
        return dict(self.provider_budget_usd).get(provider)


class LedgerCorrupt(Exception):
    pass


LEDGER_FIELDS = ("event", "slot", "key_digest", "provider", "model", "estimate_usd", "actual_usd", "tokens", "state", "code", "usage_reported")


def key_digest(key: str) -> str:
    return hashlib.sha256(("metalab-key|" + str(key)).encode()).hexdigest()[:16]


class Ledger:
    """Append-only, hash-chained JSONL. Only whitelisted fields are ever written (no key, bearer, payload or request key); each write is fsynced BEFORE the request is sent."""

    def __init__(self, path: str):
        self.path, self._prev, self._seq = path, "0" * 64, 0

    @property
    def head_path(self) -> str:
        return self.path + ".head"

    def _read_head(self):
        try:
            with open(self.head_path) as fh:
                n, h = fh.read().split()
                return int(n), h
        except FileNotFoundError:
            return None
        except (ValueError, OSError):
            raise LedgerCorrupt("ledger head marker unreadable")

    def _write_head(self):
        tmp = self.head_path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, f"{self._seq} {self._prev}".encode())
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, self.head_path)

    def replay(self) -> list[dict]:
        """Tamper-EVIDENT, not tamper-proof: the chain is unkeyed, so someone able to rewrite BOTH the ledger and its head marker can forge a history. Deletion, truncation,
        reordering and partial edits are detected; a forger with write access to both files is out of scope (see MG-META-002)."""
        out, prev, hashes = [], "0" * 64, []
        head = self._read_head()
        if not os.path.exists(self.path):
            if head is not None and head != (0, "0" * 64):
                raise LedgerCorrupt("ledger missing but its head marker records entries or carries a non-initial hash")   # an absent ledger is only consistent with an empty history
            return out
        with open(self.path, "rb") as fh:
            for n, line in enumerate(fh, 1):
                try:
                    rec = json.loads(line)
                    h = rec.pop("h")
                    if rec.get("prev") != prev or h != hashlib.sha256((prev + json.dumps(rec, sort_keys=True, separators=(",", ":"))).encode()).hexdigest():
                        raise ValueError
                except (ValueError, KeyError, TypeError):
                    raise LedgerCorrupt(f"ledger line {n} failed verification")
                prev = h
                out.append(rec)
                hashes.append(h)
        self._prev, self._seq = prev, len(out)
        if head is None:
            if out:
                raise LedgerCorrupt("ledger has entries but no head marker")
        else:
            n, h = head
            # the marker is written AFTER the record, so a crash may leave it exactly one record behind; anything else is truncation or a rewrite
            if n == len(out):
                if h != prev:
                    raise LedgerCorrupt("head marker does not match the ledger tail")
            elif n == len(out) - 1:
                if h != (hashes[n - 1] if n >= 1 else "0" * 64):
                    raise LedgerCorrupt("head marker does not identify the record it claims to follow")   # one-behind is a crash artefact only if the marker is the hash of that earlier record
            else:
                raise LedgerCorrupt("head marker disagrees with the ledger length")
        return out

    def append(self, rec: dict):
        body = {k: (str(v) if isinstance(v, Decimal) else v) for k, v in rec.items() if k in LEDGER_FIELDS}
        body["seq"], body["prev"] = self._seq, self._prev
        h = hashlib.sha256((self._prev + json.dumps(body, sort_keys=True, separators=(",", ":"))).encode()).hexdigest()
        line = json.dumps({**body, "h": h}, sort_keys=True, separators=(",", ":")) + "\n"
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line.encode())
            os.fsync(fd)
        finally:
            os.close(fd)
        self._prev, self._seq = h, self._seq + 1
        self._write_head()


@dataclass
class Reservation:
    key: str
    model_id: str
    estimate_usd: Decimal
    state: str = "reserved"   # reserved -> committed | unknown_outcome
    actual_usd: Decimal | None = None
    slot: str = ""
    provider: str = ""
    tokens: int = 0
    actual_tokens: int | None = None

    @property
    def usd(self) -> Decimal:
        return self.actual_usd if self.actual_usd is not None else self.estimate_usd

    @property
    def tok(self) -> int:
        return self.actual_tokens if self.actual_tokens is not None else self.tokens


class SpendGovernor:
    """Atomic reserve -> reconcile, write-ahead persisted. Every attempt, including retries and timeouts, consumes budget: there are no free retries. These are LOCAL limits:
    they do not stop a provider from billing more (see docs: no provider-side hard cap is documented)."""

    def __init__(self, config: GovernorConfig | None = None):
        self.config = config or GovernorConfig()
        self._lock = threading.Lock()
        self._res: dict[str, Reservation] = {}
        self.audit: list[dict] = []
        self._killed = False
        self._ledger = Ledger(self.config.ledger_path) if self.config.ledger_path else None
        self.ledger_error: str | None = None
        if self._ledger:
            try:
                self._recover(self._ledger.replay())
            except (LedgerCorrupt, OSError) as e:
                self.ledger_error = type(e).__name__   # fail closed: every reserve is denied

    def _recover(self, recs: list[dict]):
        """A reservation without a settlement means the process died mid-request: it is counted as spend (unknown outcome), never forgotten."""
        for r in recs:
            ev, slot = r.get("event"), r.get("slot")
            if ev == "reserve":
                self._res[slot] = Reservation(r.get("key_digest", ""), r.get("model", ""), Decimal(r["estimate_usd"]), slot=slot, provider=r.get("provider", ""), tokens=int(r.get("tokens", 0)))
            elif ev == "settle" and slot in self._res:
                x = self._res[slot]
                x.state, x.actual_usd = r["state"], Decimal(r["actual_usd"])
                x.actual_tokens = int(r["tokens"]) if r.get("tokens") is not None else None
            elif ev == "kill":
                self._killed = True
        for x in self._res.values():
            if x.state == "reserved":
                x.state, x.actual_usd = "unknown_outcome", x.estimate_usd

    def _log(self, event: str, **kw):
        self.audit.append({"event": event, **kw})   # never contains credentials, payloads or raw request keys

    def _persist(self, rec: dict):
        if self._ledger:
            self._ledger.append(rec)

    def engage_kill_switch(self, reason: str = "manual"):
        with self._lock:
            self._killed = True
            self._log("kill_switch", reason=reason)
            try:
                self._persist({"event": "kill", "code": reason})
            except OSError:
                pass   # the in-memory switch is already engaged

    def kill_engaged(self) -> bool:
        if self._killed:
            return True
        f = self.config.kill_switch_file
        if f:
            try:
                os.stat(f)
                return True
            except FileNotFoundError:
                return False
            except OSError:
                return True   # unreadable switch = engaged
        return False

    def reserve(self, key: str, model_id: str, provider: str = "", est_tokens: int | None = None, units: int = 1) -> Reservation:
        """A key already committed or still in flight is a duplicate submission (denied). A key whose earlier attempts ended in an unknown outcome is a RETRY:
        allowed, but it is a new attempt that consumes budget again."""
        c = self.config
        kd = key_digest(key)

        def deny(code):
            self._log("denied", code=code, key_digest=kd)
            raise Denied(code)

        with self._lock:
            if self.kill_engaged():
                deny("kill_switch_engaged")
            if self.ledger_error:
                deny("ledger_unreadable")
            if self._ledger is None:
                deny("ledger_required")   # no write-ahead record possible -> nothing may be sent
            prior = [r for r in self._res.values() if r.key == kd]
            if any(r.state in ("reserved", "committed") for r in prior):
                deny("duplicate_submission")
            if not isinstance(units, int) or isinstance(units, bool) or units < 1:
                deny("invalid_units")
            price = c.price(model_id)
            if price is None:
                deny("unknown_model_price")
            budget = c.provider_budget(provider)
            if not provider or budget is None:
                deny("provider_budget_missing")
            if len(self._res) >= c.max_real_requests:
                deny("request_budget_exhausted")
            if sum(1 for r in self._res.values() if r.state == "reserved") >= c.max_concurrency:
                deny("concurrency_limit")
            if not isinstance(est_tokens, int) or isinstance(est_tokens, bool) or est_tokens < 0:
                deny("unknown_token_estimate")
            if sum(r.tok for r in self._res.values()) + est_tokens > c.max_tokens:
                deny("token_budget_exhausted")
            estimate = price * units * max(c.estimate_margin, Decimal(1))
            if sum(r.usd for r in self._res.values() if r.provider == provider) + estimate > budget:
                deny("provider_budget_exhausted")
            if sum(r.usd for r in self._res.values()) + estimate > c.max_authorized_spend_usd:
                deny("spend_budget_exhausted")
            slot = f"{kd}#{len(prior)}"
            r = Reservation(kd, model_id, estimate, slot=slot, provider=provider, tokens=est_tokens)
            try:
                self._persist({"event": "reserve", "slot": slot, "key_digest": kd, "provider": provider, "model": model_id, "estimate_usd": estimate, "tokens": est_tokens})
            except OSError:
                deny("ledger_unavailable")   # write-ahead failed: nothing may be sent
            self._res[slot] = r
            self._log("reserved", key_digest=kd, estimate_usd=str(estimate))
            return r

    def _settle(self, r: Reservation, state: str, actual: Decimal, tokens):
        r.state, r.actual_usd = state, actual
        r.actual_tokens = tokens
        try:
            self._persist({"event": "settle", "slot": r.slot, "state": state, "actual_usd": actual, "tokens": tokens})
        except OSError:
            self._killed = True   # cannot persist: stop everything rather than risk an untracked spend
            self._log("kill_switch", reason="ledger_write_failed")

    def reconcile(self, r: Reservation, usage: dict | None):
        """Missing/invalid usage keeps the (conservative) estimate and is flagged; it never lowers spend to zero. A reported cost above the estimate trips the kill switch."""
        with self._lock:
            try:
                cost = Decimal(str(usage["cost_usd"])) if usage and "cost_usd" in usage else None
                if cost is not None and (not cost.is_finite() or cost < 0):
                    cost = None
            except (InvalidOperation, TypeError, KeyError):
                cost = None
            tok = usage.get("tokens") if isinstance(usage, dict) else None
            tok = tok if isinstance(tok, int) and not isinstance(tok, bool) and tok >= 0 else None
            self._settle(r, "committed" if cost is not None else "unknown_outcome", cost if cost is not None else r.estimate_usd, tok)
            self._log("reconciled", key_digest=r.key, state=r.state, usage_reported=cost is not None)
            if cost is not None and cost > r.estimate_usd:
                self._killed = True
                self._log("kill_switch", reason="overrun_auto_kill")
                try:
                    self._persist({"event": "kill", "code": "overrun_auto_kill"})
                except OSError:
                    pass

    def release(self, r: Reservation):
        """The request was provably NOT handed to the transport (kill switch / cancellation after reserve): no spend, but the request slot stays consumed."""
        with self._lock:
            self._settle(r, "released", Decimal(0), 0)
            self._log("released", key_digest=r.key)

    def mark_unknown(self, r: Reservation):
        with self._lock:
            self._settle(r, "unknown_outcome", r.estimate_usd, None)
            self._log("unknown_outcome", key_digest=r.key)

    def snapshot(self) -> dict:
        with self._lock:
            spent = sum(r.usd for r in self._res.values())
            return {"real_requests_reserved": len(self._res), "max_real_requests": self.config.max_real_requests, "locally_estimated_usd": str(spent),
                    "max_authorized_spend_usd": str(self.config.max_authorized_spend_usd), "tokens_reserved": sum(r.tok for r in self._res.values()),
                    "max_tokens": self.config.max_tokens, "in_flight": sum(1 for r in self._res.values() if r.state == "reserved"),
                    "max_concurrency": self.config.max_concurrency, "kill_switch_engaged": self.kill_engaged(), "ledger_ok": self.ledger_error is None,
                    "note": "local estimate; not a provider charge; no provider-side hard cap is documented"}


class Transport(Protocol):
    def send(self, request: dict, credentials: str, timeout_s: int = 30) -> dict: ...


class NullRealTransport:
    """The only 'real' transport shipped: it performs no I/O and always refuses (state PROVIDER_DISABLED / LIVE_NOT_VERIFIED)."""
    outbound_attempts = 0

    def send(self, request: dict, credentials: str, timeout_s: int = 30) -> dict:
        raise RealTransportNotImplemented("no real transport is connected")


class FakeTransport:
    """Deterministic scripted transport. `script` maps attempt number (0-based) to an outcome: dict response, or an exception instance to raise."""

    def __init__(self, script: dict | None = None, default: dict | None = None):
        self.script, self.default, self.calls, self.outbound_attempts = script or {}, default or {"status": 200, "usage": {"cost_usd": "0.01", "tokens": 0}, "body": {}}, 0, 0
        self.seen_timeouts: list = []
        self._lk = threading.Lock()

    def send(self, request: dict, credentials: str, timeout_s: int = 30) -> dict:
        with self._lk:
            n = self.calls
            self.calls += 1
            self.seen_timeouts.append(timeout_s)
        out = self.script.get(n, self.default)
        if isinstance(out, BaseException):
            raise out
        return out


MEDIA_TYPES = frozenset({"input_image", "input_video", "input_audio", "input_file", "image_url", "video_url"})
MEDIA_KEYS = frozenset({"image_url", "video_url", "file_id", "input_video", "input_image", "input_audio", "input_file"})


def count_media_parts(body, _depth=0) -> int:
    """Media references found INSIDE the authorized body (what the provider would actually fetch), independent of the caller's outer `media` flag."""
    if _depth > 20:
        return 1   # absurd nesting: treat as media (fail closed)
    if isinstance(body, dict):
        here = 1 if (body.get("type") in MEDIA_TYPES or any(k in MEDIA_KEYS for k in body)) else 0
        return here + sum(count_media_parts(v, _depth + 1) for v in body.values() if isinstance(v, (dict, list)))
    if isinstance(body, list):
        return sum(count_media_parts(v, _depth + 1) for v in body)
    return 0


ALLOWED_REQUEST_KEYS = frozenset({"model", "request_key", "provider", "media", "tier", "est_tokens", "units", "body"})
NON_RETRYABLE = frozenset({"provider_quota_exceeded", "invalid_credentials", "kill_switch_engaged", "cancelled", "payload_not_authorized", "unauthorized_request_field"})


class GuardedTransport:
    """Every real provider call goes through `send`. Checks run in a fixed order and fail closed. Nothing here can enable a provider: configuration is read-only."""

    def __init__(self, inner: Transport, governor: SpendGovernor, credentials=lambda: "", cost_fn=None):
        self.inner, self.governor, self._credentials, self._cost_fn = inner, governor, credentials, cost_fn

    def send(self, request: dict, cancel: threading.Event | None = None) -> dict:
        request = copy.deepcopy(request)   # what is authorized is what is sent: later mutation of the caller's object cannot reach the transport
        c = self.governor.config
        if self.governor.kill_engaged():
            raise Denied("kill_switch_engaged")
        if not c.live_provider_enabled:
            raise Denied("live_provider_disabled")
        if cancel is not None and cancel.is_set():
            raise Denied("cancelled")
        if set(request) - ALLOWED_REQUEST_KEYS:
            raise Denied("unauthorized_request_field")
        cred = self._credentials()
        if not isinstance(cred, str) or len(cred) < 8 or cred != cred.strip() or any(ch.isspace() for ch in cred):
            raise Denied("credentials_missing_or_malformed")
        if request.get("media") and not c.external_media_upload_enabled:
            raise Denied("external_media_upload_disabled")
        if request.get("tier") == "paid_fallback" and not c.paid_fallback_enabled:
            raise Denied("paid_fallback_disabled")
        body = request.get("body")
        if not isinstance(body, dict) or raw_digest(body) not in c.authorized_payload_digests:
            raise Denied("payload_not_authorized")   # the exact body must have been approved offline by its digest
        if body.get("model") != request.get("model"):
            raise Denied("request_body_mismatch")   # price and quota are looked up by the outer model: it must be the model the authorized body targets
        n_media = count_media_parts(body)
        if n_media and not c.external_media_upload_enabled:
            raise Denied("external_media_upload_disabled")
        units = request.get("units", 1)
        if isinstance(units, int) and not isinstance(units, bool) and units < n_media:
            raise Denied("units_underdeclared")
        key = str(request.get("request_key", ""))
        res = self.governor.reserve(key, str(request.get("model")), str(request.get("provider", "")), request.get("est_tokens"), request.get("units", 1))
        if self.governor.kill_engaged() or (cancel is not None and cancel.is_set()):
            self.governor.release(res)   # last check before the transport; a residual window between this line and the call is inherent and documented
            raise Denied("kill_switch_engaged" if self.governor.kill_engaged() else "cancelled")
        try:
            self.inner.outbound_attempts += 1
            resp = self.inner.send(request, cred, c.request_timeout_s)
        except BaseException:   # timeouts, aborts, transport faults: the provider may have processed it, so it counts as spend
            self.governor.mark_unknown(res)
            raise
        status = resp.get("status") if isinstance(resp, dict) else None
        if status == 429:
            self.governor.mark_unknown(res)
            raise Denied("provider_quota_exceeded")
        if status == 401:
            self.governor.mark_unknown(res)
            raise Denied("invalid_credentials")
        if isinstance(status, int) and 500 <= status < 600:
            self.governor.mark_unknown(res)
            e = Denied("provider_server_error")
            e.retryable = True
            raise e
        if status != 200 or not isinstance(resp.get("body"), dict):
            self.governor.mark_unknown(res)
            raise Denied("unexpected_provider_response")
        usage = resp.get("usage")
        if self._cost_fn and isinstance(usage, dict) and "cost_usd" not in usage:
            derived = self._cost_fn(request, usage)   # locally derived from provider-reported units; never from the request alone
            if derived is not None:
                usage = {**usage, "cost_usd": str(derived)}
        self.governor.reconcile(res, usage)
        if cancel is not None and cancel.is_set():
            raise Denied("cancelled")   # too late to avoid the spend; it stays counted
        return resp

    def send_with_retry(self, request: dict, max_attempts: int = 2, cancel: threading.Event | None = None) -> dict:
        """Retries only timeouts and 5xx. EVERY attempt reserves budget again and re-runs every check; quota, auth, kill-switch and authorization failures never retry."""
        max_attempts = min(max(int(max_attempts), 1), 3)
        for attempt in range(max_attempts):
            try:
                return self.send(request, cancel)
            except TransportTimeout:
                if attempt + 1 >= max_attempts:
                    raise
            except Denied as e:
                if not getattr(e, "retryable", False) or attempt + 1 >= max_attempts:
                    raise
        raise Denied("retries_exhausted")
