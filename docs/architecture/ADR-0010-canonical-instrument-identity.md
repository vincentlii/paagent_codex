# ADR-0010: Canonical instrument identity across history and replay

## Status

Accepted.

## Context

The display symbol is not a sufficient market identity. The same `XAUUSD` or
`BTCUSDT` label can resolve to different providers, venues, contracts, price
paths, and volume semantics. Reusing incremental context or counterfactual bars
across those boundaries corrupts both analysis and outcome statistics.

Capture wall-clock fields also describe observation time rather than market
state. Including them in `market_snapshot_hash` makes the same frozen bars look
like different samples.

## Decision

1. Persisted and runtime instrument identity is
   `provider + venue + resolved_symbol`; timeframe remains an explicit query
   dimension.
2. When a resolved identity is available, incremental history and
   counterfactual replay require an exact identity match. Legacy records without
   identity are not guessed or backfilled from the display symbol.
3. The derived SQLite audit index stores `source_provider` and
   `resolved_symbol`; schema version 10 migrates existing databases in place.
4. `market_snapshot_hash` excludes observation-time metadata such as
   `captured_at_ms` and `snapshot_ts_local_ms`, while retaining bars, source
   identity, HTF market context, and volume provenance.
5. Prepared-task caching uses a hash of current-frame facts. A provider revision
   at the same bar timestamp invalidates the cache and produces a new task.

## Consequences

- Old records remain readable under AnalysisRecord v5.
- Legacy rows without resolved identity remain pending for deterministic replay;
  this is preferable to manufacturing outcomes from a different market.
- Display aliases may still be shared, but they cannot merge samples.
- No PA or Wyckoff interpretation is moved into code by this decision.

## Verification

- Cross-venue incremental lookup tests.
- Cross-venue counterfactual isolation tests.
- Wall-clock-invariant market hash tests.
- Same-timestamp provider-revision cache invalidation tests.
