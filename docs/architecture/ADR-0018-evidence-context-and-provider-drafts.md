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
   Stage 1 data sufficiency (`§1.1`) is a program preflight and is not delegated
   to the provider; the provider starts contextual diagnosis at `§1.2`.
   Trace `node_id` values are exact question IDs generated from the same parsed
   decision tree used by materialization. Section headings such as `11` are not
   nodes and cannot be normalized into a model-selected `11.1`-`11.4` branch.
   Before materialization, a lossless boundary normalizer may split an
   unambiguous classifier answer such as `同向` or `是多头` into the binary
   `answer` plus its original meaning in `branch`, and may normalize
   `signal_bar.pattern=null` to an empty string when no signal bar exists.
   Every such change is audited. The materializer then restores canonical
   trace order without changing model reasoning or Evidence IDs.
   `gate_shortcircuited`, trace `question/section/bar_range/next_node/skipped`,
   hypothesis IDs, and terminal fields are program-owned. Optional
   `next_bar_prediction` remains an LLM auxiliary judgment when enabled and
   has no order authority.
4. Missing or hidden Evidence IDs, impossible protocol paths, invalid order
   geometry, and authority violations are hard errors. Alternative hypotheses,
   professional labels, and non-unique market interpretations remain model
   judgments and are not programmatically completed. An explicit, locally
   bound `K#-K# consecutive bullish/bearish bars` or `K#-K# 连续阳线/阴线`
   assertion can be compared with frozen candle directions, but because its
   scope is inferred from free prose it is an auditable quality warning rather
   than a run-blocking contract error.
   Price-path language such as `continuous rise/decline` or `连续上涨/下跌`
   remains contextual PA interpretation: it does not assert that every candle
   has the same color. Whether either sequence constitutes a trend, Spike,
   climax, Setup, or Wyckoff event remains an LLM judgment.
   Run-blocking validation is reserved for structured contracts, explicit
   locally bound OHLC values and geometry, evidence identity, authority,
   order mathematics, and execution safety. Free-prose window counts and run
   descriptions must not terminate an otherwise safe analysis.
   The same boundary applies to VPA and Joint comparator prose: an apparent
   contradiction found by natural-language parsing is a quality warning.
   Unknown evidence, structured chronology errors, authority violations, and
   executable-order safety remain blocking.
5. Canonical Stage 1/2 payloads remain compatible with records, Router, GUI,
   Shadow, and research code. Legacy canonical provider replies remain
   readable during migration. Retry protection compares the prior provider
   Draft with the repaired provider Draft; it never compares Draft with a
   materialized canonical object.
8. The exact minified Provider Draft schema and its digest are generated from
   the same runtime object used by validation. Hand-maintained field-shape
   lists are forbidden in prompts; prose may only explain semantics and field
   ownership.
9. Provider contract validation aggregates all independent schema errors. One
   repair receives the complete original Draft and may edit only reported JSON
   Pointers. The program projects only those authorized pointer values onto the
   original Draft and ignores every other candidate rewrite; the projected
   Draft must then pass the complete validation chain. Errors discovered after canonical
   materialization are mapped back to the original Draft by trace `node_id`,
   never by an index that may have shifted during materialization.
   Every failure originating from a Provider Draft exits through this same
   contract path, including downstream fact checks, so trace paths are always
   remapped to the original Draft before repair. Provider-owned inputs that
   would prevent materialization, including unknown
   nodes, hidden Evidence IDs, incomplete Gate paths, and intent/order conflicts,
   are reported as bounded Provider Contract issues rather than internal
   materialization failures.
10. VPA repair preserves unrelated judgments by semantic identity
    (`hypothesis_id`, evidence-set identity, or sequence evidence pair), never
    by array index, and never restores a field explicitly named by validation.
6. PA hypotheses, PA context, Entry Attempt, and HTF identity enter VPA through
   one `VPA_ANALYSIS_ENVELOPE`. The VPA evidence role still cannot create a
   Setup, direction, price, win rate, Gate, or order. Joint remains isolated to
   research and may only arbitrate an existing PA candidate.
7. Prefix-chain Stage 2 reuses the Stage 1 message and canonical assistant turn.
   It does not repeat the full Stage 1 JSON, Evidence Catalog, K-line table, or
   optional next-cycle task. Standalone Stage 2 retains the required context.
11. Deterministic grounding includes exact, locally bound claims only. An
    explicit `K#-K#` numeric price range is checked against that frozen
    window's high/low. Each VPA bar exposes how many later closed bars already
    exist and their range, so only an impossible claim that none exists is
    rejected. A claim that existing later bars have not confirmed acceptance
    or rejection remains an LLM judgment.
12. Relative-volume ratios expose signed `deviation_pct` from parity in
    addition to `above|below|equal`. The sign records direction and the
    deviation records magnitude; neither is a programmatic VPA label or trade
    threshold. On a no-order Draft only, `trade_confidence_reasoning=null` is
    losslessly normalized to the canonical empty string and audited. The same
    null on an executable order remains invalid.

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
- Trader Equation evidence must come from model-declared PA references. The
  materializer must never invent a fallback reference such as `PA.BAR.K1`.

## Verification

- Validate Draft schemas and canonical materialization independently.
- Confirm Stage 1 Gate derives from sections 1-2 and extreme-chaos cannot
  proceed.
- Confirm no-order Drafts do not receive a synthetic Trader Equation node.
- Confirm executable candidates receive program-exact RR, breakeven,
  expectancy, section 10.3, and terminal metadata.
- Confirm retry repairs cannot change primary direction, cycle, Setup, thesis,
  trade intent, or structural order prices unless feedback names that field.
- Confirm repair rejects unreported deletion, nulling, array growth, and array
  reorder, and that VPA stable fields survive reorder by identity.
- Replay a DeepSeek-style multi-error Draft and confirm one repair reaches the
  next stage without relaxing semantic or authority checks.
- Confirm redundant program-preflight nodes cannot shift repair pointers or
  normalization audit paths.
- Confirm explicit continuous bar-direction ranges contradicting frozen OHLC
  are reported together with all other editable Draft issues.
- Regress PA off, production Shadow, research A/A/B/B, HTF, records/audit, and
  GUI canonical consumers before deployment.
