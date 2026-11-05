#!/usr/bin/env python3
"""OFFLINE builder of the first-call SAM 3.1 approval package for ONE image and ONE concept. It performs NO network access, reads NO environment variable, credential or key, creates NO
transport and cannot send anything. It prints: the exact request body, its digest (the governor's own `raw_digest`), the endpoint and headers (redacted), the cost estimate with its source,
the image facts (if a local file is given) and a BLANK owner-authorization record whose confirmations are all false. Usage:
  python3 scripts/sam31_package.py --image-url https://<host>/<random-path>.png --concept person [--image docs/release/sam_probe/sam_probe_person_640x480.png]
Status: PROPOSED. Approving the package is a human decision and starts nothing."""
import argparse, json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
from mineguard import sam31

ap = argparse.ArgumentParser()
ap.add_argument("--image-url", required=True)
ap.add_argument("--concept", default="person")
ap.add_argument("--image", default=None, help="the local file that will be hosted at --image-url (checked for format, size and dimensions only)")
a = ap.parse_args()
try:
    pv = sam31.preview(a.image_url, a.concept)
except sam31.Refused as e:
    sys.exit(f"REFUSED: {e.code}")
facts = None
if a.image:
    with open(a.image, "rb") as fh:
        facts = sam31.image_facts(fh.read())
record = {"approved_by": "", "approved_utc": "", "expires_utc": "", "body_digest": pv["body_digest"], "max_calls": 1, "max_usd": "0.01", "retries": 0,
          "provider_side_limit_confirmed": False, "key_is_dedicated": False, "revocation_planned": False, "image_rights_confirmed": False, "image_url_is_nonpersonal": False,
          "image_url_expires_or_is_public_by_design": False, "provider": sam31.PROVIDER_ID, "model": sam31.MODEL}
out = {"status": "PROPOSED_FOR_HUMAN_APPROVAL_NOT_EXECUTED", "request": pv, "image": facts, "authorization_record_template": record,
       "separate_approvals_required": ["1 the concrete transmission (this body, this URL, this concept)", "2 the financial risk accepted (provider-side limit UNKNOWN; local limit not guaranteed)",
                                       "3 the dedicated credential and its revocation", "4 the temporary activation of egress (only api.meta.ai:443, only for the single attempt)", "5 the maximum number of attempts (1, retries 0)"],
       "problems_with_the_record_if_submitted_as_is": sam31.check_owner_authorization(record, pv["body_digest"], pv["estimate"]["with_margin_usd"], "1970-01-01T00:00:00Z"),
       "note": "No network was used and nothing was sent. The record above is a blank template: every confirmation is false until the owner states it truthfully."}
print(json.dumps(out, indent=1, sort_keys=True))
