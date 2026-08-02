# ADR-0012: Recoverable paper decision ledger

## Status

Accepted.

## Context

Analysis completion, market polling, and paper execution are asynchronous. A
slow model response can arrive after a later closed bar has already been seen;
a process can also stop after the analysis record and scheduler audit are
durable but before the paper disposition is written. Treating either boundary
as an in-memory callback loses fills or duplicates decisions.

A/A/B/B additionally measures intention-to-treat. A B arm whose Joint role
degrades to its PA fallback is still a valid assigned B observation and must not
be removed from the research denominator.

## Decision

1. Provider-declared `InstrumentSpec` is frozen in `FrozenAnalysisPayload` and
   persisted in snapshot context. Completion callbacks never read a mutable
   source or asset state for execution constraints.
2. Closed paper-market bars are persisted by full
   `provider + venue + instrument_type + symbol + timeframe + ts_open` identity.
   Re-observation must contain identical OHLCV facts; conflicting facts stop the
   paper path and are never silently overwritten.
3. Each paper order maintains its own evaluated-through timestamp. An order
   submitted after later bars were observed replays every persisted closed bar
   after its creation timestamp exactly once and in chronological order.
4. Every persisted analysis decision receives a durable `paper_decisions`
   disposition: `no_trade`, `submitted`, or `rejected`. The deterministic task
   ID is the official idempotency key. Paper failure never causes an LLM rerun.
5. Scheduler audit stores the durable task-to-run link. Startup and bounded
   in-process reconciliation load the already persisted analysis record and
   complete any missing paper disposition without calling the model.
6. Official paper accepts only a successful, persisted, audit-indexed,
   non-experiment PA result with `official_path=pa_only`, effective mode
   `off|shadow`, and no Joint payload. VPA Shadow remains non-authoritative.
7. Research paper is written only after the full A/A/B/B protocol validates.
   Four cohort-qualified accounts retain A1/A2/B1/B2 separately. A B arm that
   received PA fallback is stored with `treatment_received=false`, preserving
   intention-to-treat. Statistical reporting must aggregate at market-snapshot
   episode level before comparing A and B; four arms are not four samples.
8. Paper observation, submission, or reconciliation errors are sidecar failures.
   They are logged and retried without changing a valid PA analysis outcome.
9. A newer closed-bar analysis supersedes only older unfilled plans for the same
   exact market and timeframe, after those older plans are first replayed through
   the new bar. Open positions remain governed by their stored stop and target.
   Candidate prices must align with the provider-declared tick size.

## Consequences

- Model latency no longer changes which later bars an order can observe.
- Restarts do not require model calls to repair execution state.
- `no_trade` remains visible in the experiment denominator.
- Paper results stay distinct from reviewed real fills and closed-OHLC
  counterfactual outcomes.
- No live/private exchange order capability is added.

## Verification

- Late-decision closed-bar replay and restart idempotency tests.
- Same-timestamp market-fact conflict tests.
- Frozen instrument-spec and cross-contract isolation tests.
- Missing-disposition reconciliation without analysis resubmission tests.
- Official audit/authority boundary tests.
- B-arm PA fallback ITT and four-arm prevalidation tests.
