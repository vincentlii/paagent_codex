# ADR-0009: Production Shadow and Research-Only Joint

- Status: Accepted
- Date: 2026-07-27
- Supersedes: the production Soft/Joint portions of ADR-0003, ADR-0004 and ADR-0008

## Context

The rescue architecture correctly separated PA, VPA and Joint authority, but production
still carried the research Joint branch. This added a third model call, duplicate baseline
and joint records, more retry/failure states, and an ambiguous `degraded_pa_only` result even
when the official PA analysis had succeeded. A single production run also cannot establish
the counterfactual effect of VPA.

## Decision

1. Production has two effective modes only: `off` and `shadow`.
2. `off` executes the original price-only PA Stage 1/Stage 2 chain and contains no VPA
   prompt, measurement, schema or record payload.
3. `shadow` executes the same official PA chain, then runs one independent
   `VpaEvidenceReport` over the frozen snapshot and frozen PA hypotheses. VPA is observable
   evidence for review and research; it does not modify the official decision.
4. Joint arbitration is available only to the isolated A/A/B/B research executor. B arms
   may request effective `soft`; ordinary constructors cannot enable it accidentally.
5. A legacy production configuration requesting `soft` executes effective `shadow`, records
   both modes, and emits the typed warning `soft_not_promoted`.
6. Production records do not persist duplicate `pa_baseline_stage2`, `joint_stage2` or
   `joint_decision` objects. GUI VPA summaries are deterministic projections of the canonical
   VPA report and explicitly state `shadow_only_no_effect`.
7. VPA format or provider failure cannot turn a valid official PA result into an analysis
   failure. It produces `completed_with_warnings`, keeps `official_path=pa_only`, and records
   the failed role. PA fact contradictions, invalid PA state and illegal order mathematics
   remain hard failures.
8. Brooks Trader Equation remains probability-weighted expectancy. RR is an input, not an
   independent 1:1 Gate; sub-1:1 trades require independently justified PA probability and
   positive program-recomputed expectancy.
9. PA Stage 1/Stage 2 are compiled through one structured handoff before VPA. Optional prose
   never controls candidate existence; program-computed order math and cited-window positions
   are facts for downstream roles, not mechanical PA location labels.

## Consequences

- Production Shadow uses three model calls in the normal path: PA Stage 1, PA Stage 2 and
  VPA Evidence. It no longer pays Joint latency or exposes Joint format failures.
- The official trading behavior remains identical to PA-only before the existing confidence
  execution policy. VPA quality can be improved and measured without suppressing trade
  frequency or silently changing orders.
- Joint remains testable and statistically comparable, but no model-generated VPA effect is
  promoted from one sample.

## Verification

- Production `soft` is constrained to effective `shadow` and never calls Joint.
- Production Shadow stores one official Stage 2 and one VPA report, with no duplicate Joint
  decision fields.
- Research B arms still execute Joint with isolated clients and the frozen catalog.
- VPA failure preserves a successful PA result with typed warnings.
- GUI projection exposes VPA evidence IDs and `shadow_only_no_effect`.
- Full non-live regression, prompt budget, A/A/B/B protocol and record/audit tests pass.
