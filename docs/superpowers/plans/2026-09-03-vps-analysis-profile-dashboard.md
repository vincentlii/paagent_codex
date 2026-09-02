# VPS Analysis Profile Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a VPS dashboard that safely selects DeepSeek/Codex model profiles, runs one fixed XAUUSDT 1h + HTF 4h + VPA Shadow analysis at a time, and proves the packaged Codex AGENTS/Skills actually govern live jobs.

**Architecture:** A credential-free, versioned `AnalysisProfile` is staged by the loopback Dashboard and applied by Headless only at an idle boundary. Direct and Codex model identities remain separate, are frozen into each task/job, and are audited end-to-end; Dashboard never writes production settings, credentials, source, database, or systemd state.

**Tech Stack:** Python 3.11+, Pydantic v2, stdlib HTTP server/HTML/CSS/JavaScript, pytest, systemd, Codex CLI.

**Spec:** `docs/superpowers/specs/2026-09-03-vps-analysis-profile-dashboard-design.md`

## Global Constraints

- Fixed market scope is Binance USD-M `XAUUSDT`, main timeframe `1h`, HTF `4h` with 60 closed bars, VPA `shadow`, one asset and concurrency 1.
- Brooks PA remains primary; VPA remains independent secondary Shadow evidence; Codex is only a bounded contextual reasoning Backend.
- Stage 1, Stage 2 and VPA use separate ephemeral tasks over the same Frozen Snapshot and never share chat history.
- Dashboard never reads or writes API keys, `/etc/pa-agent`, production settings, databases, Prompt, AGENTS, Skills, Knowledge, Risk, Validator, Router or Trader Equation.
- Profile changes apply only when the scheduler is idle; already frozen tasks retain their full identity.
- Unknown model/effort combinations, stale generations, runtime asset drift and unavailable models fail closed without silent fallback.
- Production remains Paper-only and paused until the user reviews both DeepSeek and Codex smoke results.
- New production behavior is implemented test-first; each task is independently reviewed and committed.

---

### Task 1: Persist the Codex Backend North Star and Amend Architecture Decisions

**Files:**
- Create: `docs/knowledge/codex-analysis-north-star.md`
- Modify: `AGENTS.md`
- Modify: `CONTEXT.md`
- Modify: `docs/knowledge/README.md`
- Modify: `docs/architecture/ADR-0024-codex-analysis-backend.md`
- Modify: `docs/knowledge/current-status.md`

**Interfaces:**
- Consumes: accepted design spec and ADR-0024.
- Produces: a mandatory long-lived rationale reference for later agents and context compaction.

- [ ] Write `codex-analysis-north-star.md` explaining: why Agentic Analysis is being tested; why Direct LLM remains the control; why AGENTS defines immutable authority; why role Skills encode procedure rather than trading knowledge; why Evidence-on-demand replaces monolithic prompt concatenation; how Frozen Snapshot/no-lookahead/replay are retained; why improvements must be measured by factual accuracy, omissions, calibration, schema compliance, latency/cost and Paper outcomes rather than prose quality.
- [ ] Add an `AGENTS.md` mandatory-read rule requiring the North Star before any Codex Backend, Dashboard, runtime rule, Skill or deployment change.
- [ ] Link the North Star from `CONTEXT.md` and the knowledge index.
- [ ] Amend ADR-0024 Dashboard control to permit only the versioned credential-free Analysis Profile from the accepted spec; retain every other authority prohibition.
- [ ] Update current status to distinguish implemented local Backend, pending Profile UI and pending live Codex analysis evidence.
- [ ] Run `rg -n "Codex Analysis|North Star|Analysis Profile" AGENTS.md CONTEXT.md docs/knowledge docs/architecture/ADR-0024-codex-analysis-backend.md` and `git diff --check`.
- [ ] Commit with `docs: preserve Codex analysis north star`.

### Task 2: Add the Versioned Analysis Profile and Capability Catalog

**Files:**
- Create: `pa_agent/operations/analysis_profile.py`
- Modify: `pa_agent/operations/runtime_state.py`
- Test: `tests/unit/test_analysis_profile.py`
- Modify: `tests/unit/test_runtime_control.py`

**Interfaces:**
- Produces: `AnalysisProfile`, `ModelCapability`, `analysis_capabilities()`, `validate_analysis_profile()`, `profile_fingerprint()`, and schema-v3 `RuntimeControlState` carrying desired Profile and run-once generation.
- Consumes: `_atomic_write_json()` and the existing desired/applied generation protocol.

- [ ] Write failing tests proving the default Profile is exactly DeepSeek Flash + thinking high, Codex `gpt-5.6-sol` high, fixed XAUUSDT/1h/4h/VPA Shadow, and contains no credential/path fields.
- [ ] Run the focused test and verify failure because the profile module is absent.
- [ ] Implement frozen Pydantic/dataclass profile types and a server-side catalog: DeepSeek `deepseek-v4-flash|deepseek-v4-pro`; Codex `gpt-5.6-sol|terra|luna` with `none|low|medium|high|xhigh|max`, `gpt-5.5|gpt-5.4|gpt-5.4-mini` with their supported bounded efforts. Keep display labels separate from model IDs.
- [ ] Add tests for unknown fields/models, invalid model-effort pairs, canonical SHA-256 fingerprint stability and catalog serialization.
- [ ] Extend RuntimeControlState to schema 3 with `analysis_profile`, `run_once_generation`, and atomic compare-and-set methods `set_analysis_profile(profile, expected_generation, actor)` and `request_run_once(expected_generation, actor)`; migrate schema 1/2 to the default Profile.
- [ ] Add red/green tests for stale generation conflict, failed validation preserving the previous file, corrupt state fail-closed, and run-once monotonicity.
- [ ] Run `pytest tests/unit/test_analysis_profile.py tests/unit/test_runtime_control.py -q` and `git diff --check`.
- [ ] Commit with `feat: add versioned analysis profiles`.

### Task 3: Apply Profiles at the Headless Idle Boundary and Freeze Runtime Identity

**Files:**
- Modify: `pa_agent/monitoring/production.py`
- Modify: `pa_agent/monitoring/headless.py`
- Modify: `pa_agent/headless.py`
- Modify: `pa_agent/monitoring/task_factory.py`
- Modify: `pa_agent/agent_runtime/production.py`
- Modify: `pa_agent/agent_runtime/frozen_job.py`
- Modify: `pa_agent/agent_runtime/spool_worker.py`
- Modify: `pa_agent/agent_runtime/codex_worker.py`
- Test: `tests/unit/test_headless_monitor.py`
- Test: `tests/unit/test_multi_asset_service.py`
- Test: `tests/unit/test_codex_production_runtime.py`
- Test: `tests/unit/test_codex_spool_worker.py`
- Test: `tests/unit/test_codex_cli_worker.py`

**Interfaces:**
- Consumes: Task 2 `AnalysisProfile` and fingerprint.
- Produces: `ProductionOrchestratorFactory.apply_analysis_profile(profile) -> bool`, profile-aware frozen task/job identity, and one-shot consumption while the service remains paused.

- [ ] Write failing tests showing a profile cannot apply while a task is active, stale desired state is not applied, and idle application rebuilds only future Direct clients with the selected DeepSeek model/thinking/effort.
- [ ] Implement `apply_analysis_profile` using a validated deep settings copy; never mutate API key/base URL or persisted settings. Clear prepared snapshots after a successful profile/backend change.
- [ ] Write failing tests proving run-once submits exactly one task while desired state remains paused, rejects duplicates/in-flight/pending profile, and acknowledges the consumed generation in status.
- [ ] Implement one-shot consumption at the monitor loop safe boundary without launching a second Headless process.
- [ ] Write failing tests proving selected Codex model/effort are frozen into the analysis task, `FrozenAgentJob`, manifest/runtime fingerprint and worker command.
- [ ] Change Codex publication/worker execution from process-wide fixed model/effort to validated per-job values. Retain a local server allowlist as a second check; mismatch fails closed.
- [ ] Add `none` and `max` only for catalog models confirmed to support them; do not add `ultra` unless the installed VPS CLI and selected model prove it is accepted.
- [ ] Preserve full-chain cold fallback semantics: only availability failures may fallback, and fallback restarts Direct from Stage 1 using the same Snapshot and selected Direct Profile.
- [ ] Run the focused Headless/production/Codex tests and `git diff --check`.
- [ ] Commit with `feat: apply model profiles at safe runtime boundaries`.

### Task 4: Build the Authenticated Profile Dashboard and Run-Once Control

**Files:**
- Modify: `pa_agent/dashboard.py`
- Modify: `pa_agent/dashboard_page.py`
- Modify: `tests/unit/test_dashboard.py`

**Interfaces:**
- Consumes: Task 2 control store/capability catalog and Task 3 applied status fields.
- Produces: `GET /api/status` capabilities/profile projection, `POST /api/profile`, and `POST /api/run-once`.

- [ ] Write failing HTTP tests for capability projection, credential-free current/pending/applied Profile, expected-generation 409, unknown-field/model 400, unavailable runtime 503 and run-once 202/409.
- [ ] Implement endpoints while retaining Basic Auth, constant-time token comparison, loopback binding, Origin validation, custom control header, CSP and request size limit.
- [ ] Redesign the page as a compact trading operations console with fixed market scope, separate DeepSeek and Codex cards, model-aware effort selects, current/pending/applied generation, visible diff, apply action and run-once action.
- [ ] Ensure the UI never offers VPA off or HTF off, never allows arbitrary model strings, disables actions for stale/unapplied/in-flight states, and renders errors with `textContent` only.
- [ ] Extend recent-run rows with Backend, requested/effective model, effort, total latency, repair count and outcome when available.
- [ ] Add DOM/source assertions for required controls and forbidden secret/raw reasoning fields.
- [ ] Run `pytest tests/unit/test_dashboard.py tests/unit/test_runtime_control.py tests/unit/test_analysis_profile.py -q` and `git diff --check`.
- [ ] Commit with `feat: add model profile controls to VPS dashboard`.

### Task 5: Make the VPS Configuration Single-Asset and Prove Codex Rules/Skills

**Files:**
- Modify: `config/settings.vps.example.json`
- Create: `pa_agent/agent_runtime/runtime_audit.py`
- Modify: `pa_agent/agent_runtime/codex_worker.py`
- Modify: `pa_agent/headless.py`
- Modify: `deploy/install_vps.sh`
- Modify: `deploy/systemd/pa-agent-codex.env.example`
- Modify: `docs/linux_headless_deployment.md`
- Test: `tests/unit/test_codex_runtime_assets.py`
- Test: `tests/unit/test_codex_cli_worker.py`
- Test: `tests/unit/test_vps_deployment_assets.py`
- Test: `tests/unit/test_headless_cli.py`

**Interfaces:**
- Produces: `python -m pa_agent.agent_runtime.runtime_audit` static/installed digest and permission checks plus an explicit live canary command.
- Consumes: packaged AGENTS/four role Skills, capability catalog and installed Codex CLI.

- [ ] Change the VPS example to exactly Binance USD-M XAUUSDT 1h, HTF 4h/60, VPA Shadow, concurrency 1; add tests rejecting accidental extra enabled assets or disabled VPA/HTF.
- [ ] Write failing tests for a runtime audit that verifies packaged AGENTS, all four Skills, frontmatter/name, required authority/no-lookahead/tool prohibitions, deterministic digest and installed-resource parity.
- [ ] Resolve `--ignore-rules` against the installed CLI contract: preserve user/global-rule isolation without suppressing the workspace Runtime `AGENTS.md`. Add a command-construction test and a VPS live canary where a unique harmless AGENTS instruction must be reflected in a schema field; fail deployment if it is not observed.
- [ ] Add static permission expectations for Codex user, job, source, credential, DB, runtime and outbox paths without exposing secret contents.
- [ ] Expose runtime audit in Headless preflight and installer instructions; installation remains non-starting.
- [ ] Run focused runtime/deployment tests, `python -m compileall -q pa_agent`, `bash -n deploy/install_vps.sh` where bash is available, and `git diff --check`.
- [ ] Commit with `feat: audit constrained Codex runtime assets`.

### Task 6: Integration Regression, Documentation and Release Audit

**Files:**
- Modify: `docs/knowledge/current-status.md`
- Modify: `docs/knowledge/operations-and-debugging.md`
- Modify: `docs/knowledge/research-and-evaluation.md`
- Create: `docs/quality/vps_profile_dashboard_audit_20260903.md`
- Test: relevant existing integration/unit suites.

**Interfaces:**
- Consumes: Tasks 1-5.
- Produces: release evidence and exact operator workflow for DeepSeek/Codex smoke comparison.

- [ ] Run focused regression covering Dashboard, runtime control/profile, Headless, Direct production factory, Codex publisher/worker/runtime assets, VPA Shadow, HTF, records/Paper and backend A/A/B/B.
- [ ] Run `python -m compileall -q pa_agent tests` and `git diff --check`.
- [ ] Document exact SSH-tunnel, pause/apply/run-once, model selection, result interpretation and rollback-to-DeepSeek steps without embedding credentials or server secrets.
- [ ] Write the release audit with pass/fail evidence, known limitations, security review and three-role review: software architect, quantitative engineer and professional trader.
- [ ] Update current status and research protocol: smoke results prove operability only; model promotion still requires registered Frozen Snapshot evidence and Paper gates.
- [ ] Commit with `docs: audit VPS model profile release`.

### Task 7: Deploy, Verify and Run Controlled VPS Smokes

**Files:**
- VPS: `/opt/pa-agent` deployed commit.
- VPS: `/var/lib/pa-agent/config/settings.json` validated single-asset configuration.
- VPS: `/etc/pa-agent/pa-agent.env` and `/etc/pa-agent/pa-agent-codex.env` preserved with secure modes.
- VPS: systemd units and operation directories.

**Interfaces:**
- Consumes: Task 6 verified commit and existing user-authorized VPS/ChatGPT/DeepSeek credentials.
- Produces: deployed services, reachable tunnel-only Dashboard and two auditable smoke records.

- [ ] Restore and fingerprint the intended SSH key; inspect current services, disk/memory, authorized keys and temporary deployment artifacts before changing anything.
- [ ] Transfer the exact verified commit, run installer/update in place, and preserve existing credentials and databases. Remove only explicitly identified obsolete deployment public keys/temp bundle after confirming the active key.
- [ ] Validate ownership/mode and prove `pa-agent-codex` cannot read source write paths, DeepSeek key, PA databases or Paper ledger.
- [ ] Run Headless `--check`, market data probe, Codex static runtime audit, installed-resource digest check, auth check and live AGENTS/Skill canary.
- [ ] Start Dashboard and Codex Worker; keep Headless paused. Establish an SSH tunnel to `127.0.0.1:8765`.
- [ ] From Dashboard apply DeepSeek Flash with agreed thinking/effort and request one analysis. Record Snapshot hash, Stage outcomes, VPA/HTF presence, repairs, effective model, Token and latency.
- [ ] Apply one supported Codex model/effort and request one new analysis. Record the same fields plus Evidence calls and Runtime asset digest.
- [ ] Optionally run DeepSeek Pro only after the two basic smokes complete; label different-Snapshot comparisons as operational rather than causal.
- [ ] Leave long-running Headless paused for user review; keep Dashboard and Worker active if healthy.
- [ ] Append sanitized deployment evidence and exact deployed commit to the release audit, run final local `git status -sb`, and commit only documentation changes that contain no secrets.
