# Known limitations (this candidate)

1. **Webcam initialisation failure is not classified.** Seven tests in `backend/tests/test_video_feeds15.py` (the `test_d1_*` group) describe a *desired* behaviour — a stable, non-sensitive HTTP 503 when the detector cannot be created — and **fail on this candidate**: any detector-initialisation failure currently yields a generic HTTP 500 (no session, lock or file is left behind). A proposed fix was independently reviewed and rejected, so it is not included. The tests are intentionally left unchanged rather than weakened.
   Failing ids: `test_d1_model_initialization_failure_is_a_stable_non_sensitive_503[file|import|memory|runtime|weights]`, `test_d1_failure_leaves_no_session_no_lock_no_files_and_no_cached_detector`, `test_d1_recovery_when_the_model_becomes_available`.
2. One test needing the private genuine response is skipped in this package (the file is not distributed).
3. Tests that load the real RF-DETR model require the weights (not distributed) and will error without them.
4. Browser acceptance for the camera uses a simulated canvas device only; no physical camera was exercised.
5. Accessibility: no screen-reader, forced-colors, reduced-motion or 200–400 % zoom verification.
6. Populated Dashboard/Feeds states were not exercised in browser acceptance (they require inference).
7. The top-bar "Experimental · not certified" chip is hidden below 1400 px wide; the full non-certification notice remains in the Overview banner and the footer of every page.
8. Authenticity of the genuine SAM response is not cryptographically provable (the provider offers no signature).
