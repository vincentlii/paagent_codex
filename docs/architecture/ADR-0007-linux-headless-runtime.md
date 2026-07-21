# ADR-0007: Linux Headless Runtime and State Boundary

## Status

Accepted.

## Context

The desktop bootstrap imports Qt, owns one mutable chart data source, and writes state below the source checkout. That lifecycle is unsuitable for a Linux service, multi-asset polling, read-only deployment, or systemd credential delivery. Scheduler completion also cannot be equated with a successful, persisted market analysis.

## Decision

1. `pa-agent-headless` is a Qt-free entry point. GUI dependencies live in the optional `gui` extra.
2. `PROJECT_ROOT` and `PROMPT_DIR` are read-only resources. `PA_AGENT_HOME` owns configuration, logs, records, audit databases, experience and trade exports. Explicit path overrides remain available for deployment and tests.
3. Every configured asset owns one data-source instance. Data fetch and model concurrency are independently bounded.
4. A task identity binds source, resolved venue, symbol, timeframe, immutable market facts and an analysis cohort. Capture wall-clock time is excluded; price/indicator/prompt changes are not.
5. Scheduler execution, domain `RunOutcome`, record persistence and audit health are separate states. Only a persisted `completed`, `completed_with_warnings`, or `degraded_pa_only` result enters successful deduplication.
6. Failed snapshots may retry with a bounded exponential backoff. Successful task IDs are persisted in `monitoring_jobs`, so process restarts do not repeat the same analysis.
7. Runtime API-key precedence is environment, explicit credential file, systemd credential, then local encrypted configuration. Runtime injection is never persisted.
8. `SIGINT` and `SIGTERM` request cooperative cancellation. Sources disconnect after scheduler shutdown; systemd provides the outer stop deadline.

## Consequences

- Linux does not need PyQt6 or pyqtgraph.
- A writable checkout is no longer required when `PA_AGENT_HOME` is set.
- Transient Provider failures no longer poison dedupe history or masquerade as successful runs.
- Local Fernet encryption prevents plaintext-at-rest accidents, but the adjacent key does not protect a compromised runtime account; systemd credentials are preferred on VPS.
- The headless service produces analysis advice and audit records only. It still does not connect to a broker or execute orders.
