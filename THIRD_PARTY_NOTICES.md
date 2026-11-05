# Third-party notices

This file is an engineering inventory, not legal advice or complete legal clearance. The candidate does not vendor dependency source trees, model weights, or third-party video.

## Runtime and development dependencies

Exact Python versions are pinned in `backend/requirements.freeze.txt`; exact frontend versions and transitive packages are pinned in `frontend/package-lock.json`.

Locally inspected package metadata for the pinned environment reported licenses including MIT, BSD-2-Clause, BSD-3-Clause, Apache-2.0, MPL-2.0, PSF/CNRI-Python, ISC, 0BSD, Zlib, CC0-1.0, BSL-1.0, and license combinations. Notable direct components:

| Component | Version | Recorded license metadata | Role |
|---|---:|---|---|
| RF-DETR | 1.11.2 | Apache License 2.0 | local detector library |
| PyTorch | 2.14.1 | Apache-2.0 plus bundled-component licenses | inference runtime |
| torchvision | 0.29.1 | BSD | vision utilities |
| FastAPI | 0.162.0 | MIT | backend framework |
| Uvicorn | 0.54.0 | BSD-3-Clause | local server |
| NumPy | 2.5.4 | BSD-3-Clause and bundled-component licenses | numeric runtime |
| Pillow | 13.0.0 | MIT-CMU | image handling |
| certifi | 2026.10.23 | MPL-2.0 | certificate bundle dependency |
| tqdm | 4.70.1 | MPL-2.0 AND MIT | progress utility |
| React / React DOM | 19.3.1 | MIT | user interface |
| Vite | 8.3.5 | MIT | frontend build |
| TypeScript | 7.0.2 | Apache-2.0 | frontend language tooling |
| Vitest | 5.0.5 | MIT | frontend tests |

Several Python packages expose long-form or incomplete license metadata rather than a normalized SPDX expression. Before publication, the owner should generate and retain a complete software bill of materials with the license text supplied by every installed package. This candidate does not relicense any dependency.

## RF-DETR weights and training data

`models/MANIFEST.json` records the expected RF-DETR Nano weight hash and upstream source. The 366 MB weight file is not included. Upstream documentation records Apache-2.0 for the relevant model/code, but COCO training-data terms and patent clearance were not independently resolved here.

## Meta

No Meta model, provider SDK, credential, private response binary, or authorization material is distributed. The bundled SAM result is a synthetic fixture. The genuine response referenced in the demo was produced by one owner-operated external call and remains private.

## Media and audiovisual assets

No third-party stock video, personal webcam recording, Higgsfield media, or final demo video is included in this source candidate. Those assets require separate provenance and distribution approval.

