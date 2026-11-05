# ADR-0002 — Deterministic risk evaluation with four separate states (amended in MG-02R)

Status: **PROPOSED** (amended in MG-02R; not accepted — the owner must accept individually). Date: 2026-10-20.
Amendment record: replaces the MG-02 wording "no hidden state" (timers are state), removes the reference to an undefined "required standard", defines the priority and incomplete-input rule, adopts named built-in rules, per-track dwell, artifact separation and the engine/rule version contract (audit findings F-01, F-03, F-05, F-08, F-20).

## Context
A core differentiation hypothesis (UNVERIFIED as differentiating) is that AI observations must not decide safety outcomes and that results are reproducible. Uncalibrated video cannot support physical-distance or collision claims. Missing or ambiguous observations must not read as safety.

## Decision (proposed)
1. **Deterministic function of the whole input set.** `evaluate(policy, zones, observation_stream, run_config)` may keep internal timers, but uses event time (frame timestamps) only: no wall clock, randomness, network, or I/O.
2. **Four states with priority `RED > YELLOW > UNKNOWN > GREEN`** (owner-approved). UNKNOWN is not a severity or an alert, requires human review, and is never merged into GREEN. GREEN is assigned only to analyzed frames. **Incomplete inputs stay visible** on every state, including confirmed RED and YELLOW.
3. **Named, built-in, versioned rules with validated JSON parameters** (owner-approved); no custom expression language. A `(rule_id, rule_version)` is frozen; mechanics changes bump `semantics_version`. The reproducibility contract includes **both rule version and engine version**.
4. Three-valued logic. Bridging of short unknown runs applies only to TRUE conditions, never to FALSE, and never across faults.
5. **Per-track dwell** (owner-approved) with explicit identity loss, discontinuity and censoring semantics; no identity inference and no de-duplication in the engine.
6. AI/model output enters only as observations; model confidence acts only through explicit versioned policy parameters.
7. Integer-only arithmetic (microsecond time, integer pixels, doubled-coordinate geometry, permille confidence); canonical JSON; wall clock excluded from the determinism hash.
8. **Artifact separation** (owner-approved): the evaluation is the deterministic, hashed artifact; encoded evidence and mutable human review records live in separate artifacts that reference it.
9. Explanations are structured message ids; no free text and no generative text may alter or substitute for a rule result.

## Consequences
Limited expressiveness; every change is a new rule or policy version. Reproducibility is testable (hash equality, golden vectors). Policy comparison reduces to running `evaluate` twice and diffing. Image-space rules cannot express physical proximity.
Hold/debounce lag and the no-F-bridging rule make timelines conservative and sometimes noisy (UNKNOWN around dropped frames); this is intentional.

## Alternatives considered
Learned risk scoring (non-deterministic and not auditable by explicit rule trace); LLM-assigned severity (rejected: violates the AI-separation principle); a custom rule language (rejected by the owner for P0); binary safe/unsafe (hides unknowns).

## Verification (intended, not yet demonstrated)
A-P0-06, A-P0-07, A-P0-09, A-P0-18, A-P0-20, A-P0-22; mutation tests listed in MG-RISK-001 §12.

## Open issues
D-21 (acceptance of the detailed mechanics and numeric defaults in MG-RISK-001/MG-DATA-001), D-04b (YELLOW parameters). Policy comparison is post-MVP. Strided/budgeted analysis semantics (P2) may require a future `semantics_version`.
