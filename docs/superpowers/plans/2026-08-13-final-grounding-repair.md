# Final Grounding Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the last deterministic PA/VPA fact contradictions and avoid semantically pointless LLM retries before paper trading.

**Architecture:** Extend the existing provider-contract, Evidence Graph, and structured-warning pipeline. Add only reproducible metadata and narrow normalization; keep PA/VPA labels and trade arbitration in the LLM.

**Tech Stack:** Python 3.11, Pydantic/JSON Schema, pytest.

## Global Constraints

- Do not add a PA or VPA strategy router, threshold-based Setup, or trade Gate.
- Evidence first, hypothesis plural, label last.
- Preserve Shadow `official_path=pa_only` behavior.
- Existing user/worktree changes must be preserved.

---

### Task 1: No-trade contract normalization

**Files:**
- Modify: `pa_agent/ai/json_validator.py`
- Modify if schema alignment requires it: `pa_agent/ai/pa_provider_contracts.py`
- Test: `tests/unit/test_pa_provider_contracts.py`

- [x] Add a failing test proving `trade_confidence_reasoning=null` on `order_type=不下单` is normalized without retry.
- [x] Run the exact test and confirm a contract failure.
- [x] Add the narrow canonicalization and normalization audit event.
- [x] Run provider-contract and validation-retry tests.

### Task 2: Historical follow-through chronology

**Files:**
- Modify: `pa_agent/ai/vpa_measurements.py` or the existing VPA catalog builder selected by code inspection
- Modify: `pa_agent/ai/role_validator.py`
- Modify: the existing VPA prompt knowledge/assembler module selected by code inspection
- Test: existing VPA catalog and role-validator unit tests

- [x] Add failing tests for deterministic later-closed-bar metadata and a false `no later closed bar` claim.
- [x] Confirm failures are caused by missing chronology support.
- [x] Add compact chronology metadata and one exact contradiction check.
- [x] Confirm interpretive absorption/exhaustion prose remains warning-only and unrestricted.

### Task 3: Window attribution and comparator magnitude

**Files:**
- Modify: `pa_agent/ai/coherence_checks.py`
- Modify: the existing VPA measurement/catalog module
- Modify: VPA prompt guidance in its existing module
- Test: `tests/unit/test_coherence_checks.py` and VPA measurement/validator tests

- [x] Add failing tests for attributing a shorter-window range to a longer window and for near-baseline deviation metadata.
- [x] Confirm both tests fail for the intended missing behavior.
- [x] Implement exact window-bound price checks and signed deviation fields.
- [x] Add concise prompt guidance that comparison direction alone does not establish strong activity.
- [x] Run all directly related tests.

### Task 4: Integration and release verification

**Files:**
- Modify: `CONTEXT.md`
- Modify: `docs/architecture/ADR-0018-evidence-context-and-provider-drafts.md`
- Modify: `docs/wyckoff_vpa_integration.md`
- Test: full `tests` tree

- [x] Document the deterministic/interpretive boundary and new metadata.
- [x] Run targeted tests for every changed module.
- [x] Run `python -B -m pytest -q` with a workspace-local basetemp in `pa-agent-opt`.
- [x] Run `git diff --check` and remove test/temp artifacts.
- [x] Report remaining model-only uncertainty and whether deployment gates are met.
