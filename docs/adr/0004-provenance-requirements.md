# ADR-0004 — Provenance requirements (aligned in MG-02R)

Status: **PROPOSED** (aligned in MG-02R; not accepted — the owner must accept individually). Date: 2026-10-20.
Alignment record: aligned the weights policy with decision D-11, covered observation sets and evidence derivatives, replaced the reference to an unauthorized "CI" with an audit procedure (audit finding F-25).

## Context
The prototype must have documented provenance for code, models, weights, datasets, media, icons and fonts; avoid copyleft surprises; stay independent of any employer or other-project assets; and preserve an honest record relevant to competition eligibility. Open-source licensing does not establish patent, trademark or employer-IP clearance.

## Decision (proposed)
1. **No register entry, no use.** Every asset needs an entry in MG-PROVENANCE-001 before use (fields in §1 there), including observation sets, generator scripts, and evidence stills derived from source videos.
2. Third-party items require the license checklist (PROVENANCE §5). Strong copyleft (GPL/AGPL/SSPL) is not approved without owner review. Non-commercial or research-only weights are not shipped and are excluded from experiments unless the owner decides otherwise (D-11, recommended: excluded).
3. **Synthetic media is not original by assertion:** generation scripts, their dependencies, seeds, source assets and output hashes must be registered and verified (PROVENANCE §2).
4. **Cross-project reuse is an import.** Code or assets from any other project (Orbit, Compass, FinScope, employer) are not reusable without explicit owner approval and an entry; none are assumed available.
5. AI-assisted content is labeled as such in the register.
6. Dates and history are never altered or concealed. Any checkpoint mechanism (Git or otherwise) requires separate owner authorization (G-8 / D-06).
7. Re-review on version change; a lockfile-derived inventory must reconcile with the register through an audit procedure designed in MG-03 (no CI is assumed or authorized).

## Consequences
Overhead per dependency; blocks casual additions; supports later audit and an honest eligibility posture. A Markdown register is reconciled by procedure, not automatically.

## Alternatives considered
Informal tracking (not auditable); allowing "obviously permissive" libraries without entries (misses transitive and bundled-binary issues).

## Verification (intended, not yet demonstrated)
A-P0-13 register audit; lockfile-to-register reconciliation procedure (to be designed in MG-03).

## Open issues
G-1 (employer IP), G-3 (patents), G-6 (codecs), G-7 (AI-generated content copyright) remain OPEN.
