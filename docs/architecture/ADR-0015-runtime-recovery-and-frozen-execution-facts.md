# ADR-0015: Runtime Recovery and Frozen Execution Facts

## Status

Accepted.

## Context

Long-running paper research exposed three deterministic failure classes that do
not belong to PA or Wyckoff interpretation: a closed HTTP session was reused
after a feed reset, planned entry prices were used after a gap fill, and A/A/B/B
paper execution read instrument constraints outside the frozen experiment.
Native perpetual adapters also represented base-asset candle volume as contract
volume, which made VPA provenance factually incorrect.

## Decision

- A failed connected market source is discarded. Recovery creates a fresh source
  through the configured factory after backoff.
- Shutdown drains market-data workers before closing their sessions. Provider
  calls retain strict request timeouts.
- Paper sizing is recalculated at the actual fill price. Quantity may stay the
  same or decrease, but never increase. A fill that invalidates the protective
  stop or venue minimums is cancelled rather than simulated outside policy.
- Every paper order persists its submission-time `InstrumentSpec`; later fills
  consume that frozen spec while current market data supplies prices only.
- A/A/B/B v2 includes `instrument_spec_hash` in the frozen input and each arm.
  Paper execution consumes only `FrozenExperimentInput.instrument_spec`. The v1
  validator remains available for historical records.
- A/A/B/B completeness is measured by exact `(cohort_id,
  market_snapshot_hash)` groups containing A1, A2, B1, and B2.
- Native Binance, OKX, and Bitget candle `volume` is normalized as base-asset
  volume, with provider-specific raw field provenance. It remains a relative,
  same-provider comparison input rather than a Wyckoff label.
- Recovery applies completed analyses oldest first so a newer decision can
  supersede an older pending order deterministically.

## Consequences

- These changes alter execution and experiment mechanics, not PA Setup or VPA
  interpretation authority.
- Gap fills can reduce simulated quantity or cancel an otherwise valid plan.
- New experiments use protocol v2; historical v1 records remain auditable but
  cannot silently acquire execution constraints they never froze.
- Runtime recovery is idempotent and does not require another model call.
