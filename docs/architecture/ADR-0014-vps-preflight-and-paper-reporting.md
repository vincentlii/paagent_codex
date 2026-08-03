# ADR-0014: VPS Preflight and Paper Reporting

## Status

Accepted.

## Context

An unattended service needs to distinguish configuration errors, public market
connectivity, AI analysis, and paper-ledger review. Combining them in one health
check makes failures ambiguous and can waste model tokens during deployment.
Running a network probe automatically on every systemd restart can also turn a
temporary venue outage into a restart loop.

## Decision

1. `pa-agent-headless --check` is deterministic and does not open the network.
2. `pa-agent-headless --probe-data` opens only configured public market-data
   APIs. It does not call an AI provider. It verifies current and configured
   higher-timeframe closed bars, resolved identity, and instrument constraints.
3. The data probe isolates assets: one failed venue is reported without hiding
   successful venues, and every source is disconnected in `finally`.
4. systemd runs only `--check` as `ExecStartPre`. Operators run `--probe-data`
   explicitly before first start and after deployment changes.
5. `pa-agent-paper-report` opens SQLite in read-only mode and reports descriptive
   account, order, and disposition summaries.
6. A/A/B/B report metadata declares `paired_market_snapshot` as the unit of
   analysis. Arm rows are not presented as independent profitability samples.
7. Deployment templates contain no API key. systemd loads the key as a
   credential, code and prompts remain read-only, and all mutable state stays
   under `PA_AGENT_HOME`.

## Consequences

- Deployment failures can be localized before spending AI tokens.
- Temporary exchange outages do not trigger an automatic preflight restart
  storm.
- Paper state can be inspected without migrations, writes, or model calls.
- The deployment remains paper-only and does not grant real-order authority.
