# Lean Provider Contract Convergence Design

## Goal

Replace the partially trimmed canonical schemas used by `pa_draft_v2` with
independent lean provider contracts whose prompt, validation, repair, and
materialization all come from one source. Preserve PA and VPA judgment quality,
the canonical record format, and every existing authority boundary.

## Three-Role Decision

- PA trader: the model continues to own market background, cycle, Always In,
  Setup, competing hypotheses, bar roles, structural levels, signal quality,
  timing, and subjective PA priors. The program must not infer these meanings.
- Wyckoff VPA trader: the VPA role remains evidence-first and label-last. It may
  challenge PA timing but cannot create direction, Setup, Gate, price, win rate,
  or an order. Repair may not restore a field that validation explicitly asked
  the model to correct.
- Quant/software architect: provider drafts, canonical records, and research
  envelopes are distinct contracts. The provider contract is the single source
  for prompt shape, JSON validation, and repair feedback. Deterministic
  materialization is the only adapter from provider draft to canonical record.

## Contract Ownership

### Stage 1 model-owned fields

- diagnosis confidence and reasoning
- primary and zero to two genuine alternative market hypotheses
- spike/climax/transition interpretation
- detected patterns, key signals, HTF interpretation, structural levels, and
  risk warning
- current bar interpretation and five-bar contextual summary
- spike evidence interpretation
- semantic answers and evidence references for decision-tree sections 1-2
- incremental delta when incremental analysis is active

### Stage 1 program-owned fields

- primary cycle/direction/setup top-level mirrors
- stable hypothesis IDs and roles
- decision-tree question, section, bar range, and Gate result
- flattened PA evidence references and theory rule IDs

### Stage 2 model-owned fields

- Brooks Setup and final `trade|wait|reject` intent
- signal/entry interpretation and second-entry judgment
- structural entry, stop, targets, invalidation, confidence, and subjective PA
  win-rate prior
- semantic decision-tree answers excluding Trader Equation

### Stage 2 program-owned fields

- inherited Stage 1 diagnosis confidence
- risk, reward, RR, breakeven, expectancy, Trader Equation node
- terminal outcome and optional unavailable next-cycle placeholder

## Interface Design

`pa_provider_contracts.py` exposes one deep module interface:

- `schema_for_provider_draft(stage)` returns the independent runtime schema.
- `render_provider_draft_contract(stage)` renders the same schema into a compact
  prompt block. No hand-maintained second field list is allowed.
- `materialize_stage1_draft(...)` and `materialize_stage2_draft(...)` adapt only
  model-declared semantics plus deterministic metadata into canonical records.

The canonical Stage 1/2 schemas remain unchanged. Legacy canonical model replies
remain readable, but new model calls use only the lean draft path.

## Validation And Repair

- JSON syntax, schema shape, enum, required-field, and additional-property
  failures are contract errors. The validator returns all errors in one result,
  with complete paths and legal enum values.
- One bounded contract repair may rewrite only reported fields. The prior full
  provider draft and the exact provider schema accompany repair feedback.
- Primary direction, cycle, Setup hint, thesis, trade intent, structural prices,
  and other unreported semantic fields remain immutable across repair.
- Unambiguous aliases may be normalized only when meaning is one-to-one. A
  qualitative confidence such as `medium` must never be converted to a number
  by code.
- Authority violations and impossible order geometry remain hard failures.

## Compatibility And Failure Behavior

- GUI, records, audit database, Shadow, and A/A/B/B continue to consume the
  canonical schemas.
- A Stage 1/2 contract failure after one repair remains `invalid_model_output`.
- Production Shadow VPA failure continues to preserve valid PA-only output as
  `completed_with_warnings`.
- VPA repair restores only protected fields not named by validation feedback.

## Verification

- Replay the 2026-08-11 DeepSeek-style Stage 1 response and confirm all schema
  differences are reported together and are eligible for one repair.
- Verify the prompt contract is rendered from the exact runtime schema.
- Cover Stage 2 `trade`, `wait`, and `reject` drafts.
- Verify repair cannot change unreported PA hypotheses, direction, Setup, trade
  intent, or structural prices.
- Verify VPA repair can correct a reported protected field without programmatic
  restoration of the invalid value.
- Run focused unit tests, non-GUI unit tests, offline integration tests, and
  `git diff --check`.

