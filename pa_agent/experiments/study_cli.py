"""Offline research artifact CLI. No provider, market or trading calls."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pa_agent.experiments.backend_review import (
    export_study_review,
    freeze_review_evidence,
    score_blinded_backend_review,
    unblind_backend_scores,
)
from pa_agent.experiments.study_protocol import (
    register_study,
    release_holdout,
    summarize_outcomes,
    summarize_study,
    write_once,
)
from pa_agent.records.schema import AnalysisRecord


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    freeze = commands.add_parser("freeze")
    for name in ("record", "knowledge", "measurements", "output"):
        freeze.add_argument(name, type=Path)
    register = commands.add_parser("register")
    register.add_argument("spec", type=Path)
    register.add_argument("root", type=Path)
    review = commands.add_parser("review")
    for name in ("root", "public_dir", "key_path", "salt_file"):
        review.add_argument(name, type=Path)
    review.add_argument("--split", choices=("development", "holdout"), default="development")
    for name, inputs in (("score", ("packet", "ratings")), ("unblind", ("scores", "key"))):
        command = commands.add_parser(name)
        for field in (*inputs, "output"):
            command.add_argument(field, type=Path)
    coverage = commands.add_parser("coverage")
    coverage.add_argument("root", type=Path)
    outcomes = commands.add_parser("outcomes")
    outcomes.add_argument("root", type=Path)
    outcomes.add_argument("outcomes", type=Path)
    outcomes.add_argument("--split", choices=("development", "holdout"), default="development")
    release = commands.add_parser("release-holdout")
    release.add_argument("root", type=Path)
    release.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    if args.command == "freeze":
        result = freeze_review_evidence(
            AnalysisRecord.model_validate(_read(args.record)),
            knowledge=_read(args.knowledge),
            measurements=_read(args.measurements),
        )
        write_once(args.output, result)
    elif args.command == "register":
        spec = _read(args.spec)
        result = register_study(args.root, spec["plan"], spec["samples"])
    elif args.command == "review":
        export_study_review(
            args.root,
            args.public_dir,
            args.key_path,
            blinding_salt=args.salt_file.read_text(encoding="utf-8").strip(),
            split=args.split,
        )
    elif args.command == "score":
        write_once(
            args.output, score_blinded_backend_review(_read(args.packet), _read(args.ratings))
        )
    elif args.command == "unblind":
        write_once(args.output, unblind_backend_scores(_read(args.scores), _read(args.key)))
    elif args.command == "release-holdout":
        release_holdout(args.root, reason=args.reason)
    else:
        result = (
            summarize_study(args.root)
            if args.command == "coverage"
            else summarize_outcomes(args.root, _read(args.outcomes), split=args.split)
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
