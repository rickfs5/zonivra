# License matrix (provisional; not legal advice or clearance)

| Item | Rights / license | Notes |
|---|---|---|
| Original Zonivra / MineGuard source | **ALL RIGHTS RESERVED** (see `LICENSE`) | Provisional rights notice only. It grants no evaluation right; the owner must decide on an evaluation license or written permission. Ownership vs employer/contractors is unconfirmed |
| Python dependencies (73, `sbom/SBOM_backend_python.cdx.json`, `LICENSE_INVENTORY_python.csv`) | Per package, from installed metadata: mostly MIT / BSD / Apache-2.0; one MPL-2.0 | Metadata as declared, not independently verified; no hashes in the SBOM; the freeze must be installed with `--no-deps` (documented in the file) |
| Frontend dependencies (3 production, 45 incl. dev) | MIT, Apache-2.0, MPL-2.0, ISC, BSD-3-Clause | CycloneDX from `npm sbom` |
| RF-DETR code / weights | Apache-2.0 claims recorded in `models/MANIFEST.json` and `THIRD_PARTY_NOTICES.md` | Weights **not** redistributed; COCO data terms not independently reviewed |
| Bundled synthetic SAM-style fixture and synthetic truck image | Project-generated | Labelled `SYNTHETIC_FIXTURE` |
| Genuine SAM 3.1 response, authorization/credentials, personal media, stock footage, Higgsfield assets, demo videos | **Not included** | |
SBOM status: **PARTIAL** — see the README of the delivery report for gaps (no hashes; transitive closure limited to the freeze/lock files; licenses as declared).
