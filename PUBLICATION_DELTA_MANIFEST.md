# Public documentation delta against accepted Release 32

**Base (immutable):** `zonivra-release-candidate-r32.tar.gz`, SHA-256 `99bc15bdb049e5e519007291680f2b3c8736c8f54a77ab63ff3509d969720535`; application source commit `afe4b396a01106c78a36b7d6b3557a86d18ce0d0`, tree `491dddb1c04bc72d5b84e59ec5b681ee1a64fec1`. `SOURCE_COMMIT.txt` is **not changed**.
**Declaration for the public repo:** "Accepted R32 application bytes plus a documentation-only publication delta." A repository HEAD created from this delta is a different Git commit/tree from `afe4b396`/`491dddb1`, which identify the application source only.

| Operation | Path in repo | SHA-256 before (R32) | SHA-256 after | Reason | Executable bytes changed? |
|---|---|---|---|---|---|
| MODIFIED_DOC | `README.md` | `10ede27c78152ae7d084a0132e66ace8cf006a5c1a359acff451f12b8064aaef` | `34573865543853a6bea47105cf6e4d981228ea298023f816e9fa5eca2561bb4b` | Full-product README: capabilities matrix, explicit pre-existing vs event split, rights section, no local paths | No (Markdown) |
| ADDED_DOC | `CONTRIBUTION_TIMELINE.md` | (absent) | `4aa87ef50d56dd56031691ed83a17182566a11cb1e47bb89207235adaaae916b` | Commit-based contribution table, corrected per CV-35 | No (Markdown) |
| ADDED_DOC (optional) | `PUBLICATION_DELTA_MANIFEST.md` | (absent) | = this manifest, public-safe copy | Declares base hash and delta | No (Markdown) |

Note: the timeline hash differs from CV-36's because two garbled sentence fragments in its evidence-base line were corrected at staging (wording only).

Everything else in R32 is `BASE`, unchanged (268 files, `SHA256SUMS.txt` of R32 still describes them; `README.md` is the only R32-listed file that changes, so its line in `SHA256SUMS.txt` becomes stale — document this or regenerate the file list in the repo, owner/builder decision).

## Notes
Internal planning documents, placeholders-bearing submission text, private Meta material, personal media, weights, and video/audio files are not part of this delta.
