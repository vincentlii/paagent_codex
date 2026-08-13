# Final Grounding Repair Design

## Goal

Eliminate the remaining deterministic contract retries and factual contradictions before paper-trading deployment without moving PA or Wyckoff interpretation into code.

## Authority Boundary

- Code owns JSON contract normalization, bar chronology, window membership, exact price and volume measurements, and comparator magnitude.
- The LLM owns Brooks cycle/Always-In/Setup interpretation, Wyckoff/VPA hypotheses, entry timing, and the final trade judgment.
- Validators may reject structural invalidity and exact fact contradictions. Professional interpretations remain warnings unless they assert a deterministically false fact.
- VPA remains a sidecar in Shadow mode. This repair does not promote it into a trading gate or enable Joint execution.

## Changes

1. Canonicalize semantically empty no-trade confidence reasoning without another provider call.
2. Expose deterministic chronology for each VPA bar: the count and IDs of already-closed later bars. Reject only impossible claims that no later closed bar exists.
3. Bind explicit price-range claims to their stated K-window. A shorter-window extreme must not be attributed to a longer window with different extrema.
4. Expose signed comparator deviations. Prompt language must treat ratios close to 1.0 as approximately normal rather than strong low/high activity; the program does not infer Wyckoff meaning.

## Verification

- Each behavior starts with a failing regression test.
- Provider draft, VPA validator, grounding, prompt, and integration tests pass.
- The complete selected test suite passes in the `pa-agent-opt` environment.
- The latest frozen record is replayed through deterministic validators where supported; no live API call is required.

