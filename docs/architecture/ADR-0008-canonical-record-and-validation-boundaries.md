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
2. VPA provider output is a `VpaObservationDraft`. The program may validate cited
   evidence IDs and materialize their exact `bar_range`; it must not manufacture a
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
4. Stage 1 alone cannot create an executable PA setup. A setup candidate exists only
   after a valid PA-only Stage 2 provides an order, entry rule and invalidation. VPA
   may support, challenge or time that candidate; it may not create one, reverse its
   direction or change `estimated_win_rate`.
5. TradingView resolves and pins provider venue and symbol for one subscription.
   Short-lived data caching must not trigger a new auto-resolution. Closed bars are
   determined from the absolute UTC open time plus timeframe duration.
6. A headless monitoring task freezes one current/HTF snapshot bundle. Repeated work
   on the same latest closed bar reuses that task instead of silently refetching a
   different counterfactual input.
7. A/A/B/B uses four registered independent arms over one frozen snapshot, isolated
   clients and an ABBA/BAAB order. SQLite remains the query store and an atomic JSON
   manifest is written for portable audit. Production Shadow or Soft runs are not
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
13. Auxiliary next-bar and next-cycle forecasts cannot invalidate the PA trade decision.
    Valid probability distributions receive a deterministic argmax label; missing or
    malformed auxiliary forecasts degrade to `unpredictable` with an audit event.
14. Brooks Trader Equation, not a universal reward:risk floor, owns deterministic trade
    expectancy. A sub-1:1 candidate is legal when its model-estimated probability and
    program-computed risk/reward produce positive expectancy. During Shadow/Soft, VPA
    cannot modify `estimated_win_rate`.
15. VPA `next_confirmation` describes evidence that would distinguish competing volume-
    price hypotheses. It is not approval for a Brooks Setup and cannot become an implicit
    entry gate. Neutral or insufficient VPA evidence preserves the PA baseline.
16. Provider credentials are validated at configuration and transport boundaries. API
    keys containing whitespace or non-ASCII command contamination are rejected before an
    HTTP header is constructed and are never echoed in diagnostics.

## Migration and Failure Policy

- The codec explicitly migrates supported v4 records to v5 in memory. It reports
  conflicts and rejects unknown future schema versions instead of guessing.
- Historical files are not rewritten automatically. Corpus audit reports legacy
  usage mismatches, missing HTF bars and stale snapshot hashes as historical defects.
- Missing or invalid VPA/Joint output degrades to PA-only with typed status. Invalid
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
  trigger, invalidation and order arbitration. A PA candidate exists only after that
  complete Stage 2 plan validates.
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
