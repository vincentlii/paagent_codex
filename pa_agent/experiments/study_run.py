"""Study Host entry point. Default is offline preflight; --execute calls providers."""
import argparse
import json
import os
from pathlib import Path

from pa_agent.config.settings import load_settings
from pa_agent.experiments.study_host import StudyHostExecutor
from pa_agent.experiments.study_protocol import _load_registration, run_study, summarize_study, StudyPlan
from pa_agent.experiments.study_resources import domain_resources, input_from_evidence, study_domain_fingerprint
from pa_agent.util.threading import CancelToken


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--settings", required=True, type=Path)
    parser.add_argument("--prompt-dir", required=True, type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--paper", action="store_true")
    args = parser.parse_args(argv)
    if not args.settings.is_file():
        raise ValueError("existing study settings required")
    settings = load_settings(args.settings)
    root = args.root.resolve()
    registration = _load_registration(root)
    plan = StudyPlan.model_validate(registration["plan"])
    fingerprint = study_domain_fingerprint(args.prompt_dir, settings)
    if fingerprint != plan.domain_fingerprint:
        raise ValueError("registered domain fingerprint differs from runtime")
    resources = domain_resources(args.prompt_dir)
    for sample in registration["samples"]:
        if sample["evidence"]["knowledge"] != resources:
            raise ValueError("registered knowledge differs from runtime")
        input_from_evidence(sample["evidence"])
    if not args.execute:
        print(json.dumps({"preflight": "passed", "provider_calls": 0,
                          "assigned": len(registration["assignments"])}))
        return 0
    factory = None
    codex_conditions = [c for c in (plan.a, plan.b) if c.backend == "codex_agent"]
    if codex_conditions:
        codex_condition = codex_conditions[0]
        from pa_agent.agent_runtime.production import build_codex_experiment_backend_factory
        factory, _ = build_codex_experiment_backend_factory(
            state_dir=root / "runtime", prompt_dir=args.prompt_dir,
            environment={**os.environ, "PA_AGENT_CODEX_ENABLED": "1",
                         "PA_AGENT_CODEX_MODEL": codex_condition.model,
                         "PA_AGENT_CODEX_REASONING": codex_condition.reasoning},
        )
    execute = StudyHostExecutor(
        settings=settings, prompt_dir=args.prompt_dir, experience_dir=root / "experience",
        state_dir=root / "runtime", domain_fingerprint=fingerprint,
        cancel_token=CancelToken(), codex_backend_factory=factory,
    )
    on_completed = None
    if args.paper:
        if not settings.paper_trading.enabled:
            raise ValueError("study Paper settings must be enabled before execution")
        from pa_agent.experiments.study_paper import bind_study_paper
        on_completed = lambda case_id: bind_study_paper(
            root, root / "paper.sqlite3", settings.paper_trading, case_id=case_id,
        )
    run_study(root, execute, on_completed=on_completed)
    report = summarize_study(root)
    print(json.dumps(report["coverage"]))
    return int(report["coverage"]["failed"] > 0 or report["coverage"]["missing"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
