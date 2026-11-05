# Architecture

## Product path

1. An owner registers a local MP4 by content digest.
2. macOS AVFoundation decodes frames.
3. RF-DETR Nano performs local object detection when the expected weights are present.
4. Observations are stored with model, source, sampling, and evidence provenance.
5. Operator-defined zones and deterministic rules produce `GREEN`, `YELLOW`, `RED`, or `UNKNOWN` intervals.
6. Incidents expose a timeline, evidence, missing intervals, trust state, and export for human review.

The detector does not assign risk severity. Missing or disconnected evidence is never silently converted to green.

## External Meta result path

A separate offline importer accepts a saved response, manifest, and source image. It validates a closed observed response profile, hashes, sizes, image linkage, box bounds, and mask structure before storing an isolated review record. Imported SAM evidence does not enter RF-DETR observations or the Risk Kernel.

Native Meta transport remains disabled in this candidate.

## Trust boundaries

- Backend binds to loopback and applies a session token to state-changing routes.
- Canonical runs, synthetic drills, restored records, and external imports use different provenance classes.
- Provider-controlled errors are not evidence.
- Explanations are subordinate, simulated, and cannot mutate deterministic decisions.
- Private provider bytes and local run stores are excluded from the public candidate.

