# ADR-0018: Evidence Context and Lean Provider Drafts

- Status: Accepted
- Date: 2026-08-11

## Context

PA Stage 1/2 previously asked the model to repeat decision-tree questions,
sections, bar ranges, Gate results, Stage 1 confidence, Trader Equation math,
terminal labels, and auxiliary predictions. These fields increased output size
and created avoidable contradictions without improving Brooks or Wyckoff
reasoning. The program must reduce this work without turning contextual PA/VPA
interpretation into fixed thresholds.

## Decision

1. One `AnalysisFactCatalog` is the frozen Evidence Context for a PA run. It
   exposes role-safe projections, deterministic fact relations, reference
   resolution, snapshot identity, and a projection digest. Relations describe
   chronology, membership, overlap, and comparison only; they never name a
   trend, Setup, Wyckoff event, absorption, or exhaustion.
2. Providers negotiate `pa_draft_v2`. Stage 1 Draft owns a primary market
   hypothesis, zero to two genuine alternatives, confidence reasoning, PA
   semantics, and evidence-linked Gate answers. Stage 2 Draft owns the Brooks
   Setup, signal and timing interpretation, structural prices, subjective PA
   win-rate prior, and `trade|wait|reject` intent.
3. `pa_provider_contracts.py` is the only Draft-to-canonical materializer. It
   restores decision-tree question/section/bar range, derives Gate from node
   answers, freezes Stage 1 confidence into Stage 2, computes RR, breakeven and
   expectancy, inserts section 10.3 only for an executable candidate, and
   materializes terminal metadata. It does not infer market semantics.
4. Missing or hidden Evidence IDs, impossible protocol paths, invalid order
   geometry, and authority violations are hard errors. Alternative hypotheses,
   professional labels, and non-unique market interpretations remain model
   judgments and are not programmatically completed.
5. Canonical Stage 1/2 payloads remain compatible with records, Router, GUI,
   Shadow, and research code. Legacy canonical provider replies remain
   readable during migration. Retry protection compares the prior provider
   Draft with the repaired provider Draft; it never compares Draft with a
   materialized canonical object.
6. PA hypotheses, PA context, Entry Attempt, and HTF identity enter VPA through
   one `VPA_ANALYSIS_ENVELOPE`. The VPA evidence role still cannot create a
   Setup, direction, price, win rate, Gate, or order. Joint remains isolated to
   research and may only arbitrate an existing PA candidate.
7. Prefix-chain Stage 2 reuses the Stage 1 message and canonical assistant turn.
   It does not repeat the full Stage 1 JSON, Evidence Catalog, K-line table, or
   optional next-cycle task. Standalone Stage 2 retains the required context.

## Consequences

- Deterministic repetition and arithmetic hallucinations move out of model
  output while PA and VPA interpretation remain contextual.
- Genuine competing hypotheses survive the PA-to-VPA handoff; the program no
  longer copies the primary hypothesis to manufacture an alternative.
- Prompt and completion load are lower, but stronger models may still produce
  better semantic judgments. This architecture does not guarantee profit or
  remove ordinary trading uncertainty.
- Prompt/schema changes create a new analysis runtime fingerprint and therefore
  a new A/A/B/B cohort. Results must not be pooled with older cohorts.

## Verification

- Validate Draft schemas and canonical materialization independently.
- Confirm Stage 1 Gate derives from sections 1-2 and extreme-chaos cannot
  proceed.
- Confirm no-order Drafts do not receive a synthetic Trader Equation node.
- Confirm executable candidates receive program-exact RR, breakeven,
  expectancy, section 10.3, and terminal metadata.
- Confirm retry repairs cannot change primary direction, cycle, Setup, thesis,
  trade intent, or structural order prices unless feedback names that field.
- Regress PA off, production Shadow, research A/A/B/B, HTF, records/audit, and
  GUI canonical consumers before deployment.
