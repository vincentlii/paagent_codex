# ADR-0017: Deterministic Facts and Execution Semantics

- Status: Accepted
- Date: 2026-08-05

## Context

PA/VPA prose may contain exact claims whose truth is not interpretive, such as
whether one closed bar is contained by the previous bar.  A model can also
describe a passive limit order while informally asking for confirmation first,
which leaves the execution meaning ambiguous.  Repeated prose validators must
not grow into a second PA or Wyckoff engine.

## Decision

1. Program-owned OHLC geometry remains formula-only.  Structured geometry and
   explicit numeric OHLC claims are checked against the frozen frame.  Free
   prose that appears to describe inside/outside geometry is quality evidence,
   not a rejection gate, because negation and market-context wording are not
   safely reducible to regex.  The program does not infer trend, Setup,
   support, absorption, or any Wyckoff event.
2. Validated order types map to one program-owned execution mode:
   `限价单 -> passive_limit`, `突破单 -> price_triggered_stop`, and
   `市价单 -> immediate_market`.  This field clarifies execution and never
   changes the PA order choice.  Frozen legacy records use the same mapping.
3. Existing `entry_rule` prose remains available as PA context and does not
   override the program-owned execution mode.  Confirmation-before-entry prose
   on a passive limit is a consolidated warning, not a hard failure.  A genuine
   confirmation prerequisite should remain no-order and use existing watch
   fields for a later PA run, but wording variance must not discard a safe PA
   analysis.
4. When causal rolling and same-slot seasonality comparators are both available
   and point in opposite directions, the prompt highlights the disagreement.
   Omitting one side is a consolidated professional warning, not a hard gate;
   the LLM still owns contextual weighting and VPA interpretation.
5. Repeated professional warnings are grouped into one event per warning code
   with an occurrence count and retained findings.  Only factual, authority,
   schema, chronology, or execution contradictions can reject a result.
6. Required nested schema keys must be visible in the corresponding role
   prompt.  The model boundary may normalize only documented, one-to-one field
   aliases when the canonical field is absent; conflicting duplicate fields
   remain hard errors.  Validation audit records `bounded_retry` only when the
   runtime retry policy will actually issue another model call.
7. A bounded VPA or Joint contract repair may repair structure and citation
   metadata, but it may not silently replace the first parseable attempt's core
   structured judgment.  Protected fields are restored before the repaired
   object is validated, and every restoration is audited.
8. Provider rejection of structured-output parameters is a transport
   capability mismatch, not model reasoning failure.  The runner may make one
   auditable text-mode compatibility attempt while preserving the ordinary
   transient retry budget.  It must still pass the same parser, schema,
   factual, authority, and execution checks.

## Consequences

- Exact structured factual contradictions receive bounded repair without
  teaching code to trade PA or Wyckoff labels; ambiguous prose is retained as
  a warning for review.
- Entry semantics become auditable without adding a second strategy router.
- VPA receives more balanced comparator context without becoming a gate.
- Existing records remain replayable and warning volume no longer exaggerates
  the number of independent root causes.
- Prompt/schema drift is caught before release, while exact compatibility
  aliases avoid wasting another model call or discarding an otherwise valid PA
  analysis.
- Repair and provider compatibility behavior are bounded and auditable, so a
  retry cannot conceal semantic drift or turn a transport mismatch into a
  strategy change.

## Verification

- Replay the failing XAUUSD snapshot and surface the false K4 prose containment
  claim as a quality warning without discarding an otherwise valid analysis.
- Confirm unrelated “inside the trading range” prose does not trigger geometry
  validation.
- Validate all three executable order-type mappings and legacy replay.
- Verify opposite rolling/seasonality evidence produces a warning only.
- Verify grouped warning events retain the original occurrence count.
