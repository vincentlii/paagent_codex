# ADR-0016: Role-Based Reasoning Profiles

- Status: Accepted
- Date: 2026-08-04

## Context

The PA Agent has several model roles with different responsibilities and latency
budgets: PA Stage 1 frames the market, PA Stage 2 forms the executable baseline,
VPA Evidence reviews independent volume evidence, Joint Arbiter is research-only,
and Free Chat is non-decision support. A single provider-wide thinking setting
forces every role to pay the same reasoning cost and makes latency tuning hard to
audit.

## Decision

1. Keep the existing provider-wide `thinking` and `reasoning_effort` fields as
   the backward-compatible default.
2. Add optional `provider.reasoning_profiles` overrides for `stage1`, `stage2`,
   `vpa_evidence`, `joint_arbiter`, and `free_chat`. Missing fields inherit the
   provider-wide value; an override changes request controls only.
3. Apply the resolved profile to the initial call and every validation retry for
   the same role. The prompt, PA/VPA schemas, router, validator authority, order
   mathematics, and execution policy are unchanged.
4. Persist both the role-requested controls and the provider-effective controls
   in the provider call ledger and role runtime metadata. Native DeepSeek maps
   requested `low`/`medium` to effective `high`; the record must show both rather
   than presenting the requested alias as the wire value. Reasoning-profile configuration participates
   in provider/cohort fingerprints so paired experiments cannot silently combine
   different request controls.
5. When thinking is disabled, do not send a reasoning effort to the provider.
   For native DeepSeek V4, `low` and `medium` are compatibility aliases for
   `high`; `max` remains the distinct higher setting. The application does not
   claim that an alias reduces latency.

## Consequences

- Existing settings preserve their behavior because all roles inherit the global
  setting until an override is explicitly configured.
- Production Shadow remains PA-only officially; VPA remains an independent
  evidence role, and Joint remains research-only.
- Lower latency is not free: disabling thinking can reduce complex PA/VPA
  reasoning quality. Role settings are therefore an experiment and operations
  control, not a trading rule.

## Verification

- Settings round-trip tests cover absent and explicit role profiles.
- Model-call tests verify the exact per-call thinking arguments and ledger fields.
- Orchestrator tests verify distinct Stage 1, Stage 2, VPA, and Joint profiles
  without changing the decision contract.
