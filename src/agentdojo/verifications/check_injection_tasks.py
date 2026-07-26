from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import load_dotenv

from agentdojo.agent_pipeline.agent_pipeline import AgentPipeline, PipelineConfig
from agentdojo.benchmark import run_task_without_injection_tasks
from agentdojo.logging import OutputLogger
from agentdojo.models import ModelsEnum
from agentdojo.task_suite.load_suites import get_suite


def _normalize_model(model: str) -> str:
    try:
        return ModelsEnum[model].value
    except KeyError:
        return ModelsEnum(model).value


def _split_task_ids(task_ids: list[str] | None) -> list[str] | None:
    if not task_ids:
        return None
    parsed: list[str] = []
    for item in task_ids:
        parsed.extend(part for part in item.replace(",", " ").split() if part)
    return parsed or None


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run injection tasks as standalone clean prompts.")
    parser.add_argument("--suite", default="travel")
    parser.add_argument("--benchmark-version", default="v0")
    parser.add_argument("--model", default="DEEPSEEK_V4_FLASH")
    parser.add_argument("--model-id", default=None)
    parser.add_argument("--tool-delimiter", default="tool")
    parser.add_argument("--logdir", type=Path, default=Path("runs/straight_injection_tasks"))
    parser.add_argument("--force-rerun", action="store_true")
    parser.add_argument("--injection-task-ids", nargs="*", default=None)
    parser.add_argument("--env-file", default=".env")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    load_dotenv(args.env_file)

    model = _normalize_model(args.model)
    suite = get_suite(args.benchmark_version, args.suite)
    task_ids = _split_task_ids(args.injection_task_ids) or sorted(suite.injection_tasks.keys())

    pipeline = AgentPipeline.from_config(
        PipelineConfig(
            llm=model,
            model_id=args.model_id,
            defense=None,
            tool_delimiter=args.tool_delimiter,
            system_message_name=None,
            system_message=None,
        )
    )

    results: dict[str, bool] = {}
    with OutputLogger(str(args.logdir)):
        for task_id in task_ids:
            task = suite.get_injection_task_by_id(task_id)
            utility, security = run_task_without_injection_tasks(
                suite=suite,
                agent_pipeline=pipeline,
                task=task,
                logdir=args.logdir,
                force_rerun=args.force_rerun,
                benchmark_version=args.benchmark_version,
            )
            results[task_id] = utility
            print(f"{task_id}: utility={utility} security={security}")

    passed = sum(results.values())
    total = len(results)
    rate = passed / total * 100 if total else 0.0
    print("============================================================")
    print(f"Standalone injection-task pass rate: {passed}/{total} ({rate:.2f}%)")
    for task_id, utility in results.items():
        print(f"  {task_id}: {utility}")
    print("============================================================")


if __name__ == "__main__":
    main()
