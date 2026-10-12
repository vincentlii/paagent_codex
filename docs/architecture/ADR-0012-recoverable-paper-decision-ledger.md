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
   Re-observation is versioned by its frozen observation time. The approved
   2026-10-09 amendment permits audited volume/amount revisions and OHLC revisions
   only when no account has consumed that price bar in execution replay. Accepted
   changes retain before/after facts and observation time in
   `paper_market_bar_revisions`; unchanged observations advance the version's
   confirmed-through time. Older frozen observations cannot revert newer facts.
   Consumed price conflicts reject the incoming revision. The user-approved
   2026-10-12 amendment below isolates conflicts whose execution dependencies
   are historical, allowing independent future bars to progress. Conflicts that
   may affect an active order, and conflicting duplicates inside one observation,
   still reject the whole observation transaction. No original analysis record,
   order, realized result, or rejected disposition is rewritten.
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

### 2026-10-12 amendment: isolate historical consumed-price conflicts

The user explicitly approved retaining prior execution facts while isolating
historical conflicts that no longer affect any active order. This is not a
price tolerance, a new market interpretation, or a rewrite of old evidence.

- For consumed OHLC conflicts, check all accounts for `pending/open` orders
  whose `created_bar_ts < conflicting_bar_ts`, including orders that have not
  replayed that bar yet. Any such potential dependency keeps the transaction
  rejection. A filled trade is not required for consumption: a no-fill replay
  also depended on OHLC.
- Otherwise retain `paper_market_bars` unchanged and record the rejected facts
  in the existing revisions table with reason
  `consumed_price_conflict_quarantined`, including before/after facts and first/
  latest observation time. Repeated identical quarantine observations are
  idempotent. Accepted-version selection excludes this reason; quarantine does
  not advance the accepted version's confirmed-through time.
- Continue processing independent later bars atomically. Any other hard
  conflict rolls back the entire batch, including its new quarantine audit.
  Do not silently replace a current analysis's Frozen Snapshot with old prices.
- Before inserting a new order, reject if its replay would cross a quarantined
  bar after its creation timestamp. An independent future order starts at or
  after the historical conflict and never replays that bar. Existing task
  dispositions remain immutable; rejection never causes model resubmission.
- Schema and simulation versions remain unchanged: old facts, orders, PnL,
  expiry, activation and ambiguity rules are preserved. This protects v1/v2/v3
  histories without relabeling outcomes or claiming new trading evidence.
- Rollback must understand this quarantine reason and exclude it from accepted
  revisions. Do not restore a pre-amendment reader against a ledger containing
  these audit rows. Preserve a consistent backup; use a compatible rollback
  build or a separate pre-amendment instance, never delete historical evidence.

- Model latency no longer changes which later bars an order can observe.
- Restarts do not require model calls to repair execution state.
- `no_trade` remains visible in the experiment denominator.
- Paper results stay distinct from reviewed real fills and closed-OHLC
  counterfactual outcomes.
- No live/private exchange order capability is added.

## Verification

- Late-decision closed-bar replay and restart idempotency tests.
- Same-timestamp market-fact conflict tests.
- Audited unconsumed/volume revisions, stale-result restart replay, cross-account
  consumed-price protection, legacy fill protection, and transactional rollback.
- Frozen instrument-spec and cross-contract isolation tests.
- Missing-disposition reconciliation without analysis resubmission tests.
- Official audit/authority boundary tests.
- B-arm PA fallback ITT and four-arm prevalidation tests.

## Authorized amendment: 2026-10-13 limit lifetime

The user authorized implementation and VPS publication of the reviewed three-close initial lifetime for new explicit-pipeline 1h Headless LIMIT orders. Frozen `limit_3bar_v1` separates next-close result freshness from third-close pending expiry. Broker activation checks freshness again under its write lock. Market/breakout and unmarked restored jobs retain original timing.

Extended LIMIT orders use `paper_latency_v4`, without relabeling v1-v3 orders or rewriting their prices, events, PnL or expiry. Valid hourly analyses, including No Trade, retain supersession semantics. Failures never renew expiry; opened positions retain protective exits. Missing eligible bars while v4 is pending produce an ambiguous outcome, not a later certain fill/no-fill.

Migration adds only nullable `paper_orders.result_expires_at_ms`; legacy rows remain null without backfill. Before migration: pause, drain Host/Worker, stop services and verify a consistent backup. After migration: compare all legacy columns and other tables, run SQLite quick checks and inspect version scoping. Rollback requires a build supporting both v4 and the earlier quarantine amendment; an old reader must not take over this ledger. Restoring a pre-release data backup would discard later evidence and requires a separate recovery decision.

Separate v3/v4 projections preserve historical visibility and PnL scope. Capital/risk remains shared by the official account. The longer lifetime is an initial policy, not proven superior returns.
