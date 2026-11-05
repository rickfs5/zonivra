# MG-VS01 — Dependency BOM, Licence Gate Results and Provenance

Status: PROPOSED (Night Run 01, vertical slice VS-01; licence evidence section 6 added in Night Run 02). Date: 2026-10-24.
Nothing here is legal, patent, trademark or licence clearance. Open-source licensing is not patent clearance. Gates G-1..G-9 in `MG-GATES-001` remain as recorded there, except where this document states otherwise.
Evidence labels follow `docs/README.md`. Licence classes come from package metadata unless a row says the licence text was read.

## 1. Licence gate results (the findings that shaped the build)
| # | Finding | Label | Disposition |
|---|---|---|---|
| L-1 | The `opencv-python-headless` macOS arm64 wheels bundle an FFmpeg built with `--enable-gpl --enable-version3 --enable-libx264 --enable-libx265` (read from the `libavcodec` binary of 5.0.0.93; for 4.14.0.94 the same bundled library set, including `libx264` and `libx265`, was observed but its FFmpeg configure string was not recovered). The wheel's own notice describes the FFmpeg as LGPL. | CONFIRMED | **OpenCV is not installed.** No OpenCV wheel on this platform passed the gate. |
| L-2 | `supervision` 0.31.1 declares `av` (PyAV, bundled FFmpeg) as a dependency; 0.29.x requires non-headless `opencv-python`. | CONFIRMED | `supervision` 0.31.1 is installed and PyAV is **removed**. `supervision` 0.31.1 has a pure-NumPy fallback when OpenCV is absent (it prints a warning). The freeze file must be installed with `--no-deps` (a plain install pulls `av` back; a test fails if `av` or `cv2` is importable). |
| L-3 | Decoder: the macOS system decoder (AVFoundation through PyObjC 12.2.2, MIT) replaces OpenCV/FFmpeg. | CONFIRMED (works) / **RATIFIED by the owner for the prototype (AP-31, Night Run 03)** | PyObjC was not on the Night Run 01 allowlist; it was added because every allowlisted decoder path fails the gate, and the owner has since ratified it as the local macOS decoder. No new decoder or codec libraries are authorized. Codec/patent exposure of the OS decoder remains open gate G-6. |
| L-4 | MPL-2.0 components: `certifi` 2026.10.23 and `tqdm` 4.70.1 (Python, transitive of `requests`/`rfdetr`/`transformers`); `lightningcss` 1.33.0 and `lightningcss-darwin-arm64` 1.33.0 (npm, build-time transitive of Vite). | CONFIRMED | **Owner decision AP-32 (Night Run 03): continued LOCAL, non-distributed use approved**, unmodified, subject to the obligations in section 6. No approval to redistribute binaries or publish the application. |
| L-5 | RF-DETR Nano weights: Apache-2.0 according to the upstream README model table; `rfdetr_plus` and the PML 1.0 variants are not used. | CONFIRMED (README read) / UNVERIFIED (no separate weights licence file exists in the checkpoint) | Used under Night Run authorization; conditions in section 3. |
| L-6 | `ultralytics`, `rfdetr_plus`, YOLOX weights: not installed, not downloaded. | CONFIRMED | — |

## 2. Python environment (`backend/requirements.freeze.txt`, Python 3.13.16, macOS 27.0.1 arm64)
Direct dependencies: `backend/requirements.in`. Complete closure: `backend/requirements.freeze.txt` (install with `--no-deps`). Hash-pinned lockfiles were not generated.
Licence text files were present in the installed distributions; each text was not individually read.

| Package | Version | License (package metadata) |
|---|---|---|
| annotated-doc | 0.0.5 | MIT |
| annotated-types | 0.8.0 | MIT |
| anyio | 4.15.1 | MIT |
| certifi | 2026.10.23 | Mozilla Public License 2.0 (MPL 2.0) **MPL-2.0 (owner decision)** |
| charset-normalizer | 3.5.2 | MIT |
| click | 8.5.0 | BSD-3-Clause |
| contourpy | 1.4.0 | BSD-3-Clause |
| cycler | 0.12.1 | BSD License |
| defusedxml | 0.7.1 | Python Software Foundation License |
| fastapi | 0.162.0 | MIT |
| filelock | 4.2.4 | MIT |
| fonttools | 4.67.0 | MIT |
| fsspec | 2026.9.0 | BSD-3-Clause |
| h11 | 0.16.0 | MIT License |
| hf-xet | 1.7.0 | Apache-2.0 |
| httpcore | 1.0.9 | BSD-3-Clause |
| httpcore2 | 2.16.0 | BSD-3-Clause |
| httpx | 0.28.1 | BSD License |
| httpx2 | 2.16.0 | BSD-3-Clause |
| huggingface_hub | 2.3.0 | Apache Software License |
| idna | 3.20 | BSD-3-Clause |
| iniconfig | 2.3.1 | MIT |
| Jinja2 | 3.1.6 | BSD License |
| kiwisolver | 1.5.1 | BSD License |
| markdown-it-py | 4.2.0 | MIT License |
| MarkupSafe | 3.0.4 | BSD-3-Clause |
| matplotlib | 3.11.2 | Python Software Foundation License |
| mdurl | 0.1.2 | MIT License |
| mpmath | 1.3.0 | BSD License |
| networkx | 3.7 | BSD-3-Clause |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| opentelemetry-api | 1.45.1 | Apache-2.0 |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| pillow | 13.0.0 | MIT-CMU |
| pluggy | 1.7.0 | MIT License |
| pydantic | 2.14.1 | MIT |
| pydantic_core | 2.50.1 | MIT |
| pyDeprecate | 0.9.0 | Apache-2.0 |
| Pygments | 2.21.0 | BSD-2-Clause |
| pyobjc-core | 12.2.2 | MIT |
| pyobjc-framework-AVFoundation | 12.2.2 | MIT |
| pyobjc-framework-Cocoa | 12.2.2 | MIT |
| pyobjc-framework-CoreAudio | 12.2.2 | MIT |
| pyobjc-framework-CoreMedia | 12.2.2 | MIT |
| pyobjc-framework-Quartz | 12.2.2 | MIT |
| pyparsing | 3.3.3 | MIT |
| pytest | 9.1.1 | MIT |
| python-dateutil | 3.0.0 | BSD License, Apache Software License |
| PyYAML | 6.0.3 | MIT License |
| regex | 2026.9.29 | Apache-2.0 AND CNRI-Python |
| requests | 2.34.2 | Apache Software License |
| rfdetr | 1.11.2 | Apache Software License |
| rich | 15.0.0 | MIT License |
| safetensors | 0.8.0 | Apache Software License |
| scipy | 1.18.1 | BSD License |
| setuptools | 84.2.1 | MIT |
| shellingham | 1.5.4 | ISC License (ISCL) |
| six | 1.17.0 | MIT License |
| starlette | 1.9.0 | BSD-3-Clause |
| supervision | 0.31.1 | MIT |
| sympy | 1.14.0 | BSD License |
| tokenizers | 0.23.3 | Apache Software License |
| torch | 2.14.1 | Apache-2.0 AND Apache-2.0 WITH LLVM-exception AND BSD-2-Clause AND BSD-3-Clause AND BSL-1.0 AND MIT |
| torchvision | 0.29.1 | BSD |
| tqdm | 4.70.1 | MPL-2.0 AND MIT **MPL-2.0 (owner decision)** |
| transformers | 5.20.2 | Apache 2.0 License |
| truststore | 0.10.5 | MIT |
| typer | 0.27.3 | MIT |
| typing-inspection | 0.4.4 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| tzdata | 2026.5 | Apache-2.0 |
| urllib3 | 2.8.0 | MIT |
| uvicorn | 0.54.0 | BSD-3-Clause |

## 3. Model, weights and media
| Item | Value |
|---|---|
| Model | RF-DETR Nano (`rfdetr==1.11.2`, class `RFDETRNano`), COCO-pretrained, 384 px input |
| Checkpoint file | `models/rf-detr-nano.pth` (git-ignored), 366 287 238 bytes |
| SHA-256 | `d8d6b9ee57d4d0ed2b1f305163624712a0532cb7bce0c747317984fc5457440d` (recorded in `models/MANIFEST.json`; verified before every load) |
| MD5 | `fb6504cce7fbdc783f7a46991f07639f` (matches the value shipped in `rfdetr/assets/model_weights.py` and the bucket ETag) |
| Download origin | `https://storage.googleapis.com/rfdetr/nano_coco/checkpoint_best_regular.pth`, retrieved 2026-10-24T06:52:38Z |
| Weights licence evidence | Upstream README (`roboflow/rf-detr`, branch `develop`): RF-DETR-N is listed Apache 2.0; PML 1.0 applies to the Plus models only. Upstream `LICENSE` is Apache-2.0 (read). |
| Backbone | DINOv2 vision transformer. `facebookresearch/dinov2` README: code and model weights Apache-2.0 (the non-commercial licences in that repository apply to its X-ray and Cell variants). Which DINOv2 initialization RF-DETR Nano used is UNVERIFIED; the whole checkpoint is published by Roboflow. |
| Training data | COCO. Image and annotation terms were not reviewed (OPEN). |
| Runtime download guard | `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`; the detector refuses to start unless the file exists and its SHA-256 matches; an in-process socket guard refuses non-loopback connections made by Python code inside the server process (a test covers it; it is not operating-system network isolation and does not cover Chrome or other processes). Inference was also run under `sandbox-exec` with `deny network*`. |
| Video | `test-assets/pexels_6565442.mp4` (git-ignored), 68 995 289 bytes, SHA-256 `7e46386505f9dd8bf1221e3eaba4eb7cd5a99ddc9ebd00f9aaef4a028cf4e3f6` |
| Video source | https://www.pexels.com/video/a-back-hoe-excavator-putting-soil-in-a-truck-6565442/ — creator Rhys Abel — Pexels licence https://www.pexels.com/license/ (free to use and modify; no attribution required; redistribution on other stock platforms prohibited) — retrieved 2026-10-24 through the official `pexels.com/download/video/6565442/` redirect (the largest rendition was served; no smaller one was offered) |
| Video media facts (decoder-measured) | H.264 (`avc1`), 3840×2160, 29.97 fps (time base 1/30000, 1001 ticks per frame), 24.758 s, 742 frames, strictly increasing PTS |
| Video content (observed) | Aerial quarry view of a dump truck being loaded; no workers confirmed. Person-class performance is **untested** on this clip. |

## 4. Frontend (`frontend/package.json`, exact pins, `package-lock.json`; installed with `--ignore-scripts`)
45 packages, 37 MIT, 4 Apache-2.0 (`@typescript/typescript-darwin-arm64`, `detect-libc`, `expect-type`, `typescript`), 1 ISC (`picocolors`), 1 BSD-3-Clause (`source-map-js`), 2 MPL-2.0 (`lightningcss`, `lightningcss-darwin-arm64`; see L-4). Direct: react 19.3.1, react-dom 19.3.1, vite 8.3.5, @vitejs/plugin-react 6.1.2, typescript 7.0.2, vitest 5.0.5, @types/react 19.3.0, @types/react-dom 19.3.0.

## 5. Open items
1. ~~Owner decision on the MPL-2.0 components (L-4) and ratification of PyObjC (L-3).~~ Decided under Night Run 03 (AP-31, AP-32); redistribution or publication would need a new decision. PyObjC licence files are missing from 4 of its 6 distributions (section 6): relevant only if anything is ever redistributed.
2. G-4: licence review of the COCO training data and a primary-source weights licence statement.
3. G-6: codec exposure of the OS H.264 decoder (decode only, no redistribution).
4. Patent freedom-to-operate: not assessed.
5. Hash-pinned dependency lockfile (`uv pip compile --generate-hashes`) not produced.

## 6. Licence evidence read from the installed files (Night Run 02)
Method: the licence files shipped inside the installed distributions were opened and read, their SHA-256 recorded, and the result compared with the classification taken from package metadata (the "scanner" classification used in sections 2 and 4). This is an engineering reading of the texts, not legal advice and not a clearance. The obligations below are the ones the texts themselves state; whether a given use triggers them is a question for the owner and counsel.

| Package | Scanner classification | File read (SHA-256 prefix) | What the file actually says | Reconciliation |
|---|---|---|---|---|
| certifi 2026.10.23 | MPL-2.0 | `certifi-2026.10.23.dist-info/licenses/LICENSE`, 989 bytes (`e93716da6b9c0d5a`) | A notice, not the licence text: the bundled `cacert.pem` is derived from Mozilla's `certdata.txt`, and the MPL 2.0 header (v. 2.0, `mozilla.org/MPL/2.0/`) applies. The full MPL text is not included in the distribution. | Consistent with the scanner (MPL-2.0). The covered material is the CA bundle data and the package that carries it; both are used unmodified. |
| tqdm 4.70.1 | MPL-2.0 AND MIT | `tqdm-4.70.1.dist-info/licenses/LICENCE` (`fcff87c3a47ce8028a`) | MIT is the stated default; "files: *" are MPL-2.0 (2015-2026, Casper da Costa-Luis); `tqdm/_tqdm.py` is MIT (Google Inc. 2016, Noam Yorav-Raphael 2013). Contains the MPL Exhibit A pointer and the MIT text; the full MPL text is not included. | Consistent with the scanner (mixed). Practically the package is MPL-2.0 plus MIT notices. Used unmodified. |
| lightningcss 1.33.0 and lightningcss-darwin-arm64 1.33.0 (npm) | MPL-2.0 | `node_modules/lightningcss/LICENSE` and the identical file in `lightningcss-darwin-arm64` (`5eba353fe5076ac3`) | Full MPL-2.0 text; both `package.json` declare `"license": "MPL-2.0"`. | Consistent. Vite's CSS tooling (dependency of Vite 8, not imported by the application). The built `frontend/dist/assets/*.css` contains only the custom-property names `--lightningcss-light` / `--lightningcss-dark` that the tool emits for `light-dark()` handling; no lightningcss source or binary is in `frontend/dist`. |
| PyObjC 12.2.2 (`pyobjc-core`, `-framework-AVFoundation`, `-Cocoa`, `-CoreAudio`, `-CoreMedia`, `-Quartz`) | MIT | `pyobjc_framework_cocoa-12.2.2.dist-info/licenses/LICENSE.txt` and the identical file in the Quartz distribution, 1 249 bytes (`0ca04b07928d4872`) | MIT; copyright 2002-2025 Ronald Oussoren and others; a parenthetical states that libffi-src is a separate product with its own licence. | MIT confirmed. **Gap:** only 2 of the 6 installed PyObjC distributions carry a licence file; `pyobjc-core`, `-AVFoundation`, `-CoreAudio` and `-CoreMedia` declare MIT in metadata only. `objc/_objc*.so` links the operating system's `/usr/lib/libffi.dylib`; no libffi source or binary is bundled in the wheel. PyObjC ratified by the owner (AP-31). |

Distribution obligations as the texts state them (nothing is distributed today, and no MPL-covered file is modified):
- MPL-2.0 sections 3.1 and 3.2 apply when Covered Software is distributed: recipients must be told that the Source Code Form is governed by the MPL and how to obtain it and the licence; in Executable Form the Source Code Form must also be made available by reasonable means at no more than the cost of distribution. Section 3.3 allows a Larger Work under other terms provided the MPL requirements are met for the covered parts. Section 3.4 requires keeping the licence notices.
- MIT (PyObjC; part of tqdm): keep the copyright and permission notice in copies or substantial portions.
- Conclusion for the current posture (local use, unmodified, nothing redistributed): no distribution obligation is triggered by the use recorded here. If the application, a virtual environment or `node_modules` were ever distributed, each of the items above would have to be re-evaluated and the notice files bundled. Owner decisions recorded in Night Run 03: local, non-distributed use of the MPL-2.0 components approved (AP-32); PyObjC ratified (AP-31).
- Not claimed: patent freedom-to-operate for any component, clearance of the COCO data terms (G-4), or clearance of codec exposure through the OS decoder (G-6).
- OpenCV and PyAV exclusions are unchanged and are enforced by `backend/tests/test_adapter_policy.py`. No package, weight or model was added in Night Run 02; `backend/requirements.freeze.txt` is unchanged.

## VS-02 (MVP) dependency and model provenance changes
- **No new Python or npm dependency, no new model, no new weights.** The tracker is an original pure-Python IoU tracker (`backend/mineguard/risk/tracker.py`); `supervision` 0.31.1 contains no ByteTrack and is not used for tracking. The Y4M test source (`e2e/make_fake_camera.py`) uses the existing `Pillow`, `numpy` and the project's own decoder.
- RF-DETR Nano weights and their SHA-256 are unchanged; every session re-hashes the executing weights during deep verification.
- Chrome (already used for the browser acceptance) is started with its FAKE capture flags for the webcam suite: a controlled test source, not a physical camera.
- Licence status of the existing components is unchanged and no clearance is claimed (gates remain open in the private gate record).
