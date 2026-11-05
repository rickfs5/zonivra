# Install and reproduce

## Verified platform boundary

The MP4 path was verified on macOS with Python 3.13 and Apple silicon. Linux and Windows are not supported for the AVFoundation decoder. CPU-only performance has not been characterized.

## Install

```sh
uv venv --python 3.13 .venv
uv pip install --no-deps --python .venv/bin/python -r backend/requirements.freeze.txt
(cd frontend && npm ci --ignore-scripts && npm run build)
```

`--no-deps` is required because the freeze file is the reviewed closure. Initial installation may require network access; an offline installation needs a pre-populated package cache or wheel archive.

## Start an isolated local demo

```sh
MG_DEMO_PORT=8790 bash scripts/demo_v07.sh
```

The launcher uses isolated temporary storage, binds to `127.0.0.1`, prints the local URL, and removes its storage after shutdown.

## Reproduce without weights or private evidence

Open Settings → External provider results → Load synthetic demo. Confirm:

- `SYNTHETIC_FIXTURE` provenance;
- provider/model labels are descriptive, not proof of a live call;
- segment remains `UNCLASSIFIED`;
- no confidence or Risk Kernel result is created;
- mask structure is checked but pixels are not rendered;
- provider status still says Meta live transport is disabled.

The deterministic RED drill can be exercised separately and remains labelled synthetic.

## Optional local RF-DETR path

Supply the exact weight file described in `models/MANIFEST.json` and an MP4 you are entitled to use. The repository does not include either. Do not interpret successful execution as an accuracy or safety validation.

## Focused offline checks

```sh
.venv/bin/python -m pytest -q -p no:cacheprovider \
  backend/tests/test_external_import.py \
  backend/tests/test_meta_invariants.py
(cd frontend && npm run test && npm run typecheck && npm run build)
```

The optional test using the private genuine response is skipped unless `MG_REAL_SSE_RESPONSE` and `MG_REAL_SSE_IMAGE` point to separately authorized local files.

