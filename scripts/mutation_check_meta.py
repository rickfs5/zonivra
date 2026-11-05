import shutil, subprocess, sys, pathlib
"""Mutation check of the inert Meta transport and clients (sam31, meta_obs, meta_pipeline, metahttp, metaclient, muse_open). Usage: python scripts/mutation_check_unified.py <empty scratch dir>.
Works on PRIVATE copies; never edits the worktree. The UNMUTATED copy must pass first (otherwise every failure would look like a kill). A survivor, an invalid or an unapplied mutant exits nonzero."""
ROOT = pathlib.Path(__file__).resolve().parents[1]
WORK = pathlib.Path(sys.argv[1]) / "mut"
PY = str(ROOT / ".venv/bin/python")
TESTS = ["test_meta_sam31.py", "test_meta_pipeline.py", "test_meta_transport.py", "test_muse_open.py", "test_meta_invariants.py", "test_meta_provenance.py", "test_meta_containment.py", "test_unified_explain_unit.py"]
M = [
 ("S01 request item loses the documented type=message", "sam31.py", '"input": [{"type": "message", "role": "user", "content"', '"input": [{"role": "user", "content"'),
 ("S02 include_confidence sent as a boolean", "sam31.py", 'ALLOWED_METADATA = {"include_confidence": "true",', 'ALLOWED_METADATA = {"include_confidence": True,'),
 ("S03 http image URLs accepted", "sam31.py", '    if u.scheme != "https" or not u.hostname:\n        raise Refused("url_not_https")', '    if False:\n        raise Refused("url_not_https")'),
 ("S04 credentials inside the URL accepted", "sam31.py", '    if u.username or u.password:\n        raise Refused("url_has_userinfo")', ''),
 ("S05 signed URL (query) accepted", "sam31.py", '    if u.query or u.fragment:\n        raise Refused("url_has_query_or_fragment")', ''),
 ("S06 private/loopback IP accepted", "sam31.py", '    if ip is not None and not ip.is_global:', '    if False:'),
 ("S07 mask optional in a record", "sam31.py", '        if not mm:\n            raise Refused("mask_missing_or_outside_documented_grammar")', '        if False:\n            raise Refused("x")'),
 ("S08 incomplete stream accepted", "sam31.py", '    if not out["completed"]:', '    if False:'),
 ("S09 failed/error event ignored", "sam31.py", '    if out["failed"]:', '    if False:'),
 ("S10 box outside the image accepted", "sam31.py", 'if not (0 <= b["x1"] <= b["x2"] <= width and 0 <= b["y1"] <= b["y2"] <= height):', 'if False:'),
 ("S11 frame size disagreeing with the source accepted", "sam31.py", 'if (b["w"], b["h"]) != (width, height):', 'if False:'),
 ("S12 duplicate object ids accepted", "sam31.py", '            if len(set(ids)) != len(ids):', '            if False:'),
 ("S13 authorization: retries not checked", "sam31.py", 'record["retries"] != RETRIES_FIRST_LIVE or isinstance(record["retries"], bool)', 'False'),
 ("S14 authorization: body digest not compared", "sam31.py", 'if record["body_digest"] != body_digest:', 'if False:'),
 ("S15 authorization: more than one call allowed", "sam31.py", 'if record["max_calls"] != MAX_CALLS_FIRST_LIVE or isinstance(record["max_calls"], bool):', 'if False:'),
 ("S16 sam31 digest differs from the governor's digest", "sam31.py", '    return raw_digest(obj)', '    return hashlib.sha256(canonical(obj).encode()).hexdigest()'),
 ("S17 governed request loses the token estimate", "sam31.py", '"units": 1, "est_tokens": 0, "body"', '"units": 1, "body"'),
 ("O02 object ids of different responses collide", "meta_obs.py", '(f"{id_prefix}:{r[\'object_id\']}" if id_prefix else r["object_id"])', 'r["object_id"]'),
 ("O03 confidence fabricated when absent", "meta_obs.py", 'conf = ({"permille": None, "kind": "not_applicable"} if score is None else', 'conf = ({"permille": 900, "kind": "model_uncalibrated"} if score is None else'),
 ("O04 real provider response labelled as simulated even with a receipt", "meta_obs.py", '"origin": ORIGIN_REAL if real else ORIGIN, "simulation": not real,', '"origin": ORIGIN, "simulation": True,'),
 ("P01 an unparsed concept response leaves the frames empty instead of unknown", "meta_pipeline.py", 'by[i]["error"] = {"code": "concept_missing_for_frame"}', 'pass'),
 ("P02 absence of detections reported as safe", "meta_pipeline.py", '"absence_is_not_safety": bool(n_unknown or not all_objects),', '"absence_is_not_safety": False,'),
 ("P03 evidence attributed to the local detector", "meta_pipeline.py", '"attributed_to_local_detector": False', '"attributed_to_local_detector": True'),
 ("P04 provider decides severity", "meta_pipeline.py", 'rec = {"schema": SCHEMA, "decided_by": "deterministic_policy"', 'rec = {"schema": SCHEMA, "decided_by": "provider"'),
 ("H02 plain http accepted for any host", "metahttp.py", 'if u.scheme == "http" and not (allow_loopback_http and loop):', 'if False:'),
 ("H03 the credential is not redacted from errors", "metahttp.py", '        t = t.replace(secret, "[redacted]")', '        pass'),
 ("H05 response size unbounded", "metahttp.py", 'while len(raw) <= MAX_RESPONSE_BYTES:', 'while True:'),
 ("H06 path traversal accepted", "metahttp.py", ' or ".." in path or "//" in path:', ':'),
 ("H07 malformed SSE tolerated", "metahttp.py", '            return None\n        if not isinstance(ev, dict):\n            return None', '            continue\n        if not isinstance(ev, dict):\n            continue'),
 ("C02 the first call retries", "metaclient.py", 'resp = self.guarded.send_with_retry(req, max_attempts=1, cancel=cancel)\n        except (Denied, TransportTimeout, TransportFault) as e:\n            return {**classify(e, self.guarded.inner), "evidence": None', 'resp = self.guarded.send_with_retry(req, max_attempts=3, cancel=cancel)\n        except (Denied, TransportTimeout, TransportFault) as e:\n            return {**classify(e, self.guarded.inner), "evidence": None'),
 ("C03 a denial is not counted as spend when the provider answered 429", "metaclient.py", '"spend_counted": exc.code in ("provider_quota_exceeded", "invalid_credentials", "provider_server_error", "unexpected_provider_response"),', '"spend_counted": False,'),
 ("M01 'video inspected' text accepted from the model", "muse_open.py", '    if explain._VIDEO.search(blob):', '    if False:'),
 ("M02 a restated different state accepted", "muse_open.py", '        if other != st and any(rx.search(x) for x in texts):', '        if False:'),
 ("M03 a mock endpoint counts as a real Muse call", "muse_open.py", '"real_muse_call": real,', '"real_muse_call": True,'),
 ("M04 a mock endpoint gets the real kind", "muse_open.py", '"kind": KIND_REAL if real else "SIMULATED_EXPLANATION"', '"kind": KIND_REAL'),
 ("M05 the model text may change the severity", "muse_open.py", '"severity": incident["risk"]["severity"], "incident_id"', '"severity": "GREEN", "incident_id"'),
 ("M06 unsupported response_format added to the body", "muse_open.py", 'body = {"model": model, "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user_content(view)}], "max_tokens": max_tokens}', 'body = {"model": model, "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user_content(view)}], "max_tokens": max_tokens, "response_format": {"type": "json_object"}}'),
 ("M07 free text beyond the size limit accepted", "muse_open.py", 'len(res) > MAX_SUMMARY or', 'False or'),
 ("M08 invented numbers accepted", "muse_open.py", 'if any(n not in explain._allowed_numbers(view) for n in explain._NUM.findall(blob)):', 'if False:'),
 ("P05 a concept that reported nothing leaves the other concept's frames analyzed", "meta_pipeline.py", 'if have < len(envs) or any(', 'if False and any('),
 ("V04 real evidence from several responses accepted", "meta_pipeline.py", '        if len(responses) != 1:', '        if False:'),
 ("F1b TLS evidence accepts a weakened context or another server name", "metahttp.py", '    if ctx.verify_mode != ssl.CERT_REQUIRED or not ctx.check_hostname or sock.server_hostname != host:', '    if False:'),
 ("F1c production 200 without verified TLS is returned", "metahttp.py", '        if tls is None:\n            raise TransportFault("tls_peer_not_verified")', '        if False:\n            raise TransportFault("x")'),
 ("F1d the production transport can be subclassed", "metahttp.py", '    def __init_subclass__(cls, **kw):\n        raise TypeError("ProductionHttpsTransport cannot be subclassed")', '    def __init_subclass__(cls, **kw):\n        pass'),
 ("F1e a test transport response may carry an attestation", "metaclient.py", '    if not prod and origin == "TEST" and att is None:', '    if not prod and origin == "TEST":'),
 ("F1g a client accepts a plain or foreign guard", "metaclient.py", '        if not isinstance(guarded, EndpointBoundGuard) or guarded.inner.provider != sam31.PROVIDER_ID:', '        if False:'),
 ("F2a attestation MAC not checked", "metahttp.py", '    if not hmac.compare_digest(att.mac, _mac(f)):\n        return ["mac_invalid"]', '    if False:\n        return ["mac_invalid"]'),
 ("F2b stage order/completeness not checked", "metahttp.py", '    if tuple(names) != STAGES or seqs != sorted(set(seqs)) or ts != sorted(ts):', '    if False:'),
 ("F2c stale attestations accepted", "metahttp.py", '    if not 0 <= now - att.issued_ns <= MAX_ATTESTATION_AGE_S * 1_000_000_000:', '    if False:'),
 ("F2d consumption never recorded (duplicate receipts)", "metahttp.py", '        _CONSUMED.add(red.attempt_id)', '        pass'),
 ("F2e ledger not consulted", "metahttp.py", '    p += _ledger_problems(att, governor)', '    pass'),
 ("F2f response digest not compared", "metahttp.py", '    if att.response_digest != raw_digest(payload):', '    if False:'),
 ("F2g request digest not compared", "metahttp.py", '        if att.request_digest != d:', '        if False:'),
 ("F2h request identity not compared", "metahttp.py", 'if not isinstance(request, dict) or att.provider != request.get("provider") or att.model != request.get("model") or att.request_key != request.get("request_key"):', 'if not isinstance(request, dict):'),
 ("F2i request body authorization not required", "metahttp.py", '        if d not in governor.config.authorized_payload_digests:', '        if False:'),
 ("F2k real evidence without an attestation", "meta_pipeline.py", '        if attestation is None or request is None or governor is None:', '        if False:'),
 ("F2l TLS hostname not compared", "metahttp.py", '    if not (isinstance(att.tls, dict) and att.tls.get("peer_verified") is True and att.tls.get("hostname") == att.endpoint[0]):', '    if False:'),
 ("F2o tampered ledger tolerated", "metahttp.py", '        return ["ledger_unreadable_or_tampered"]', '        return []'),
 ("F3g destination attributes mutable after construction", "metahttp.py", '        if getattr(self, "_frozen", False) and k not in self._MUTABLE:', '        if False:'),
 ("F4a no credential validation at all", "metahttp.py", '    if not isinstance(c, str):\n        return ["not_a_string"]', '    return []'),
 ("F4b whitespace in a credential accepted", "metahttp.py", '    if re.search(r"\\s", c):\n        p.append("whitespace")', '    if False:\n        p.append("whitespace")'),
 ("F4c non-ASCII credential accepted", "metahttp.py", '    if not c.isascii():', '    if False:'),
 ("F4d control characters accepted", "metahttp.py", '    if re.search(r"[\\x00-\\x1f\\x7f]", c):\n        p.append("control_character")', '    if False:\n        p.append("control_character")'),
 ("F4e token68 syntax not enforced", "metahttp.py", '    if not p and not _TOKEN68.fullmatch(c):', '    if False:'),
 ("F4f length limits not enforced", "metahttp.py", '    if len(c) < CRED_MIN:', '    if False:'),
 ("F4g raw gate ignores the credential format", "metahttp.py", 'raw_digest(body) not in c.authorized_payload_digests or credential_problems(credentials):', 'raw_digest(body) not in c.authorized_payload_digests:'),
 ("F4h the guard passes an invalid credential through", "metahttp.py", '        return c if not self.last_credential_problems else ""', '        return c'),
 ("H10 a response echoing the credential is returned", "metahttp.py", '    if credentials and credentials in text:', '    if False:'),
 ("F1a patched connection primitives not detected", "metahttp.py", '        if strict and (http_mod.HTTPSConnection is not https_conn or ssl_mod.create_default_context is not create_ctx):', '        if False:'),
 ("F1i no pending claim required before a connection exists", "metahttp.py", '    _require_claim(core, trip)                       # no pending claim from the guard -> refused BEFORE any connection exists', '    pass'),
 ("F2p a redemption is not bound to the response", "metahttp.py", '    if red.response_digest != response_digest:', '    if False:'),
 ("F2q a redemption is not bound to the request", "metahttp.py", '    if request_digest is not None and red.request_digest != request_digest:', '    if False:'),
 ("F2r redemption MAC not checked", "metahttp.py", '    if not hmac.compare_digest(red.mac, hmac.new(_KEY, json.dumps(_rfields(red), sort_keys=True).encode(), hashlib.sha256).hexdigest()):', '    if False:'),
 ("F3a outer provider mismatch not refused", "metahttp.py", '    if request.get("provider") != core.provider:\n        raise Denied("provider_endpoint_mismatch")', '    if False:\n        raise Denied("provider_endpoint_mismatch")'),
 ("F3b model/payload binding not required", "metahttp.py", '    if trip not in core.authorized or request["body"].get("model") != request.get("model"):', '    if False:'),
 ("F3c binding exhaustion not enforced", "metahttp.py", '        if core._uses.get(trip, 0) + core._pending.get(trip, 0) >= core.max_uses:', '        if False:'),
 ("B2 pending claims not counted (check/use race)", "metahttp.py", 'core._uses.get(trip, 0) + core._pending.get(trip, 0) >= core.max_uses', 'core._uses.get(trip, 0) >= core.max_uses'),
 ("F3d uses never counted", "metahttp.py", '        core._uses[trip] = core._uses.get(trip, 0) + 1', '        pass'),
 ("F3e the guard takes no atomic claim", "metahttp.py", '        claim = _claim_impl(self.inner, request)                           # atomic, before the governor reserves any budget', '        claim = _Claim(self.inner, _precheck_impl(self.inner, request))'),
 ("F3f redirects handled like any other status", "metahttp.py", '    if 300 <= status < 400:', '    if False:'),
 ("F3h raw gate ignores the reservation provider", "metahttp.py", 'r.state == "reserved" and r.model_id == request.get("model") and r.provider == core.provider)', 'r.state == "reserved" and r.model_id == request.get("model"))'),
 ("B2b an unused claim is never released", "metahttp.py", '            _release_impl(claim)                                           # no-op once the request was handed to the connection', '            pass'),
 ("B3a stdlib debug logging not switched off", "metahttp.py", '            if hasattr(conn, "set_debuglevel"):\n                conn.set_debuglevel(0)', '            if False:\n                pass'),
 ("B3b a connection that keeps debug logging is used", "metahttp.py", '            if getattr(conn, "debuglevel", 0) != 0:', '            if False:'),
 ("F2m endpoint not compared to the provider endpoint", "metahttp.py", '    if not _endpoint_ok(att.provider, tuple(att.endpoint)):', '    if False:'),
 ("O01 a real label without a redemption accepted", "meta_obs.py", '    if not isinstance(redemption, metahttp.Redemption) or redemption.provider != PROVIDER_ID_REAL or redemption.model != MODEL_ID_REAL:', '    if False:'),
 ("R502a real label without consuming or binding the redemption", "meta_obs.py", '    bad = metahttp.use_redemption(redemption, response_digest=raw_digest({"events": events}), request_digest=raw_digest(request["body"]))', '    bad = []'),
 ("R502b the public normalizer accepts a non-simulated fixture", "meta_obs.py", '    if fixture["simulated"] is not True:\n        raise NormalizationRefused("real_label_not_accepted_here")', '    if False:\n        raise NormalizationRefused("real_label_not_accepted_here")'),
 ("R502c the redeemed request and concept are not matched", "meta_obs.py", '        ok = request["provider"] == PROVIDER_ID_REAL and request["model"] == MODEL_ID_REAL and text == concept', '        ok = True'),
 ("F2n Muse redemption for another provider or model accepted", "muse_open.py", '    if real and (not isinstance(redemption, metahttp.Redemption) or redemption.provider != PROVIDER_ID or redemption.model != model):', '    if real and not isinstance(redemption, metahttp.Redemption):'),
 ("R503a the Muse request digest is taken from the redemption instead of derived", "muse_open.py", '    request_digest = raw_digest(build_body(view, model, max_tokens))', '    request_digest = redemption.request_digest if redemption is not None else raw_digest(build_body(view, model, max_tokens))'),
 ("R501a the public endpoint view is a mutable dict", "metahttp.py", 'OFFICIAL_ENDPOINTS = types.MappingProxyType({row[0]: (row[1], row[2], row[3]) for row in _OFFICIAL_TABLE})', 'OFFICIAL_ENDPOINTS = {row[0]: (row[1], row[2], row[3]) for row in _OFFICIAL_TABLE}'),
 ("R501b the endpoint table is a mutable list", "metahttp.py", '_OFFICIAL_TABLE = (("meta_sam31", "api.meta.ai", "/v1", "/responses", True), ("baseten_muse_spark_open", "inference.baseten.co", "/v1", "/chat/completions", False))', '_OFFICIAL_TABLE = [["meta_sam31", "api.meta.ai", "/v1", "/responses", True], ["baseten_muse_spark_open", "inference.baseten.co", "/v1", "/chat/completions", False]]'),
 ("R501c the production constructor reads the module table at call time", "metahttp.py", '        row = rows.get(provider)', '        row = {r[0]: r for r in _OFFICIAL_TABLE}.get(provider)'),
 ("R501d the attestation endpoint check reads the mutable public view", "metahttp.py", '    captured = {r[0]: (r[1], r[2], r[3]) for r in table}             # private copy: not reachable from the module namespace\n    return lambda provider, endpoint: captured.get(provider) == tuple(endpoint)', '    return lambda provider, endpoint: OFFICIAL_ENDPOINTS.get(provider) == tuple(endpoint)'),
 ("R501e the dispatch closure does not check the destination", "metahttp.py", ' or not endpoint_ok(self.provider, (self.host, self.base_path, self.path)) or self.scheme != "https" or self.port is not None:', ':'),
 ("R504a Retry-After is not validated", "metahttp.py", '    if not isinstance(raw, str) or (credentials and credentials in raw) or not _RETRY_AFTER.fullmatch(raw):', '    if not isinstance(raw, str):'),
 ("R504c control characters are kept in error messages", "metahttp.py", '    return re.sub(r"[\\x00-\\x1f\\x7f]", " ", t)[:MAX_ERROR_CHARS]', '    return t[:MAX_ERROR_CHARS]'),
 ("R504d an unexpected exception message leaves the transport", "metahttp.py", '            raise TransportFault(f"unexpected_{type(e).__name__}"[:80]) from None', '            raise TransportFault(str(e)) from None'),
 ("F601a a credential echoed with JSON escapes in a 200 JSON document is returned", "metahttp.py", '        if fns.secret_in(doc, credentials):', '        if False:'),
 ("F601b a credential echoed with JSON escapes in SSE events is returned", "metahttp.py", '        if fns.secret_in(events, credentials):', '        if False:'),
 ("F601c a credential echoed with JSON escapes in an error body is accepted", "metahttp.py", '        if err_doc is not None and fns.secret_in(err_doc, credentials):', '        if False:'),
 ("F602a redeem consumes the attestation before the record is built", "metahttp.py", '    return _seal_redemption(f), []', '    _CONSUMED.add(att.attempt_id)\n    return _seal_redemption(f), []'),
 ("F602b the redemption is spent BEFORE the observations are built", "meta_obs.py", '    parsed = sam31.parse_stream(events, width, height, max_frames=len(pts_us) if isinstance(pts_us, list) else 1)', '    metahttp.use_redemption(redemption, response_digest=raw_digest({"events": events}), request_digest=raw_digest(request["body"]))\n    parsed = sam31.parse_stream(events, width, height, max_frames=len(pts_us) if isinstance(pts_us, list) else 1)'),
 ("F602c the Muse redemption is spent before the facts are validated", "muse_open.py", '    out = {"kind": KIND_REAL if real else "SIMULATED_EXPLANATION", "provider": PROVIDER_ID,', '    if real:\n        metahttp.use_redemption(redemption, response_digest=raw_digest(resp_body), request_digest=request_digest)\n    out = {"kind": KIND_REAL if real else "SIMULATED_EXPLANATION", "provider": PROVIDER_ID,'),
 ("H04 non-200 answers treated as success", "metahttp.py", '    if status != 200:\n        try:\n            err_doc = json.loads(text)', '    if False:\n        try:\n            err_doc = json.loads(text)'),
 ("F601d the secret scanner does not look at dictionary keys", "projection.py", '            if keys:\n                _strings(k, acc, depth + 1, keys)', '            if False:\n                _strings(k, acc, depth + 1, keys)'),
 ("G01 SAM events are returned as received instead of projected", "projection.py", '            out.append({"type": t, "delta": canonical_delta(idx, recs)})', '            out.append(ev)'),
 ("G03 an out-of-grammar delta is skipped instead of refusing the response", "projection.py", '            except sam31.Refused:\n                return None', '            except sam31.Refused:\n                continue'),
 ("G04 usage is not reduced to numeric allow-listed counters", "projection.py", '    return out or None', '    return dict(u) if u else None'),
 ("G05 the chat completion keeps every unknown top-level field", "projection.py", '    out = {"choices": [first]}', '    out = {**{k: v for k, v in doc.items() if k != "choices"}, "choices": [first]}'),
 ("G06 the chat message keeps reasoning and tool fields", "projection.py", '    first = {"message": {"content": content}}', '    first = {"message": ch[0].get("message")}'),
 ("G07 unknown SAM event types are preserved", "projection.py", '        elif t == "response.completed":\n            r = ev.get("response")', '        elif t != "response.completed":\n            out.append(ev)\n        elif t == "response.completed":\n            r = ev.get("response")'),
 ("L01 adjacent fragments are not joined", "projection.py", '    return any(text_leaks(sep.join(group), secret) for group in (acc, vals) for sep in ("", " "))', '    return False'),
 ("L02 percent-encoded text is not decoded", "projection.py", '    variants = {text, unquote(text)}', '    variants = {text}'),
 ("L03 base64 tokens are not decoded", "projection.py", '    for tok in re.findall(r"[A-Za-z0-9+/_-]{8,}={0,2}", text):', '    for tok in []:'),
 ("L04 JSON text inside a string is not decoded", "projection.py", '            if inner is not None:', '            if False:'),
 ("L05 punctuation-collapsed text is not checked", "projection.py", '    variants |= {_collapse(v) for v in list(variants)}', '    pass'),
 ("D01 SSE responses are not projected", "metahttp.py", '        body = fns.project(core.provider, events)', '        body = events'),
 ("D02 JSON responses are not projected", "metahttp.py", '        body = fns.project(core.provider, doc)', '        body = doc'),
 ("D03 the returned structure is not scanned", "metahttp.py", '    if fns.secret_in(body, credentials):', '    if False:'),
 ("D04 a response outside the contract is replaced by an empty body instead of refused", "metahttp.py", '    if body is None:\n        raise TransportFault("response_outside_contract")', '    if body is None:\n        body = [] if core.accept_sse else {}'),
 ("D05 a test transport accepts a provider/mode without a projector", "metahttp.py", '        if provider not in projection.PROVIDER_SSE or bool(accept_sse) != projection.PROVIDER_SSE[provider]:', '        if False:'),
 ("G02 failure events keep a provider-chosen code", "projection.py", '            out.append({"type": t, "code": code if code in ERROR_CODES else None})', '            out.append({"type": t, "code": code})'),
 ("N01 usage keys are not restricted to the documented counters", "projection.py", '    out = {k: v for k, v in u.items() if k in USAGE_KEYS and', '    out = {k: v for k, v in u.items() if isinstance(k, str) and'),
 ("N02 numbers are not scanned for spelling a credential", "projection.py", '    if numeric_leaks(obj, secret):\n        return True', '    if False:\n        return True'),
 ("N03 the SAM fields (ids, coordinates) are not scanned across records", "projection.py", '    for rx in _SAM_FIELDS:', '    for rx in ():'),
 ("N04 reversed spellings are not checked", "projection.py", '    return base + [b[::-1] for b in base]', '    return base'),
 ("N06 any provider string is returned as finish_reason", "projection.py", '    if fr in FINISH_REASONS:', '    if isinstance(fr, str):'),
 ("N07 SAM number bounds not enforced", "projection.py", '            if idx > MAX_FRAME or any(r["object_id"] > MAX_OBJECT_ID', '            if False and any(r["object_id"] > MAX_OBJECT_ID'),
 ("N08 the provider's own confidence digits are carried", "projection.py", '        cc = _canonical_confidence(r["confidence"])', '        cc = r["confidence"]'),
 ("N09 error type and code are not restricted to the documented sets", "metahttp.py", '                view[k] = v if v in allowed else None', '                view[k] = v'),
 ("N10 a test transport may be built for any host without a fake", "metahttp.py", '        if not (loop or connection_factory is not None):', '        if False:'),
 ("N11 the test transport opens a connection to a non-loopback host", "metahttp.py", '        if self.host not in _LOOPBACK and not _is_loopback_ip(self.host):', '        if False:'),
]
def prepare():
    if WORK.exists(): shutil.rmtree(WORK)
    (WORK / "backend" / "tests").mkdir(parents=True)
    shutil.copytree(ROOT / "backend" / "mineguard", WORK / "backend" / "mineguard", ignore=shutil.ignore_patterns("__pycache__"))
    for t in ["conftest.py", *TESTS]: shutil.copy(ROOT / "backend" / "tests" / t, WORK / "backend" / "tests" / t)
    shutil.copytree(ROOT / "backend" / "tests" / "fixtures_meta", WORK / "backend" / "tests" / "fixtures_meta")
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
