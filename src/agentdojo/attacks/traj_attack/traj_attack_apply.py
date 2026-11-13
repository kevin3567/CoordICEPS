import argparse
import json
import os
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from agentdojo.attacks.traj_attack.traj_attack_common import _find_repo_root
from agentdojo.attacks.traj_attack.traj_attack_paths import (
    build_traj_case_dir,
    resolve_attack_method_dir,
    resolve_rewrite_strategy_dir,
)
from agentdojo.attacks.traj_attack.traj_attack_runtime import (
    FAILURE_GENERATION,
    FAILURE_INFRASTRUCTURE,
    FailureRecord,
    PairInfo,
    PairResult,
    _classify_benchmark_exception,
    _json_safe,
    failure_result,
    get_resolved_agent_model_name,
    load_latest_pair_result,
    make_failure_record,
    save_pair_result,
    save_failure_records_jsonl,
    save_results_jsonl,
)
from agentdojo.attacks.traj_attack.traj_attack_strategy import ALLOWED_STRATEGY_MODES
from agentdojo.benchmark import load_task_results
from agentdojo.models import ModelsEnum
from agentdojo.scripts.benchmark import benchmark_suite
from agentdojo.task_suite.load_suites import get_suite


def _build_applied_case_dir(
    *,
    repo_root: Path,
    output_subdir: str | Path,
    rewrite_strategy: str,
    artifact_attack_method: str,
    eval_attack_method: str,
    artifact_attack_model_name: str | None,
    artifact_agent_model_name: str,
    eval_agent_model_name: str,
    suite_name: str,
    benchmark_version: str,
    user_task_id: str,
    injection_task_id: str,
) -> Path:
    return (
        repo_root
        / Path(output_subdir)
        / "generated_trajectory_applied"
        / resolve_rewrite_strategy_dir(rewrite_strategy)
        / "artifact_attack"
        / artifact_attack_method
        / "eval_attack"
        / eval_attack_method
        / "attack_model"
        / (artifact_attack_model_name or "unknown")
        / "artifact_agent_model"
        / artifact_agent_model_name
        / "eval_agent_model"
        / eval_agent_model_name
        / suite_name
        / benchmark_version
        / f"{user_task_id}_{injection_task_id}"
    )


def _build_applied_log_root(
    *,
    logdir: str | Path,
    rewrite_strategy: str,
    artifact_attack_method: str,
    eval_attack_method: str,
    artifact_attack_model_name: str | None,
    artifact_agent_model_name: str,
    eval_agent_model_name: str,
    suite_name: str,
    benchmark_version: str,
) -> Path:
    # AgentDojo appends eval pipeline_name/suite/user_task/attack/injection_task.json
    # below this root, so eval_agent_model_name and suite_name are not included here.
    return (
        Path(logdir)
        / "generated_trajectory_applied"
        / resolve_rewrite_strategy_dir(rewrite_strategy)
        / "artifact_attack"
        / artifact_attack_method
        / "eval_attack"
        / eval_attack_method
        / "attack_model"
        / (artifact_attack_model_name or "unknown")
        / "artifact_agent_model"
        / artifact_agent_model_name
        / benchmark_version
    )


def _build_applied_suite_dir(
    *,
    repo_root: Path,
    output_subdir: str | Path,
    rewrite_strategy: str,
    artifact_attack_method: str,
    eval_attack_method: str,
    artifact_attack_model_name: str | None,
    artifact_agent_model_name: str,
    eval_agent_model_name: str,
    suite_name: str,
    benchmark_version: str,
) -> Path:
    return (
        repo_root
        / Path(output_subdir)
        / "generated_trajectory_applied"
        / resolve_rewrite_strategy_dir(rewrite_strategy)
        / "artifact_attack"
        / artifact_attack_method
        / "eval_attack"
        / eval_attack_method
        / "attack_model"
        / (artifact_attack_model_name or "unknown")
        / "artifact_agent_model"
        / artifact_agent_model_name
        / "eval_agent_model"
        / eval_agent_model_name
        / suite_name
        / benchmark_version
    )


def _artifact_exists(
    *,
    repo_root: Path,
    output_subdir: str | Path,
    artifact_attack_method: str,
    artifact_attack_model_name: str | None,
    rewrite_strategy: str,
    artifact_agent_model_name: str,
    suite_name: str,
    benchmark_version: str,
    user_task_id: str,
    injection_task_id: str,
) -> bool:
    case_dir = build_traj_case_dir(
        repo_root=repo_root,
        output_subdir=output_subdir,
        attack_method=artifact_attack_method,
        rewrite_strategy=rewrite_strategy,
        attack_model_name=artifact_attack_model_name,
        agent_model_name=artifact_agent_model_name,
        suite_name=suite_name,
        benchmark_version=benchmark_version,
        user_task_id=user_task_id,
        injection_task_id=injection_task_id,
    )
    if (case_dir / "ij_creation_newest.json").exists():
        return True
    legacy_case_dir = build_traj_case_dir(
        repo_root=repo_root,
        output_subdir=output_subdir,
        attack_method=artifact_attack_method,
        rewrite_strategy=rewrite_strategy,
        agent_model_name=artifact_agent_model_name,
        suite_name=suite_name,
        benchmark_version=benchmark_version,
        user_task_id=user_task_id,
        injection_task_id=injection_task_id,
    )
    return (legacy_case_dir / "ij_creation_newest.json").exists()


def _set_artifact_env(args, *, artifact_attack_method: str, artifact_attack_model_name: str | None, artifact_agent_model_name: str) -> None:
    os.environ["AGENTDOJO_REWRITE_ARTIFACT_MODEL_NAME"] = artifact_agent_model_name
    if artifact_attack_model_name:
        os.environ["AGENTDOJO_REWRITE_ARTIFACT_ATTACK_MODEL_NAME"] = artifact_attack_model_name
    else:
        os.environ.pop("AGENTDOJO_REWRITE_ARTIFACT_ATTACK_MODEL_NAME", None)
    os.environ["AGENTDOJO_REWRITE_ARTIFACT_STRATEGY"] = resolve_rewrite_strategy_dir(args.rewrite_strategy)
    os.environ["AGENTDOJO_REWRITE_ARTIFACT_ATTACK_METHOD"] = artifact_attack_method
    os.environ["AGENTDOJO_REWRITE_ARTIFACT_OUT_DIR"] = str(args.artifact_out_dir)
    os.environ["AGENTDOJO_REWRITE_STRATEGY"] = resolve_rewrite_strategy_dir(args.rewrite_strategy)
    os.environ["AGENTDOJO_BENCHMARK_VERSION"] = args.benchmark_version


def apply_worker(
    suite_name: str,
    user_task_id: str,
    injection_task_id: str,
    eval_agent_model_name: str,
    applied_log_root: Path,
    benchmark_version: str,
    attack_method: str,
    defense: str | None,
    artifact_attack_method: str,
    artifact_attack_model_name: str | None,
    rewrite_strategy: str,
    artifact_agent_model_name: str,
    artifact_out_dir: str,
    fail_fast: bool = False,
) -> PairResult:
    try:
        os.environ["AGENTDOJO_REWRITE_ARTIFACT_MODEL_NAME"] = artifact_agent_model_name
        if artifact_attack_model_name:
            os.environ["AGENTDOJO_REWRITE_ARTIFACT_ATTACK_MODEL_NAME"] = artifact_attack_model_name
        else:
            os.environ.pop("AGENTDOJO_REWRITE_ARTIFACT_ATTACK_MODEL_NAME", None)
        os.environ["AGENTDOJO_REWRITE_ARTIFACT_STRATEGY"] = resolve_rewrite_strategy_dir(rewrite_strategy)
        os.environ["AGENTDOJO_REWRITE_ARTIFACT_ATTACK_METHOD"] = artifact_attack_method
        os.environ["AGENTDOJO_REWRITE_ARTIFACT_OUT_DIR"] = artifact_out_dir
        os.environ["AGENTDOJO_REWRITE_STRATEGY"] = resolve_rewrite_strategy_dir(rewrite_strategy)
        os.environ["AGENTDOJO_BENCHMARK_VERSION"] = benchmark_version

        suite = get_suite(benchmark_version, suite_name)
        applied_log_root.mkdir(parents=True, exist_ok=True)
        results = benchmark_suite(
            suite=suite,
            model=ModelsEnum(eval_agent_model_name),
            logdir=applied_log_root,
            force_rerun=True,
            benchmark_version=benchmark_version,
            user_tasks=(user_task_id,),
            injection_tasks=(injection_task_id,),
            model_id=None,
            attack=attack_method,
            defense=defense,
            tool_delimiter="tool",
            system_message_name=None,
            system_message=None,
            tool_output_format=None,
            live=None,
        )

        security_passed = results["security_results"].get((user_task_id, injection_task_id), False)
        utility_passed = results["utility_results"].get((user_task_id, injection_task_id), False)

        messages = []
        logged_error: str | None = None
        try:
            log_pipeline_name = f"{eval_agent_model_name}-{defense}" if defense else eval_agent_model_name
            task_result = load_task_results(
                pipeline_name=log_pipeline_name,
                suite_name=suite_name,
                user_task=user_task_id,
                attack_name=attack_method,
                injection_task=injection_task_id,
                logdir=applied_log_root,
            )
            messages = task_result.messages
            logged_error = task_result.error
        except FileNotFoundError:
            messages = []

        if logged_error:
            pair_info = PairInfo(user_task_id=user_task_id, injection_task_id=injection_task_id)
            failure = make_failure_record(
                pair_info,
                suite_name=suite_name,
                benchmark_version=benchmark_version,
                round_num=0,
                category=FAILURE_INFRASTRUCTURE,
                stage="benchmark_execution",
                error=logged_error,
                retryable=True,
                error_type="LoggedBenchmarkError",
                artifact_path="ij_creation_newest.json",
                run_path=applied_log_root,
            )
            return PairResult(
                user_task_id=user_task_id,
                injection_task_id=injection_task_id,
                round_num=0,
                security_passed=False,
                utility_passed=False,
                snippet_name="ij_creation_newest.json",
                messages=messages,
                error=logged_error,
                failure=failure,
            )

        return PairResult(
            user_task_id=user_task_id,
            injection_task_id=injection_task_id,
            round_num=0,
            security_passed=security_passed,
            utility_passed=utility_passed,
            snippet_name="ij_creation_newest.json",
            messages=messages,
        )
    except Exception as e:
        error_text = str(e)
        category, stage, retryable = _classify_benchmark_exception(e)
        print(f"APPLY ERROR ut={user_task_id} it={injection_task_id}: {error_text}")
        traceback.print_exc()
        if fail_fast:
            raise
        pair_info = PairInfo(user_task_id=user_task_id, injection_task_id=injection_task_id)
        failure = make_failure_record(
            pair_info,
            suite_name=suite_name,
            benchmark_version=benchmark_version,
            round_num=0,
            category=category,
            stage=stage,
            error=e,
            retryable=retryable,
            artifact_path="ij_creation_newest.json",
            run_path=applied_log_root,
        )
        return PairResult(
            user_task_id=user_task_id,
            injection_task_id=injection_task_id,
            round_num=0,
            security_passed=False,
            utility_passed=False,
            snippet_name="ij_creation_newest.json",
            messages=[],
            error=error_text,
            failure=failure,
        )


def _result_from_saved_record(pair_info: PairInfo, record: dict) -> PairResult:
    failure_data = record.get("failure")
    failure = FailureRecord(**failure_data) if isinstance(failure_data, dict) else None
    return PairResult(
        user_task_id=pair_info.user_task_id,
        injection_task_id=pair_info.injection_task_id,
        round_num=record.get("round", 0),
        security_passed=record.get("security_passed", False),
        utility_passed=record.get("utility_passed", False),
        snippet_name=record.get("snippet_name", "ij_creation_newest.json"),
        messages=[],
        error=record.get("error"),
        failure=failure,
    )


def _build_pairs(
    args,
    *,
    repo_root: Path,
    artifact_model_name: str,
    eval_model_name: str,
) -> tuple[list[PairInfo], list[str], list[PairResult]]:
    artifact_attack_method = resolve_attack_method_dir(args.artifact_attack_method, args.artifact_defense)
    eval_attack_method = resolve_attack_method_dir(args.attack_method, args.defense)
    artifact_attack_model_name = args.artifact_attack_model_name
    pairs = []
    missing = []
    existing_results = []
    for user_task_id in args.user_task_id:
        for injection_task_id in args.injection_task_id:
            output_dir = _build_applied_case_dir(
                repo_root=repo_root,
                output_subdir=args.applied_out_dir,
                rewrite_strategy=args.rewrite_strategy,
                artifact_attack_method=artifact_attack_method,
                eval_attack_method=eval_attack_method,
                artifact_attack_model_name=artifact_attack_model_name,
                artifact_agent_model_name=artifact_model_name,
                eval_agent_model_name=eval_model_name,
                suite_name=args.suite,
                benchmark_version=args.benchmark_version,
                user_task_id=user_task_id,
                injection_task_id=injection_task_id,
            )
            output_dir.mkdir(parents=True, exist_ok=True)
            previous_result = load_latest_pair_result(output_dir) if not args.force_run else None
            if previous_result is not None:
                print(f"Skipping existing apply result for {user_task_id}|{injection_task_id}")
                pair_info = PairInfo(user_task_id, injection_task_id, output_dir, previous_result)
                existing_results.append(_result_from_saved_record(pair_info, previous_result))
                continue
            if not _artifact_exists(
                repo_root=repo_root,
                output_subdir=args.artifact_out_dir,
                artifact_attack_method=artifact_attack_method,
                artifact_attack_model_name=artifact_attack_model_name,
                rewrite_strategy=args.rewrite_strategy,
                artifact_agent_model_name=artifact_model_name,
                suite_name=args.suite,
                benchmark_version=args.benchmark_version,
                user_task_id=user_task_id,
                injection_task_id=injection_task_id,
            ):
                missing.append(f"{user_task_id}|{injection_task_id}")
                continue
            pairs.append(PairInfo(user_task_id=user_task_id, injection_task_id=injection_task_id, output_dir=output_dir))
    return pairs, missing, existing_results


def run_apply(args) -> list[PairResult]:
    repo_root = _find_repo_root()
    artifact_model_name = get_resolved_agent_model_name(args.artifact_agent_model_name or args.agent_model_name)
    artifact_attack_model_name = args.artifact_attack_model_name
    eval_model_name = get_resolved_agent_model_name(args.agent_model_name)
    artifact_attack_method = resolve_attack_method_dir(args.artifact_attack_method, args.artifact_defense)
    eval_attack_method = resolve_attack_method_dir(args.attack_method, args.defense)
    applied_log_root = _build_applied_log_root(
        logdir=args.agent_logdir,
        rewrite_strategy=args.rewrite_strategy,
        artifact_attack_method=artifact_attack_method,
        eval_attack_method=eval_attack_method,
        artifact_attack_model_name=artifact_attack_model_name,
        artifact_agent_model_name=artifact_model_name,
        eval_agent_model_name=eval_model_name,
        suite_name=args.suite,
        benchmark_version=args.benchmark_version,
    )

    _set_artifact_env(
        args,
        artifact_attack_method=artifact_attack_method,
        artifact_attack_model_name=artifact_attack_model_name,
        artifact_agent_model_name=artifact_model_name,
    )
    pairs, missing, existing_results = _build_pairs(
        args,
        repo_root=repo_root,
        artifact_model_name=artifact_model_name,
        eval_model_name=eval_model_name,
    )
    results: list[PairResult] = existing_results
    if missing:
        print(f"Missing {len(missing)} artifact files:")
        for item in missing:
            print(f"  - {item}")
            user_task_id, injection_task_id = item.split("|", maxsplit=1)
            output_dir = _build_applied_case_dir(
                repo_root=repo_root,
                output_subdir=args.applied_out_dir,
                rewrite_strategy=args.rewrite_strategy,
                artifact_attack_method=artifact_attack_method,
                eval_attack_method=eval_attack_method,
                artifact_attack_model_name=artifact_attack_model_name,
                artifact_agent_model_name=artifact_model_name,
                eval_agent_model_name=eval_model_name,
                suite_name=args.suite,
                benchmark_version=args.benchmark_version,
                user_task_id=user_task_id,
                injection_task_id=injection_task_id,
            )
            output_dir.mkdir(parents=True, exist_ok=True)
            pair_info = PairInfo(user_task_id, injection_task_id, output_dir)
            failure = make_failure_record(
                pair_info,
                suite_name=args.suite,
                benchmark_version=args.benchmark_version,
                round_num=0,
                category=FAILURE_GENERATION,
                stage="artifact_lookup",
                error="Generated rewrite artifact is missing.",
                retryable=False,
                error_type="MissingArtifact",
                artifact_path=args.artifact_out_dir,
            )
            result = failure_result(pair_info, failure, snippet_name="ij_creation_newest.json")
            save_pair_result(result, output_dir)
            results.append(result)
        if args.fail_fast:
            raise FileNotFoundError("Missing rewrite artifacts")

    print(f"Applying {len(pairs)} existing rewrite artifacts")

    def handle_result(pair_info: PairInfo, result: PairResult) -> None:
        results.append(result)
        try:
            with open(pair_info.output_dir / "newest_messages.json", "w") as f:
                json.dump(_json_safe(result.messages), f, ensure_ascii=False, indent=4)
            save_pair_result(result, pair_info.output_dir)
        except Exception as e:
            failure = make_failure_record(
                pair_info,
                suite_name=args.suite,
                benchmark_version=args.benchmark_version,
                round_num=0,
                category=FAILURE_INFRASTRUCTURE,
                stage="result_persistence",
                error=e,
                retryable=True,
                artifact_path=pair_info.output_dir,
            )
            result.security_passed = False
            result.utility_passed = False
            result.error = str(e)
            result.failure = failure
            print(f"RESULT PERSISTENCE ERROR for {pair_info.user_task_id}|{pair_info.injection_task_id}: {e}")
            if args.fail_fast:
                raise
        print(
            f"{pair_info.user_task_id}/{pair_info.injection_task_id}: "
            f"utility={result.utility_passed} security={result.security_passed}"
        )

    if args.run_parallel:
        with ProcessPoolExecutor(max_workers=args.max_workers) as executor:
            futures = {
                executor.submit(
                    apply_worker,
                    args.suite,
                    pair_info.user_task_id,
                    pair_info.injection_task_id,
                    args.agent_model_name,
                    applied_log_root,
                    args.benchmark_version,
                    args.attack_method,
                    args.defense,
                    artifact_attack_method,
                    artifact_attack_model_name,
                    args.rewrite_strategy,
                    artifact_model_name,
                    str(args.artifact_out_dir),
                    args.fail_fast,
                ): pair_info
                for pair_info in pairs
            }
            for future in as_completed(futures):
                pair_info = futures[future]
                handle_result(pair_info, future.result())
    else:
        for pair_info in pairs:
            result = apply_worker(
                args.suite,
                pair_info.user_task_id,
                pair_info.injection_task_id,
                args.agent_model_name,
                applied_log_root,
                args.benchmark_version,
                args.attack_method,
                args.defense,
                artifact_attack_method,
                artifact_attack_model_name,
                args.rewrite_strategy,
                artifact_model_name,
                str(args.artifact_out_dir),
                args.fail_fast,
            )
            handle_result(pair_info, result)

    return results


def print_summary(results: list[PairResult]) -> str:
    quarantined_categories = {
        "evaluator_error",
        "invalid_benchmark_case",
        "infrastructure_error",
    }
    valid_results = [
        result for result in results
        if result.failure is None or result.failure.category not in quarantined_categories
    ]
    planned = len(results)
    total = len(valid_results)
    security_passed = sum(result.security_passed for result in valid_results)
    utility_passed = sum(result.utility_passed for result in valid_results)
    asr = security_passed / total * 100 if total else 0.0
    utility = utility_passed / total * 100 if total else 0.0
    lines = [
        "=" * 60,
        "APPLY SUMMARY",
        "=" * 60,
        f"Total pairs:         {total}",
        f"Planned pairs:       {planned}",
        "",
        f"Successful pairs ({security_passed}):",
    ]
    lines.extend(
        f"  {result.user_task_id}/{result.injection_task_id}"
        for result in valid_results if result.security_passed
    )
    failed_results = [result for result in valid_results if not result.security_passed]
    lines.extend(["", f"Failed pairs ({len(failed_results)}):"])
    lines.extend(f"  {result.user_task_id}/{result.injection_task_id}" for result in failed_results)

    category_labels = (
        ("generation_failure", "Generation failures"),
        ("evaluator_error", "Evaluator errors"),
        ("invalid_benchmark_case", "Invalid benchmark cases"),
        ("infrastructure_error", "Infrastructure errors"),
    )
    category_counts = {}
    for category, label in category_labels:
        category_results = [
            result for result in results
            if result.failure is not None and result.failure.category == category
        ]
        category_counts[category] = len(category_results)
        lines.extend(["", f"{label} ({len(category_results)}):"])
        lines.extend(
            f"  {result.user_task_id}/{result.injection_task_id}: "
            f"stage={result.failure.stage} {result.failure.error_type}: {result.failure.error}"
            for result in category_results
        )

    lines.extend([
        "",
        "=" * 60,
        f"ASR: {security_passed}/{total} ({asr:.1f}%)",
        f"U@A: {utility_passed}/{total} ({utility:.1f}%)",
        f"Generation failures: {category_counts['generation_failure']}",
        f"Evaluator errors: {category_counts['evaluator_error']}",
        f"Invalid benchmark cases: {category_counts['invalid_benchmark_case']}",
        f"Infrastructure errors: {category_counts['infrastructure_error']}",
        f"Planned / valid evaluated: {planned} / {total}",
    ])
    summary = "\n".join(lines)
    print(summary)
    return summary


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Apply existing generated trajectory rewrites to a victim model.")
    parser.add_argument("--suite", default="slack")
    parser.add_argument("--benchmark-version", default="v0")
    parser.add_argument("--user-task-id", nargs="+", default=["user_task_0"])
    parser.add_argument("--injection-task-id", nargs="+", default=["injection_task_0"])
    parser.add_argument("--agent-model-name", default="deepseek/deepseek-v4-flash", help="Victim/evaluation model.")
    parser.add_argument("--artifact-agent-model-name", default=None, help="Model-name folder that owns existing rewrite artifacts. Defaults to agent model.")
    parser.add_argument("--artifact-attack-model-name", default=None, help="Attack/rewrite model-name folder that owns existing rewrite artifacts. Omit to allow legacy artifact paths.")
    parser.add_argument("--rewrite-strategy", default="auto", choices=ALLOWED_STRATEGY_MODES)
    parser.add_argument("--attack-method", default="generated_trajectory", help="Registered AgentDojo attack to apply.")
    parser.add_argument("--defense", default=None, choices=[None, "tool_filter", "transformers_pi_detector", "spotlighting_with_delimiting", "repeat_user_prompt", "promptarmor", "melon"])
    parser.add_argument("--artifact-attack-method", default="generated_trajectory", help="Artifact attack-method folder.")
    parser.add_argument("--artifact-defense", default=None, choices=[None, "tool_filter", "transformers_pi_detector", "spotlighting_with_delimiting", "repeat_user_prompt", "promptarmor", "melon"])
    parser.add_argument("--artifact-out-dir", default=Path("res"), help="Directory containing generated rewrite artifacts.")
    parser.add_argument("--applied-out-dir", default=Path("res"), help="Directory for apply-stage per-case result summaries.")
    parser.add_argument("--agent-logdir", default=Path("runs"), help="Directory for AgentDojo run logs.")
    parser.add_argument("--results-output", default=None)
    parser.add_argument("--max-workers", default=4, type=int)
    parser.add_argument("--run-parallel", action="store_true", default=False)
    parser.add_argument("--fail-fast", action="store_true", default=False)
    parser.add_argument("--force-run", action="store_true", default=False)
    return parser


def main() -> None:
    parser = _build_arg_parser()
    args = parser.parse_args()
    for key, value in vars(args).items():
        print(f"=={key}: {value}==")

    results = run_apply(args)
    summary = print_summary(results)

    repo_root = _find_repo_root()
    artifact_model_name = get_resolved_agent_model_name(args.artifact_agent_model_name or args.agent_model_name)
    artifact_attack_model_name = args.artifact_attack_model_name
    eval_model_name = get_resolved_agent_model_name(args.agent_model_name)
    artifact_attack_method = resolve_attack_method_dir(args.artifact_attack_method, args.artifact_defense)
    eval_attack_method = resolve_attack_method_dir(args.attack_method, args.defense)
    output_path = Path(args.results_output) if args.results_output else (
        _build_applied_suite_dir(
            repo_root=repo_root,
            output_subdir=args.applied_out_dir,
            rewrite_strategy=args.rewrite_strategy,
            artifact_attack_method=artifact_attack_method,
            eval_attack_method=eval_attack_method,
            artifact_attack_model_name=artifact_attack_model_name,
            artifact_agent_model_name=artifact_model_name,
            eval_agent_model_name=eval_model_name,
            suite_name=args.suite,
            benchmark_version=args.benchmark_version,
        )
        / "pipeline_results.jsonl"
    )
    save_results_jsonl(results, output_path)
    unresolved_failures = [result.failure for result in results if result.failure is not None]
    save_failure_records_jsonl(unresolved_failures, output_path.parent / "failed_cases.jsonl")
    summary_path = output_path.parent / "summary_results.txt"
    summary_path.write_text(summary + "\n")
    print(f"Summary saved to {summary_path}")


if __name__ == "__main__":
    print(f"filepath={Path(__file__).resolve()}")
    main()
