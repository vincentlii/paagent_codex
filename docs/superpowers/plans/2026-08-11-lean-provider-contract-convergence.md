# Lean Provider Contract Convergence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build independent lean PA provider contracts with one-source prompt,
validation, repair, and deterministic canonical materialization.

**Architecture:** `pa_provider_contracts.py` owns explicit Stage 1/2 provider
schemas and their prompt rendering. `JsonValidator` aggregates provider-contract
errors, `validation_retry` performs one bounded field-directed repair, and the
existing canonical schemas remain the stable interface for all downstream code.

**Tech Stack:** Python 3.13, JSON Schema Draft 7, pytest, existing PA Agent
orchestrator and record models.

## Global Constraints

- Program facts and arithmetic remain deterministic; PA/VPA interpretation
  remains model-owned.
- Brooks Router remains the only strategy router.
- VPA cannot create direction, Setup, Gate, price, win rate, or order.
- No new dependency and no asset-, timeframe-, or sample-specific exception.
- Canonical records and old-record reads remain compatible.

---

### Task 1: Independent Provider Contracts

**Files:**
- Modify: `pa_agent/ai/pa_provider_contracts.py`
- Test: `tests/unit/test_pa_provider_contracts.py`

**Interfaces:**
- Produces: `schema_for_provider_draft(stage)` and
  `render_provider_draft_contract(stage)`.
- Preserves: `materialize_stage1_draft` and `materialize_stage2_draft`.

- [ ] Add failing tests proving Draft schemas are independently defined, omit
  program-owned fields, retain every model-owned semantic field, and render a
  contract containing exact required fields, types, enums, and nesting.
- [ ] Run focused tests and confirm they fail because the current Draft schemas
  are canonical-schema copies and no renderer exists.
- [ ] Define explicit Stage 1/2 Draft schemas and the deterministic renderer.
- [ ] Update materializers only where the new Draft shape requires adaptation;
  do not infer missing market semantics.
- [ ] Run focused tests and canonical schema validation for Stage 1 and all
  Stage 2 intents.

### Task 2: One-Source Prompt Contract

**Files:**
- Modify: `pa_agent/ai/prompt_assembler.py`
- Modify only contradictory output wording in:
  `prompt_engineering/市场诊断框架.txt`
- Test: `tests/unit/test_prompt_assembler.py`

**Interfaces:**
- Consumes: `render_provider_draft_contract(stage)`.
- Produces: initial and incremental prompts with the exact runtime contract.

- [ ] Add failing tests that extract the prompt contract and compare its schema
  digest to `schema_for_provider_draft(stage)` for Stage 1 and Stage 2.
- [ ] Confirm current hand-written reminders fail the digest tests.
- [ ] Replace duplicate field-shape prose with the generated compact contract;
  retain professional PA instructions and field ownership explanations.
- [ ] Remove old wording that asks Draft v2 to output program-owned top-level
  fields.
- [ ] Run prompt tests and inspect prompt size deltas.

### Task 3: Aggregated Contract Validation And Repair

**Files:**
- Modify: `pa_agent/ai/json_validator.py`
- Modify: `pa_agent/ai/retry_policy.py`
- Modify: `pa_agent/ai/retry_feedback.py`
- Modify: `pa_agent/orchestrator/validation_retry.py`
- Test: `tests/unit/test_validation_retry.py`
- Test: `tests/unit/test_pa_provider_contracts.py`

**Interfaces:**
- Produces: one `ValidationError` containing every provider-schema error with
  full paths and enum choices.
- Guarantees: at most one contract repair and immutable unreported semantics.

- [ ] Add a failing DeepSeek-style Stage 1 fixture with multiple simultaneous
  schema violations and assert all paths are returned together.
- [ ] Add a failing retry test proving the current first-error response performs
  no repair.
- [ ] Aggregate errors, classify provider shape failures as repairable contract
  errors, and include the exact schema in repair feedback.
- [ ] Preserve the complete prior provider draft for repair; remove only fields
  explicitly reported as unexpected and mark only reported invalid/missing
  paths editable.
- [ ] Extend immutable checks to diagnosis confidence and all primary hypothesis
  semantics unless validation named them.
- [ ] Run focused validation and retry tests.

### Task 4: VPA Repair Ownership

**Files:**
- Modify: `pa_agent/orchestrator/role_call.py`
- Test: the existing VPA role-call unit test module located by `rg role_call tests`.

**Interfaces:**
- Preserves: bounded VPA repair and VPA authority enforcement.
- Changes: stable-field restoration excludes fields named by validation.

- [ ] Add a failing test where VPA validation reports a protected contextual
  field and the repaired response changes only that field.
- [ ] Confirm current restoration reinstates the invalid first value.
- [ ] Filter restored fields by normalized feedback paths while retaining hard
  rejection for authority violations.
- [ ] Run VPA role validation, Shadow degradation, and authority tests.

### Task 5: End-To-End Replay And Compatibility

**Files:**
- Modify: `tests/integration/test_two_stage_happy_path.py`
- Create if needed: `tests/fixtures/deepseek_style_provider_drafts.py`
- Update: `docs/architecture/ADR-0018-evidence-context-and-provider-drafts.md`
- Update: `CONTEXT.md`

**Interfaces:**
- Verifies: provider draft to canonical record to PA/VPA handoff.

- [ ] Add a DeepSeek-style contract-repair integration path without network
  access and verify Stage 1 continues into Stage 2 after one repair.
- [ ] Cover Stage 2 trade, wait, and reject canonical outputs.
- [ ] Verify legacy canonical replies and historical records remain readable.
- [ ] Replay the latest failed raw Stage 1 response and assert all differences
  are reported in one validation result.
- [ ] Run focused tests, non-GUI unit tests, offline integration tests, static
  checks available in the environment, and `git diff --check`.
- [ ] Remove temporary files and update ADR-0018/`CONTEXT.md` with the final
  contract and verification commands.

