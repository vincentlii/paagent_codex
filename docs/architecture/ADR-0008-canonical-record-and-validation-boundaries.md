# ADR-0008: Canonical Record and Validation Boundaries

## Status

Accepted.

## Context

The PA/VPA pipeline previously allowed the same fact to be represented in several
places: provider output, normalized output, record sidecars, runtime summaries and
experiment metadata. Validation also inferred responsibility from field names and
message text. These duplications made migrations, retries, replay and A/A/B/B
experiments fragile, and encouraged local fixes that did not preserve the authority
model.

The rescue release keeps the project principle unchanged:

> Code measures deterministic facts and enforces safety. LLMs interpret context,
> compare plausible hypotheses and judge setup timing.

## Decision

1. `AnalysisRecordV5` is the only persisted analysis contract. JSON enters and
   leaves the application through `pa_agent.records.codec`. Runtime projections,
   including GUI VPA summaries and aggregate usage, are derived and are not stored
   as competing sources of truth.
2. VPA provider output is a `VpaObservationDraft`. The program validates only evidence
   IDs visible in that invocation and materializes `catalog_id`, `data_status`, exact
   `bar_range` and `requires_closed_bar`; it must not manufacture a
   Wyckoff label, market conclusion or missing evidence. Unknown non-authoritative
   Draft fields are whitelist-normalized with an audit event; reserved PA, order,
   confidence and execution fields remain authority violations at any nesting depth.
   The resulting `VpaObservation` is canonical.
3. Validation findings carry stable `code`, `severity`, `owner` and `retry_policy`
   fields. Retry routing and authority accounting use those typed fields, never
   message substrings or field-name heuristics. Valid numeric support and resistance
   sets are canonically ordered near-to-far without changing membership or price;
   invalid formats, wrong-side levels, price-action geometry conflicts and explicit
   `K# + OHLC field + price` contradictions remain hard failures. Display rounding is
   allowed; market interpretation is not inferred from prose.
4. Stage 1 alone cannot create an executable PA setup. `compile_pa_handoff(...)` is the
   only PA-to-VPA/Joint compiler and consumes validated Stage 1/Stage 2 plus the frozen
   price snapshot. A candidate exists for every valid executable order; breakout orders
   retain their required basis/rule, while limit and market orders may legitimately have
   `entry_rule=null`. Missing Setup prose or analytical invalidation remains explicit null
   instead of being invented or silently turning the candidate into an empty list.
5. TradingView resolves and pins provider venue and symbol for one subscription.
   Short-lived data caching must not trigger a new auto-resolution. Closed bars are
   determined from the absolute UTC open time plus timeframe duration.
6. A headless monitoring task freezes one current/HTF snapshot bundle. Repeated work
   on the same latest closed bar reuses that task instead of silently refetching a
   different counterfactual input.
7. A/A/B/B uses four registered independent arms over one frozen snapshot, isolated
   clients and an ABBA/BAAB order. SQLite remains the query store and an atomic JSON
    manifest is written for portable audit. Production Shadow runs are not
   presented as counterfactual A/B evidence.
8. Research quality requires at least 300 independent `market_snapshot_hash` values
   within one cohort before claims about VPA lift are accepted. Duplicate runs measure
   model variance, not additional market samples.
9. A pending Entry Attempt has one canonical empty-bar state. A protective stop is an
   intrabar execution boundary, while analytical invalidation is a separate model-owned
   re-evaluation condition. Closed-OHLC replay may report the former but never infer the
   latter from free text.
10. Runtime history readers and writers use the same `PA_AGENT_HOME`-derived directory.
    GUI E2E tests disable post-order persistence and notifications so fixtures cannot
    become production history.
11. Each deterministic condition has one validation owner. JSON Schema owns conditional
    field-state groups, while coherence checks own chronology, frozen-bar references and
    cross-domain semantics. The same defect must not be emitted twice through both layers.
12. Validation has three explicit severities:
    - hard rejection is limited to unparseable JSON, frozen-fact contradictions,
      authority violations, impossible state/chronology and illegal order geometry or
      mathematics;
    - deterministic normalization is allowed only where the declared state or supplied
      numbers imply one representation, and every material change is audited;
    - professional wording, non-unique PA/VPA interpretation and terminology quality are
      warnings. They never trigger a model retry or discard an otherwise valid analysis.
    A JSON object emitted inside the Stage 1 free-text `key_signals` list is serialized
    losslessly as a canonical JSON string and audited as representation normalization;
    the program does not reinterpret, translate or discard any part of the signal.
    A VPA `sequence_claim` whose two evidence windows overlap is deterministically removed
    with normalization plus warning because it cannot express an ordered response; the
    containing observation remains usable. A strictly reversed claim remains a hard error.
13. Auxiliary next-bar and next-cycle forecasts cannot invalidate the PA trade decision.
    Valid probability distributions receive a deterministic argmax label; missing or
    malformed auxiliary forecasts degrade to `unpredictable` with an audit event.
14. Brooks Trader Equation, not a universal reward:risk floor, owns deterministic trade
    expectancy. A sub-1:1 candidate is legal when its PA subjective probability prior and
    program-computed risk/reward produce positive expectancy. The handoff labels this input
    `llm_subjective_prior`, records `calibration_id=null` until real calibration exists, and
    exposes program-computed break-even probability and expectancy. It must not be described
    as historical/backtest probability without sourced statistics. VPA cannot modify it.
15. VPA `next_confirmation` describes evidence that would distinguish competing volume-
    price hypotheses. It is not approval for a Brooks Setup and cannot become an implicit
    entry gate. Neutral or insufficient VPA evidence preserves the PA baseline.
16. Provider credentials are validated at configuration and transport boundaries. API
    keys containing whitespace or non-ASCII command contamination are rejected before an
    HTTP header is constructed and are never echoed in diagnostics.
17. Research quality reports separate first-pass cleanliness from final accepted quality.
    A repaired first attempt remains visible and fails `first_pass_clean`, but an earlier
    error does not reclassify a later validated final call as a current PA/VPA semantic
    failure or failed call status. Latency and token totals still include every attempt.
18. Citation and relation materialization may use only identities already implied by the
    frozen catalog. An explicit `K#` can be auto-bound to one unique single-bar evidence item
    without mutating the model's declared citations or counting that binding as independent
    volume evidence;
    unknown or ambiguous references remain hard failures. When no PA Entry Attempt exists,
    VPA timing is normalized to `insufficient_data` without suppressing background evidence.
    Adjacent-window net direction is derived at render/validation time from existing facts,
    not persisted into the catalog, so historical catalog identities remain stable. Explicit
    same/opposite window claims and HTF upper/lower spatial claims are frozen-fact checks;
    leg names, support/resistance meaning and Wyckoff interpretation remain model-owned.
19. Raw seasonality history is not copied into records. Its exact causal sample set and
    boundary metadata are represented by a frozen hash in compact snapshot context; replay
    restores that identity together with volume provenance and still revalidates the catalog
    hash. Missing payload bytes are never regenerated or guessed.
20. The audit database projects VPA observations, declared versus auto-bound evidence,
    hypothesis assessments and typed failures into normalized tables. JSON remains the source
    artifact; the database is a rebuildable review index, not a second semantic authority.

## Migration and Failure Policy

- The codec explicitly migrates supported v4 records to v5 in memory. It reports
  conflicts and rejects unknown future schema versions instead of guessing.
- Historical files are not rewritten automatically. Corpus audit reports legacy
  usage mismatches, missing HTF bars and stale snapshot hashes as historical defects.
- Missing or invalid production VPA output preserves PA-only with a typed warning;
  invalid research Joint output degrades its B arm to PA-only. Invalid
  deterministic facts or PA output cannot produce an executable order.
- One directed repair is allowed only when a typed finding declares it repairable.
  Repeated schema variation becomes an explicit failed/degraded role, not an
  unbounded prompt-and-parser loop.
- Directed feedback includes the dependency closure of the state being repaired. For
  example, changing a Spike from stable to transitioning must also expose its required
  transition risk; a repair must never strand a newly required sibling field.
- A parseable VPA/Joint draft remains the repair base. The retry receives that
  draft and is instructed to change only the fields or citations named by
  feedback; the full role object is then revalidated. Authority violations fail closed without a
  generative rewrite. Discarding a parseable draft and resampling the whole role
  is prohibited because it converts a local contract error into unrelated model
  variance.
- A Stage 2 order that fails deterministic trade mathematics is rejected by the
  execution policy on the same frozen snapshot. The program clears executable fields,
  terminates at Trader Equation node 10.3 and preserves the original provider output in
  the audit record. It never asks the model to rewrite entry, stop or target merely to
  cross a threshold.
- Stage 1 `entry_setup` is a framework hint, not an authorization token. After
  `gate_result=proceed`, Stage 2 exclusively owns concrete Brooks Setup selection,
  optional trigger/invalidation prose and order arbitration. A PA candidate exists only
  after the executable Stage 2 plan validates; candidate identity never depends on optional
  prose fields.
- Stage 2 normalizers perform representation-only work. Invalid order geometry or Trader
  Equation is handled by the explicit deterministic execution policy, which preserves the
  provider response in audit data, blocks the order without a generative retry, and records
  a typed warning instead of disguising the action as normalization.
- `trade_confidence` describes an executable Entry Attempt. It is null when no such
  attempt exists and an integer from 0 to 100 only for an executable order; the
  program never fabricates a confidence score for a no-order decision.

## Consequences

The persisted model, runtime display and experiment reports can no longer drift
silently. Provider wording remains flexible where theory permits several reasonable
interpretations, while citations, chronology, identity, usage and order legality are
deterministic and replayable. Old records remain inspectable but are not treated as
proof that a new release passes replay or research gates.

## Verification

- Record v5 round-trip and v4 migration tests.
- PA/VPA authority, citation and chronology invariant tests.
- Explicit K#/OHLC/value grounding and Entry Attempt state-machine tests.
- TradingView identity, cache and closed-bar tests.
- Frozen headless task and bounded refresh/backoff tests.
- A/A/B/B registration, manifest, cohort and quality-gate tests.
- Full non-live test suite plus Linux headless import and Windows GUI import checks.
- GUI E2E hash check proving the production trade CSV is unchanged.
