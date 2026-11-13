import argparse
import json
import sys

import yaml
from pydantic import ValidationError

from llm_bias.db import db, init_db
from llm_bias.jobspec import load_job_spec, resolved_config
from llm_bias.models import RunRequest
from llm_bias.report import generate_report
from llm_bias.runs import (
    create_run,
    get_run_progress,
    list_run_names,
    run_name_exists,
)


def _job_run(args: argparse.Namespace) -> int:
    init_db()
    try:
        spec = load_job_spec(args.file)
    except (OSError, ValueError, yaml.YAMLError, ValidationError) as exc:
        print(f"llm-bias job: invalid spec: {exc}", file=sys.stderr)
        return 1

    if not args.force and run_name_exists(spec.name):
        print(
            f"llm-bias job: run name {spec.name!r} already exists; use --force",
            file=sys.stderr,
        )
        return 1

    resolved = resolved_config(spec)

    if args.dry_run:
        print(f"name: {spec.name}")
        print(f"experiment: {spec.experiment}")
        print(f"answer_type: {spec.answer_type}")
        print(f"models: {', '.join(spec.models)}")
        print(f"prompts: {len(spec.prompts)}")
        print(f"samples_per_prompt: {spec.samples_per_prompt}")
        print(f"runs to create: {len(spec.models)}")
        print(f"config: {json.dumps(resolved, sort_keys=True)}")
        return 0

    run_ids = []
    for model in spec.models:
        req = RunRequest(
            experiment=spec.experiment,
            model=model,
            prompts=spec.prompts,
            samples_per_prompt=spec.samples_per_prompt,
            answer_type=spec.answer_type,
            name=spec.name,
            temperature=spec.params.temperature,
            top_p=spec.params.top_p,
            reasoning_effort=spec.params.reasoning_effort,
        )
        run_ids.append(create_run(req))

    print(f"created {len(run_ids)} runs: {run_ids}")
    return 0


def _job_list(_args: argparse.Namespace) -> int:
    init_db()
    for row in list_run_names():
        name = row["name"] or "<unnamed>"
        print(f"{name}: {row['runs']} runs, {row['done']} done")
    return 0


def _job_status(args: argparse.Namespace) -> int:
    init_db()
    if args.name is None:
        for row in list_run_names():
            name = row["name"] or "<unnamed>"
            print(f"{name}: {row['runs']} runs, {row['done']} done")
        return 0

    rows = get_run_progress(args.name)
    if not rows:
        print(f"llm-bias job: no runs named {args.name!r}", file=sys.stderr)
        return 1
    for row in rows:
        expected = row["n_prompts"] * row["samples_per_prompt"]
        print(f"run {row['id']}: {row['model']} status={row['status']} samples={row['n_samples']}/{expected}")
    return 0


def _report(args: argparse.Namespace) -> int:
    init_db()
    out_dir = args.out_dir
    if args.all or args.experiment is None:
        with db() as conn:
            names = [r["experiment"] for r in conn.execute("select distinct experiment from run").fetchall()]
        if not names:
            print("no experiments found", file=sys.stderr)
            return 1
        for name in names:
            path = generate_report(experiment=name, out_dir=out_dir)
            print(str(path))
        return 0
    path = generate_report(experiment=args.experiment, out_dir=out_dir)
    print(str(path))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="llm-bias")
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    job_parser = subparsers.add_parser("job", help="run and inspect YAML experiments")
    job_sub = job_parser.add_subparsers(dest="job_command", metavar="COMMAND")
    job_sub.required = True

    run_parser = job_sub.add_parser("run", help="expand a YAML file into runs")
    run_parser.add_argument("file", help="path to a YAML job spec")
    run_parser.add_argument("--dry-run", action="store_true", help="print the plan without writing")
    run_parser.add_argument("--force", action="store_true", help="allow a duplicate run name")
    run_parser.set_defaults(handler=_job_run)

    list_parser = job_sub.add_parser("list", help="list run names")
    list_parser.set_defaults(handler=_job_list)

    status_parser = job_sub.add_parser("status", help="show progress for a run name")
    status_parser.add_argument("name", nargs="?", help="run name to inspect")
    status_parser.set_defaults(handler=_job_status)

    report_parser = subparsers.add_parser("report", help="generate a Plotly HTML report")
    report_parser.add_argument("experiment", nargs="?", default=None, help="experiment name (default: all)")
    report_parser.add_argument("--all", action="store_true", help="generate a report for every experiment")
    report_parser.add_argument("--out-dir", default=None, help="output directory")
    report_parser.set_defaults(handler=_report)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
