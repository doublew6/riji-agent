"""Prepare or execute private synthetic EvalMesh batches with pinned source fixtures."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from evalmesh_support.suite import SuiteOptions, prepare, run_suite


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evalmesh-source", type=Path)
    parser.add_argument("--selection", choices=("boundary", "smoke", "quality-regression", "candidate", "all"), default="boundary")
    parser.add_argument("--provider", choices=("deepseek", "codex"), default="deepseek")
    parser.add_argument("--model", default="deepseek-reasoner")
    parser.add_argument("--memory-model", default="deepseek-chat")
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--run-prepared", action="store_true")
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if args.run_prepared and not args.execute:
        parser.error("--run-prepared requires --execute")
    if not args.run_prepared and args.evalmesh_source is None:
        parser.error("preparation requires --evalmesh-source")
    for model in (args.model, args.memory_model):
        if re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", model) is None:
            parser.error("invalid model identity")
    options = SuiteOptions(
        output=args.output, subject=Path(__file__).resolve().parents[1],
        harness=args.evalmesh_source or args.output / "harness", selection=args.selection, provider=args.provider,
        model=args.model, memory_model=args.memory_model, repetitions=args.repetitions,
        live=args.live, execute=args.execute, case_ids=tuple(args.case),
    )
    try:
        cases = [] if args.run_prepared else prepare(options)
        result = run_suite(options) if args.execute else {
            "mode": "prepared", "cases": len(cases),
            "attempts": len(cases) * args.repetitions, "model_calls": 0,
        }
    except Exception as error:
        print(json.dumps({"status": "evaluation_setup_failed", "category": type(error).__name__}))
        return 2
    print(json.dumps(result))
    return int(args.execute and (not result["machine_passed"] or not result["reporting_ok"]
                                 or not result["environment_ok"]))


if __name__ == "__main__":
    raise SystemExit(main())
