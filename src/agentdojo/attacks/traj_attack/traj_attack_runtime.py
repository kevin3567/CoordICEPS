import asyncio
import enum
import json
import shutil
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from openai import OpenAI
from pydantic import BaseModel

from agentdojo.agent_pipeline.agent_pipeline import _get_local_model_id
from agentdojo.attacks.traj_attack.traj_attack_common import (
    TrajectoryLoader,
    get_create_injection_schema,
    get_select_site_schema,
    sanitize_injection_text,
)
from agentdojo.attacks.traj_attack.traj_attack_paths import (
    build_traj_case_dir,
    build_traj_run_root,
    resolve_attack_method_dir,
    resolve_rewrite_strategy_dir,
)
from agentdojo.attacks.traj_attack.traj_attack_initial import (
    form_create_injection_prompt,
    form_select_site_prompt,
)
from agentdojo.benchmark import load_task_results
from agentdojo.models import ModelsEnum
from agentdojo.scripts.benchmark import benchmark_suite
from agentdojo.task_suite.load_suites import get_suite


# === Types ===


@dataclass
class PairInfo:
    """Info for a (user_task_id, injection_task_id) pair."""

    user_task_id: str
    injection_task_id: str
    output_dir: Path = None
    previous_result: dict | None = None


@dataclass
class PairResult:
    """Result for a single pair after benchmarking."""

    user_task_id: str
    injection_task_id: str
    round_num: int
    security_passed: bool
    utility_passed: bool
    snippet_name: str
    messages: dict = None
    error: str = None
    failure: "FailureRecord | None" = None


@dataclass
class FailureRecord:
    """Structured non-behavioral failure for one benchmark pair and stage."""

    suite_name: str
    benchmark_version: str
    user_task_id: str
    injection_task_id: str
    round_num: int
    category: str
    stage: str
    error_type: str
    error: str
    retryable: bool
    artifact_path: str | None = None
    run_path: str | None = None


@dataclass
class StageError:
    """Error returned by a batched generation call without losing pair identity."""

    error_type: str
    error: str


FAILURE_GENERATION = "generation_failure"
FAILURE_EVALUATOR = "evaluator_error"
FAILURE_INVALID_CASE = "invalid_benchmark_case"
FAILURE_INFRASTRUCTURE = "infrastructure_error"


def make_failure_record(
    pair_info: PairInfo,
    *,
    suite_name: str,
    benchmark_version: str,
    round_num: int,
    category: str,
    stage: str,
    error: BaseException | str,
    retryable: bool,
    error_type: str | None = None,
    artifact_path: str | Path | None = None,
    run_path: str | Path | None = None,
) -> FailureRecord:
    resolved_error_type = error_type or (
        type(error).__name__ if isinstance(error, BaseException) else "PipelineError"
    )
    return FailureRecord(
        suite_name=suite_name,
        benchmark_version=benchmark_version,
        user_task_id=pair_info.user_task_id,
        injection_task_id=pair_info.injection_task_id,
        round_num=round_num,
        category=category,
        stage=stage,
        error_type=resolved_error_type,
        error=str(error),
        retryable=retryable,
        artifact_path=str(artifact_path) if artifact_path is not None else None,
        run_path=str(run_path) if run_path is not None else None,
    )


def failure_result(pair_info: PairInfo, failure: FailureRecord, *, snippet_name: str = "") -> PairResult:
    """Represent a failed pipeline stage without silently dropping the pair."""
    return PairResult(
        user_task_id=pair_info.user_task_id,
        injection_task_id=pair_info.injection_task_id,
        round_num=failure.round_num,
        security_passed=False,
        utility_passed=False,
        snippet_name=snippet_name,
        messages=[],
        error=failure.error,
        failure=failure,
    )


# === Helpers ===


def _json_safe(obj):
    """Recursively convert objects into JSON-serializable equivalents."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, tuple):
                key = "|".join(map(str, k))
            elif isinstance(k, (str, int, float, bool)) or k is None:
                key = k
            else:
                key = str(k)
            out[key] = _json_safe(v)
        return out
    if isinstance(obj, (list, tuple)):
        return [_json_safe(x) for x in obj]
    if isinstance(obj, set):
        return [_json_safe(x) for x in sorted(obj, key=lambda x: str(x))]
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, enum.Enum):
        return obj.value
    try:
        json.dumps(obj)
        return obj
    except TypeError:
        return str(obj)


def get_all_user_trajectories(
    pairs: list[PairInfo],
    *,
    suite,
    benchmark_version: str = None,
) -> tuple[list[tuple[PairInfo, list[dict], list[dict]]], dict[str, StageError]]:
    """Load observed user trajectories and reference target trajectories for each pair."""
    trajectory_loader = TrajectoryLoader(suite_name=suite.name, benchmark_version=benchmark_version)

    pairs_with_trajectories = []
    errors: dict[str, StageError] = {}
    for pair_info in pairs:
        custom_id = f"{pair_info.user_task_id}|{pair_info.injection_task_id}"
        try:
            user_trajectory = trajectory_loader.get_user_trajectory(user_task_id=pair_info.user_task_id)
            injection_trajectory = trajectory_loader.get_injection_trajectory(injection_task_id=pair_info.injection_task_id)
            pairs_with_trajectories.append((pair_info, user_trajectory, injection_trajectory))
        except Exception as e:
            print(f"TRAJECTORY ERROR ut={pair_info.user_task_id} it={pair_info.injection_task_id}: {e}")
            errors[custom_id] = StageError(type(e).__name__, str(e))

    return pairs_with_trajectories, errors


def build_all_site_selection_prompts_and_schemas(
    pairs: list[tuple[PairInfo, list[dict], list[dict]]],
    *,
    suite,
    benchmark_version: str = None,
    strategy_mode: str = "auto",
) -> tuple[list[tuple[PairInfo, str, type[BaseModel]]], dict[str, StageError]]:
    """Build per-pair site-selection prompts and schemas using preloaded benign trajectories."""
    pairs_with_site_sel_prompts = []
    errors: dict[str, StageError] = {}
    for pair_info, user_trajectory, injection_trajectory in pairs:
        custom_id = f"{pair_info.user_task_id}|{pair_info.injection_task_id}"
        try:
            observed_sites = list(
                dict.fromkeys(site for step in user_trajectory for site in step["tool_output"]["inj_cands"])
            )
            prompt = form_select_site_prompt(
                user_trajectory=user_trajectory,
                injection_trajectory=injection_trajectory,
                user_task_id=pair_info.user_task_id,
                injection_task_id=pair_info.injection_task_id,
                suite_name=suite.name,
                benchmark_version=benchmark_version,
                strategy_mode=strategy_mode,
            )
            schema = get_select_site_schema(
                suite.name,
                observed_sites=observed_sites,
                strategy_mode=strategy_mode,
            )
            pairs_with_site_sel_prompts.append((pair_info, prompt, schema))
        except Exception as e:
            print(f"SITE-SELECTION PROMPT ERROR ut={pair_info.user_task_id} it={pair_info.injection_task_id}: {e}")
            errors[custom_id] = StageError(type(e).__name__, str(e))

    return pairs_with_site_sel_prompts, errors


def build_all_site_rewrite_prompts_and_schemas(
    pairs: list[tuple[PairInfo, list[dict], list[dict], BaseModel]],
    *,
    suite,
    benchmark_version: str = None,
    fail_fast: bool = False,
    strategy_mode: str = "auto",
) -> tuple[list[tuple[PairInfo, str, type[BaseModel]]], dict[str, StageError]]:
    """Build per-pair rewrite prompts and schemas from site-selection outputs."""
    pairs_with_site_rewrite_prompts = []
    errors: dict[str, StageError] = {}
    for pair_info, user_trajectory, injection_trajectory, site_selection_info in pairs:
        try:
            selected_sites = [
                site_plan.site.value if hasattr(site_plan.site, "value") else str(site_plan.site)
                for site_plan in site_selection_info.site_plans
            ]
            if not selected_sites:
                raise ValueError("No selected sites returned from site selection.")

            prompt = form_create_injection_prompt(
                site_selection_info=site_selection_info,
                user_trajectory=user_trajectory,
                injection_trajectory=injection_trajectory,
                user_task_id=pair_info.user_task_id,
                injection_task_id=pair_info.injection_task_id,
                suite_name=suite.name,
                benchmark_version=benchmark_version,
                strategy_mode=strategy_mode,
            )
            schema = get_create_injection_schema(selected_sites=selected_sites, suite_name=suite.name)
            pairs_with_site_rewrite_prompts.append((pair_info, prompt, schema))
        except Exception as e:
            print(
                f"GENERATION ERROR rr=0 ut={pair_info.user_task_id} "
                f"it={pair_info.injection_task_id}: {e}"
            )
            errors[f"{pair_info.user_task_id}|{pair_info.injection_task_id}"] = StageError(
                type(e).__name__, str(e)
            )
            if fail_fast:
                raise

    return pairs_with_site_rewrite_prompts, errors


def initialize_pairs(args, *, repo_root: Path, resolved_model_name: str) -> tuple[list[PairInfo], list[PairInfo]]:
    """Create pair records and apply skip logic based on prior success."""
    all_pairs: list[PairInfo] = []
    skipped_pairs: list[PairInfo] = []
    rewrite_strategy_dir = resolve_rewrite_strategy_dir(args.rewrite_strategy)
    attack_method_dir = resolve_attack_method_dir(args.attack_method, args.defense)
    for user_task_id in args.user_task_id:
        for injection_task_id in args.injection_task_id:
            output_dir = build_traj_case_dir(
                repo_root=repo_root,
                output_subdir=args.out_dir,
                attack_method=attack_method_dir,
                rewrite_strategy=rewrite_strategy_dir,
                attack_model_name=args.attack_model_name,
                agent_model_name=resolved_model_name,
                suite_name=args.suite,
                benchmark_version=args.benchmark_version,
                user_task_id=user_task_id,
                injection_task_id=injection_task_id,
            )
            output_dir.mkdir(parents=True, exist_ok=True)
            pair_info = PairInfo(
                user_task_id=user_task_id,
                injection_task_id=injection_task_id,
                output_dir=output_dir,
            )
            if not args.force_run:
                latest = load_latest_pair_result(output_dir)
                if latest and latest.get("security_passed", False):
                    pair_info.previous_result = latest
                    skipped_pairs.append(pair_info)
                    continue

            all_pairs.append(pair_info)
            print(f"To run (not skipped) === case-({user_task_id}, {injection_task_id}), with {output_dir=}")

    return all_pairs, skipped_pairs


def collect_round_failures(round_results: list[PairResult], active_pairs: list[PairInfo]) -> list[PairInfo]:
    """Return behavioral and generation failures eligible for another rewrite."""
    failed_results = [
        result
        for result in round_results
        if not result.security_passed
        and (result.failure is None or result.failure.category == FAILURE_GENERATION)
    ]
    failed_result_map = {
        f"{result.user_task_id}|{result.injection_task_id}": result
        for result in failed_results
    }
    return [
        pair_info
        for pair_info in active_pairs
        if f"{pair_info.user_task_id}|{pair_info.injection_task_id}" in failed_result_map
    ]


def get_resolved_agent_model_name(agent_model_name: str) -> str:
    """Resolve local/vLLM agent names for path layout."""
    if "vllm_parsed" in agent_model_name or agent_model_name == "local":
        return _get_local_model_id(8000).replace("/", "_")
    return agent_model_name


def _classify_benchmark_exception(error: BaseException) -> tuple[str, str, bool]:
    """Classify a benchmark exception using the stage visible in its traceback."""
    frames = traceback.extract_tb(error.__traceback__)
    frame_names = {frame.name for frame in frames}
    frame_paths = {frame.filename for frame in frames}

    if any("generated_trajectory_attacks.py" in path for path in frame_paths):
        return FAILURE_GENERATION, "artifact_application", True

    evaluator_frames = {
        "_check_user_task_utility",
        "_check_injection_task_security",
        "_check_task_result",
        "utility",
        "utility_from_traces",
        "security",
        "security_from_traces",
    }
    if frame_names & evaluator_frames:
        return FAILURE_EVALUATOR, "evaluation", False

    invalid_case_frames = {
        "get_user_task_by_id",
        "get_injection_task_by_id",
        "load_and_inject_default_environment",
        "init_environment",
        "ground_truth",
    }
    if frame_names & invalid_case_frames:
        return FAILURE_INVALID_CASE, "benchmark_setup", False

    error_module = type(error).__module__
    if error_module.startswith(("openai", "httpx", "httpcore")) or isinstance(error, OSError):
        return FAILURE_INFRASTRUCTURE, "benchmark_execution", True

    return FAILURE_INFRASTRUCTURE, "benchmark_execution", True


# === Generation ===


def call_api_single_sync(
    client: OpenAI,
    model_name: str,
    pair_info: PairInfo,
    prompt: str,
    schema: type[BaseModel],
) -> tuple[PairInfo, Any | StageError]:
    """Synchronous version of API call."""
    try:
        response = client.responses.parse(
            model=model_name,
            input=[{"role": "user", "content": prompt}],
            text_format=schema,
        )
        parsed_output = response.output_parsed
        return pair_info, parsed_output
    except Exception as e:
        print(f"Error for {pair_info.user_task_id}/{pair_info.injection_task_id}: {e}")
        return pair_info, StageError(error_type=type(e).__name__, error=str(e))


async def concurrent_generate(
    pairs_with_prompt_and_schema: list[tuple],
    client: OpenAI,
    model_name: str,
    max_concurrent: int = 10,
) -> dict[str, Any]:
    """Generate parsed outputs concurrently using either per-pair or shared schemas."""
    semaphore = asyncio.Semaphore(max_concurrent)

    async def bounded_call(pair_info: PairInfo, prompt: str, schema: type[BaseModel]):
        async with semaphore:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None,
                lambda: call_api_single_sync(client, model_name, pair_info, prompt, schema),
            )

    tasks = [bounded_call(pair_info, prompt, schema) for pair_info, prompt, schema in pairs_with_prompt_and_schema]
    results_list = await asyncio.gather(*tasks, return_exceptions=True)

    results = {}
    for i, result in enumerate(results_list):
        pair_info, _, _ = pairs_with_prompt_and_schema[i]
        custom_id = f"{pair_info.user_task_id}|{pair_info.injection_task_id}"

        if isinstance(result, Exception):
            print(f"Error for {custom_id}: {result}")
            results[custom_id] = None
        else:
            _, parsed_output = result
            results[custom_id] = parsed_output

    return results


def batch_generate(
    pairs_with_prompt_and_schema: list[tuple],
    client: OpenAI,
    model_name: str,
    max_concurrent: int = 4,
) -> tuple[dict[str, Any], float]:
    """Generate parsed outputs for all pairs via concurrent API calls."""
    start_time = time.time()
    print(
        f"Generating {len(pairs_with_prompt_and_schema)} items with "
        f"model={model_name} max_concurrent={max_concurrent}",
        flush=True,
    )

    results = asyncio.run(
        concurrent_generate(
            pairs_with_prompt_and_schema,
            client,
            model_name,
            max_concurrent=max_concurrent,
        )
    )

    elapsed_time = time.time() - start_time
    return results, elapsed_time


# === Benchmark ===


def benchmark_worker(
    suite_name: str,
    user_task_id: str,
    injection_task_id: str,
    agent_model_name: str,
    agent_logdir: Path,
    benchmark_version: str,
    attack_method: str,
    rewrite_strategy: str,
    attack_model_name: str,
    defense: str | None,
    round_num: int,
    snippet_name: str,
    fail_fast: bool = False,
) -> PairResult:
    """Run one benchmark case and return a structured result."""
    resolved_agent_logdir: Path | None = None
    try:
        suite = get_suite(benchmark_version, suite_name)
        rewrite_strategy_dir = resolve_rewrite_strategy_dir(rewrite_strategy)
        attack_method_dir = resolve_attack_method_dir(attack_method, defense)
        resolved_agent_logdir = build_traj_run_root(
            logdir=agent_logdir,
            attack_method=attack_method_dir,
            rewrite_strategy=rewrite_strategy_dir,
            attack_model_name=attack_model_name,
            agent_model_name=agent_model_name,
            suite_name=suite_name,
            benchmark_version=benchmark_version,
        )
        resolved_agent_logdir.mkdir(parents=True, exist_ok=True)
        import os
        os.environ["AGENTDOJO_REWRITE_STRATEGY"] = rewrite_strategy_dir
        os.environ["AGENTDOJO_REWRITE_ARTIFACT_ATTACK_MODEL_NAME"] = attack_model_name
        os.environ["AGENTDOJO_REWRITE_ATTACK_MODEL_NAME"] = attack_model_name
        os.environ["AGENTDOJO_BENCHMARK_VERSION"] = benchmark_version
        os.environ["AGENTDOJO_TRAJ_ATTACK_METHOD"] = attack_method_dir
        results = benchmark_suite(
            suite=suite,
            model=ModelsEnum(agent_model_name),
            logdir=resolved_agent_logdir,
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
            log_pipeline_name = f"{agent_model_name}-{defense}" if defense else agent_model_name
            task_result = load_task_results(
                pipeline_name=log_pipeline_name,
                suite_name=suite_name,
                user_task=user_task_id,
                attack_name=attack_method,
                injection_task=injection_task_id,
                logdir=resolved_agent_logdir,
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
                round_num=round_num,
                category=FAILURE_INFRASTRUCTURE,
                stage="benchmark_execution",
                error=logged_error,
                retryable=True,
                error_type="LoggedBenchmarkError",
                artifact_path=snippet_name,
                run_path=resolved_agent_logdir,
            )
            return PairResult(
                user_task_id=user_task_id,
                injection_task_id=injection_task_id,
                round_num=round_num,
                security_passed=False,
                utility_passed=False,
                snippet_name=snippet_name,
                messages=messages,
                error=logged_error,
                failure=failure,
            )
        return PairResult(
            user_task_id=user_task_id,
            injection_task_id=injection_task_id,
            round_num=round_num,
            security_passed=security_passed,
            utility_passed=utility_passed,
            snippet_name=snippet_name,
            messages=messages,
        )
    except Exception as e:
        error_text = str(e)
        category, stage, retryable = _classify_benchmark_exception(e)
        print(f"BENCHMARK ERROR rr={round_num} ut={user_task_id} it={injection_task_id}: {error_text}")
        traceback.print_exc()
        if fail_fast:
            raise
        pair_info = PairInfo(user_task_id=user_task_id, injection_task_id=injection_task_id)
        failure = make_failure_record(
            pair_info,
            suite_name=suite_name,
            benchmark_version=benchmark_version,
            round_num=round_num,
            category=category,
            stage=stage,
            error=e,
            retryable=retryable,
            artifact_path=snippet_name,
            run_path=resolved_agent_logdir,
        )
        return PairResult(
            user_task_id=user_task_id,
            injection_task_id=injection_task_id,
            round_num=round_num,
            security_passed=False,
            utility_passed=False,
            snippet_name=snippet_name,
            messages=[],
            error=error_text,
            failure=failure,
        )


def run_all_pairs(
    pair_infos: list[PairInfo],
    args,
    round_num: int,
    pair_to_snippet_name: dict[str, str],
    parallel: bool = True,
    fail_fast: bool = False,
) -> list[PairResult]:
    round_results: list[PairResult] = []

    def handle_result(pair_info: PairInfo, result: PairResult, source: str) -> None:
        custom_id = f"{pair_info.user_task_id}|{pair_info.injection_task_id}"
        if result.failure is not None and result.failure.artifact_path:
            artifact_path = Path(result.failure.artifact_path)
            if not artifact_path.is_absolute():
                result.failure.artifact_path = str(pair_info.output_dir / artifact_path)
        round_results.append(result)
        print(f"Examining case ({custom_id}) ({source})")
        print(f"==> {result.utility_passed=}, {result.security_passed=}")
        if result.error:
            print(f"Recorded benchmark error for ut={result.user_task_id} it={result.injection_task_id}: {result.error}")
        try:
            print(f"Logging message of {pair_info.output_dir=}")
            with open(pair_info.output_dir / "newest_messages.json", "w") as f:
                json.dump(_json_safe(result.messages), f, ensure_ascii=False, indent=4)
            save_pair_result(result, pair_info.output_dir)
        except Exception as e:
            failure = make_failure_record(
                pair_info,
                suite_name=args.suite,
                benchmark_version=args.benchmark_version,
                round_num=round_num,
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
            print(f"RESULT PERSISTENCE ERROR for {custom_id}: {e}")
            if fail_fast:
                raise
        status = "PASS" if result.security_passed else "FAIL"
        print(f"  {result.user_task_id}/{result.injection_task_id}: {status}")

    if parallel:
        executor = ProcessPoolExecutor(max_workers=args.max_workers)
        pool_failed = False
        try:
            futures = {}
            for pair_info in pair_infos:
                custom_id = f"{pair_info.user_task_id}|{pair_info.injection_task_id}"
                snippet_name = pair_to_snippet_name.get(custom_id)
                if snippet_name is None:
                    print(f"Skipped ({custom_id})")
                    continue

                print(f"Launching case ({custom_id}) (via future)")
                future = executor.submit(
                    benchmark_worker,
                    args.suite,
                    pair_info.user_task_id,
                    pair_info.injection_task_id,
                    args.agent_model_name,
                    Path(args.agent_logdir),
                    args.benchmark_version,
                    args.attack_method,
                    args.rewrite_strategy,
                    args.attack_model_name,
                    args.defense,
                    round_num,
                    snippet_name,
                    fail_fast,
                )
                futures[future] = pair_info

            for future in as_completed(futures):
                pair_info = futures[future]
                try:
                    result = future.result()
                    handle_result(pair_info, result, "via as_completed")
                except Exception as e:
                    print(
                        f"FATAL benchmark handling failure: rr={round_num} "
                        f"ut={pair_info.user_task_id} it={pair_info.injection_task_id}"
                    )
                    if fail_fast:
                        pool_failed = True
                        executor.shutdown(wait=False, cancel_futures=True)
                        raise
                    failure = make_failure_record(
                        pair_info,
                        suite_name=args.suite,
                        benchmark_version=args.benchmark_version,
                        round_num=round_num,
                        category=FAILURE_INFRASTRUCTURE,
                        stage="benchmark_orchestration",
                        error=e,
                        retryable=True,
                        artifact_path=pair_info.output_dir,
                    )
                    round_results.append(
                        failure_result(
                            pair_info,
                            failure,
                            snippet_name=pair_to_snippet_name.get(
                                f"{pair_info.user_task_id}|{pair_info.injection_task_id}", ""
                            ),
                        )
                    )
        finally:
            if not pool_failed:
                executor.shutdown(wait=True, cancel_futures=False)
    else:
        for pair_info in pair_infos:
            custom_id = f"{pair_info.user_task_id}|{pair_info.injection_task_id}"
            snippet_name = pair_to_snippet_name.get(custom_id)
            if snippet_name is None:
                print(f"Skipped ({custom_id})")
                continue

            print(f"Launching case ({custom_id}) (serial)")
            try:
                result = benchmark_worker(
                    args.suite,
                    pair_info.user_task_id,
                    pair_info.injection_task_id,
                    args.agent_model_name,
                    Path(args.agent_logdir),
                    args.benchmark_version,
                    args.attack_method,
                    args.rewrite_strategy,
                    args.attack_model_name,
                    args.defense,
                    round_num,
                    snippet_name,
                    fail_fast,
                )
                handle_result(pair_info, result, "serial")
            except Exception as e:
                print(
                    f"FATAL benchmark handling failure: rr={round_num} "
                    f"ut={pair_info.user_task_id} it={pair_info.injection_task_id}"
                )
                if fail_fast:
                    raise
                failure = make_failure_record(
                    pair_info,
                    suite_name=args.suite,
                    benchmark_version=args.benchmark_version,
                    round_num=round_num,
                    category=FAILURE_INFRASTRUCTURE,
                    stage="benchmark_orchestration",
                    error=e,
                    retryable=True,
                    artifact_path=pair_info.output_dir,
                )
                round_results.append(
                    failure_result(pair_info, failure, snippet_name=snippet_name or "")
                )

    return round_results


# === IO ===


def save_results_jsonl(all_results: list[PairResult], output_path: Path) -> None:
    """Save all results to a JSONL file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "a") as f:
        for result in all_results:
            record = {
                "user_task_id": result.user_task_id,
                "injection_task_id": result.injection_task_id,
                "round": result.round_num,
                "security_passed": result.security_passed,
                "utility_passed": result.utility_passed,
                "snippet_name": result.snippet_name,
            }
            if result.error:
                record["error"] = result.error
            if result.failure:
                record["failure"] = asdict(result.failure)
            f.write(json.dumps(record) + "\n")

    print(f"Results saved to {output_path}")


def save_pair_result(result: PairResult, output_dir: Path) -> None:
    """Append a result to the per-pair results.jsonl file."""
    results_file = output_dir / "results.jsonl"
    record = {
        "snippet_name": result.snippet_name,
        "round": result.round_num,
        "security_passed": result.security_passed,
        "utility_passed": result.utility_passed,
    }
    if result.error:
        record["error"] = result.error
    if result.failure:
        record["failure"] = asdict(result.failure)

    with open(results_file, "a") as f:
        f.write(json.dumps(record) + "\n")


def save_failure_records_jsonl(failures: list[FailureRecord], output_path: Path) -> None:
    """Replace the diagnostics file with the latest unresolved pair failures."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for failure in failures:
            f.write(json.dumps(asdict(failure), ensure_ascii=False) + "\n")


def load_latest_pair_result(output_dir: Path) -> dict | None:
    """Load the latest result from per-pair results.jsonl. Returns None if no results."""
    results_file = output_dir / "results.jsonl"
    if not results_file.exists():
        return None

    latest = None
    with open(results_file, "r") as f:
        for line in f:
            if line.strip():
                latest = json.loads(line)
    return latest


def save_site_selection_stage(
    pair_infos: list[PairInfo],
    results: dict[str, Any],
    prompt_map: dict[str, str],
    round_num: int,
    *,
    fail_fast: bool = False,
) -> tuple[list[tuple[PairInfo, Any]], dict[str, StageError]]:
    """Save site-selection artifacts and return successful `(pair_info, site_selection)` pairs."""
    saved = []
    errors: dict[str, StageError] = {}
    for pair_info in pair_infos:
        custom_id = f"{pair_info.user_task_id}|{pair_info.injection_task_id}"
        try:
            site_selection_info = results.get(custom_id)
            if site_selection_info is None:
                print(f"Failed site selection for {custom_id}")
                errors[custom_id] = StageError("MissingGenerationOutput", "No site-selection output returned.")
                if fail_fast:
                    raise RuntimeError(errors[custom_id].error)
                continue
            if isinstance(site_selection_info, StageError):
                print(f"Failed site selection for {custom_id}: {site_selection_info.error}")
                errors[custom_id] = site_selection_info
                if fail_fast:
                    raise RuntimeError(site_selection_info.error)
                continue

            pair_info.output_dir.mkdir(parents=True, exist_ok=True)
            sel_prompt_v = pair_info.output_dir / f"selected_sites_prompt_v{round_num}.txt"
            sel_resp_v = pair_info.output_dir / f"selected_sites_v{round_num}.json"
            sel_prompt_v.write_text(prompt_map[custom_id])
            sel_resp_v.write_text(site_selection_info.model_dump_json(indent=2))
            shutil.copy(str(sel_prompt_v), str(pair_info.output_dir / "selected_sites_prompt_newest.txt"))
            shutil.copy(str(sel_resp_v), str(pair_info.output_dir / "selected_sites_newest.json"))
            print(f"Saved site-selection artifacts for {custom_id}")
            saved.append((pair_info, site_selection_info))
        except Exception as e:
            print(f"GENERATION ERROR rr={round_num} ut={pair_info.user_task_id} it={pair_info.injection_task_id}: {e}")
            errors[custom_id] = StageError(type(e).__name__, str(e))
            if fail_fast:
                raise
    return saved, errors


def save_rewrite_stage(
    pair_infos: list[PairInfo],
    results: dict[str, Any],
    prompt_map: dict[str, str],
    round_num: int,
    pair_to_snippet_name: dict[str, str],
    *,
    fail_fast: bool = False,
) -> tuple[list[PairInfo], dict[str, StageError]]:
    """Save rewrite artifacts, update newest pointers, and track active snippet names."""
    ready_pairs = []
    errors: dict[str, StageError] = {}
    for pair_info in pair_infos:
        custom_id = f"{pair_info.user_task_id}|{pair_info.injection_task_id}"
        try:
            rewrite_info = results.get(custom_id)
            if rewrite_info is None:
                print(f"Failed site rewrite for {custom_id}")
                errors[custom_id] = StageError("MissingGenerationOutput", "No rewrite output returned.")
                if fail_fast:
                    raise RuntimeError(errors[custom_id].error)
                continue
            if isinstance(rewrite_info, StageError):
                print(f"Failed site rewrite for {custom_id}: {rewrite_info.error}")
                errors[custom_id] = rewrite_info
                if fail_fast:
                    raise RuntimeError(rewrite_info.error)
                continue

            pair_info.output_dir.mkdir(parents=True, exist_ok=True)
            rewrite_prompt_v = pair_info.output_dir / f"ij_creation_prompt_v{round_num}.txt"
            rewrite_resp_v = pair_info.output_dir / f"ij_creation_v{round_num}.json"
            rewrite_payload = rewrite_info.model_dump()
            for entry in rewrite_payload.values():
                if isinstance(entry, dict):
                    if isinstance(entry.get("title"), str):
                        entry["title"] = sanitize_injection_text(entry["title"])
                    if isinstance(entry.get("text"), str):
                        entry["text"] = sanitize_injection_text(entry["text"])
            rewrite_prompt_v.write_text(prompt_map[custom_id])
            rewrite_resp_v.write_text(json.dumps(rewrite_payload, indent=2, ensure_ascii=False))
            shutil.copy(str(rewrite_prompt_v), str(pair_info.output_dir / "ij_creation_prompt_newest.txt"))
            shutil.copy(str(rewrite_resp_v), str(pair_info.output_dir / "ij_creation_newest.json"))
            pair_to_snippet_name[custom_id] = rewrite_resp_v.name
            print(f"Saved rewrite artifacts for {custom_id}")
            ready_pairs.append(pair_info)
        except Exception as e:
            print(f"GENERATION ERROR rr={round_num} ut={pair_info.user_task_id} it={pair_info.injection_task_id}: {e}")
            errors[custom_id] = StageError(type(e).__name__, str(e))
            if fail_fast:
                raise
    return ready_pairs, errors
