# python -m agentdojo.attacks.search_attack_pipeline2

# version2: drop the no_search branch, the input user/injection task can be a list of tasks, ask ai for batch acceleration. : round-based architecture with batch API and parallel benchmarking.
# version3: use success examples
import argparse
import os
import time
from pathlib import Path

from openai import OpenAI

from agentdojo.attacks.traj_attack.traj_attack_bootstrap import (
    form_select_site_prompt as bootstrap_form_select_site_prompt,
    form_create_injection_prompt as bootstrap_form_create_injection_prompt
)
from agentdojo.attacks.traj_attack.traj_attack_common import (
    _find_repo_root,
    find_nearest_success_examples,
    get_select_site_schema,
    get_create_injection_schema
)
from agentdojo.attacks.traj_attack.traj_attack_paths import (
    build_traj_suite_dir,
    resolve_attack_method_dir,
    resolve_rewrite_strategy_dir
)
from agentdojo.attacks.traj_attack.traj_attack_strategy import ALLOWED_STRATEGY_MODES
from agentdojo.attacks.traj_attack.traj_attack_runtime import (
    FAILURE_GENERATION,
    FAILURE_INVALID_CASE,
    PairInfo,
    PairResult,
    StageError,
    batch_generate,
    build_all_site_rewrite_prompts_and_schemas,
    build_all_site_selection_prompts_and_schemas,
    collect_round_failures,
    get_all_user_trajectories,
    get_resolved_agent_model_name,
    failure_result,
    initialize_pairs,
    make_failure_record,
    run_all_pairs,
    save_failure_records_jsonl,
    save_pair_result,
    save_results_jsonl,
    save_rewrite_stage,
    save_site_selection_stage,
)
from agentdojo.task_suite.load_suites import get_suite


def _pair_key(pair_info: PairInfo) -> str:
    return f"{pair_info.user_task_id}|{pair_info.injection_task_id}"


def _raise_if_fail_fast(errors: dict[str, StageError], *, fail_fast: bool) -> None:
    if fail_fast and errors:
        custom_id, error = next(iter(errors.items()))
        raise RuntimeError(f"{custom_id}: {error.error_type}: {error.error}")


def _complete_round_results(
    pair_infos: list[PairInfo],
    benchmark_results: list[PairResult],
    failure_details: dict[str, tuple[str, str, StageError]],
    *,
    args,
    round_num: int,
    pair_to_snippet_name: dict[str, str],
) -> list[PairResult]:
    """Ensure that every requested pair has an explicit result for this round."""
    completed = list(benchmark_results)
    completed_ids = {f"{result.user_task_id}|{result.injection_task_id}" for result in completed}
    for pair_info in pair_infos:
        custom_id = _pair_key(pair_info)
        if custom_id in completed_ids:
            continue
        category, stage, stage_error = failure_details.get(
            custom_id,
            (
                FAILURE_GENERATION,
                "generation_pipeline",
                StageError("MissingPairResult", "Pair did not reach benchmark execution."),
            ),
        )
        failure = make_failure_record(
            pair_info,
            suite_name=args.suite,
            benchmark_version=args.benchmark_version,
            round_num=round_num,
            category=category,
            stage=stage,
            error=stage_error.error,
            retryable=category == FAILURE_GENERATION,
            error_type=stage_error.error_type,
            artifact_path=(
                pair_info.output_dir / pair_to_snippet_name[custom_id]
                if custom_id in pair_to_snippet_name
                else pair_info.output_dir
            ),
        )
        result = failure_result(
            pair_info,
            failure,
            snippet_name=pair_to_snippet_name.get(custom_id, ""),
        )
        save_pair_result(result, pair_info.output_dir)
        completed.append(result)
    return completed


def round_0_exec(
    active_pairs: list[PairInfo],
    *,
    args,
    suite,
    attack_client: OpenAI,
    pair_to_snippet_name: dict[str, str],
    parallel: bool,
    fail_fast: bool,
) -> list[PairResult]:
    """Execute round 0: site selection, rewrite generation, and benchmark."""
    failure_details: dict[str, tuple[str, str, StageError]] = {}
    pairs_with_user_trajectories, trajectory_errors = get_all_user_trajectories(
        pairs=active_pairs,
        suite=suite,
        benchmark_version=args.benchmark_version,
    )
    failure_details.update(
        {key: (FAILURE_INVALID_CASE, "trajectory_setup", error) for key, error in trajectory_errors.items()}
    )
    _raise_if_fail_fast(trajectory_errors, fail_fast=fail_fast)

    site_selection_inputs, selection_prompt_errors = build_all_site_selection_prompts_and_schemas(
        pairs=pairs_with_user_trajectories,
        suite=suite,
        benchmark_version=args.benchmark_version,
        strategy_mode=args.rewrite_strategy,
    )
    failure_details.update(
        {key: (FAILURE_GENERATION, "site_selection_prompt", error) for key, error in selection_prompt_errors.items()}
    )
    _raise_if_fail_fast(selection_prompt_errors, fail_fast=fail_fast)
    site_selection_results, batch_time = batch_generate(site_selection_inputs, attack_client, args.attack_model_name, max_concurrent=args.generation_max_concurrent)
    print(f"Site selection completed in {batch_time:.1f}s")

    site_prompt_map = {
        f"{pair_info.user_task_id}|{pair_info.injection_task_id}": prompt
        for pair_info, prompt, _ in site_selection_inputs
    }
    saved_site_selections, selection_errors = save_site_selection_stage(
        [pair_info for pair_info, _, _ in site_selection_inputs],
        site_selection_results,
        site_prompt_map,
        0,
        fail_fast=fail_fast,
    )
    failure_details.update(
        {key: (FAILURE_GENERATION, "site_selection", error) for key, error in selection_errors.items()}
    )

    pairs_with_site_selections = []
    saved_site_selection_map = {
        f"{pair_info.user_task_id}|{pair_info.injection_task_id}": site_selection_info
        for pair_info, site_selection_info in saved_site_selections
    }
    for pair_info, user_trajectory, injection_trajectory in pairs_with_user_trajectories:
        custom_id = f"{pair_info.user_task_id}|{pair_info.injection_task_id}"
        site_selection = saved_site_selection_map.get(custom_id)
        if site_selection is None:
            continue

        selected_sites = getattr(site_selection, "site_plans", None)
        if not selected_sites:
            print(
                f"GENERATION ERROR rr=0 ut={pair_info.user_task_id} "
                f"it={pair_info.injection_task_id}: no selected sites returned"
            )
            if fail_fast:
                raise ValueError(f"No selected sites returned for {custom_id}")
            failure_details[custom_id] = (
                FAILURE_GENERATION,
                "site_selection",
                StageError("EmptySiteSelection", "No selected sites returned."),
            )
            continue

        pairs_with_site_selections.append((pair_info, user_trajectory, injection_trajectory, site_selection))

    site_rewrite_inputs, rewrite_prompt_errors = build_all_site_rewrite_prompts_and_schemas(
        pairs=pairs_with_site_selections,
        suite=suite,
        benchmark_version=args.benchmark_version,
        fail_fast=fail_fast,
        strategy_mode=args.rewrite_strategy,
    )
    failure_details.update(
        {key: (FAILURE_GENERATION, "rewrite_prompt", error) for key, error in rewrite_prompt_errors.items()}
    )
    site_rewrite_results, batch_time = batch_generate(site_rewrite_inputs, attack_client, args.attack_model_name, max_concurrent=args.generation_max_concurrent)
    print(f"Site rewrite completed in {batch_time:.1f}s")

    rewrite_prompt_map = {
        f"{pair_info.user_task_id}|{pair_info.injection_task_id}": prompt
        for pair_info, prompt, _ in site_rewrite_inputs
    }
    rewrite_ready_pairs, rewrite_errors = save_rewrite_stage(
        [pair_info for pair_info, _, _ in site_rewrite_inputs],
        site_rewrite_results,
        rewrite_prompt_map,
        0,
        pair_to_snippet_name,
        fail_fast=fail_fast,
    )
    failure_details.update(
        {key: (FAILURE_GENERATION, "rewrite_generation", error) for key, error in rewrite_errors.items()}
    )

    print(f">> \nBenchmarking {len(rewrite_ready_pairs)} pairs...")
    benchmark_results = run_all_pairs(
        rewrite_ready_pairs,
        args,
        round_num=0,
        pair_to_snippet_name=pair_to_snippet_name,
        parallel=parallel,
        fail_fast=fail_fast,
    )
    return _complete_round_results(
        active_pairs,
        benchmark_results,
        failure_details,
        args=args,
        round_num=0,
        pair_to_snippet_name=pair_to_snippet_name,
    )


def round_n_exec(
    failed_pair_infos: list[PairInfo],
    *,
    round_num: int,
    args,
    suite,
    attack_client: OpenAI,
    resolved_model_name: str,
    pair_to_snippet_name: dict[str, str],
    parallel: bool,
    fail_fast: bool,
) -> list[PairResult]:
    """Execute a bootstrap rewrite round for the still-failed pairs."""
    failure_details: dict[str, tuple[str, str, StageError]] = {}
    failed_pairs_with_trajectories, trajectory_errors = get_all_user_trajectories(
        pairs=failed_pair_infos,
        suite=suite,
        benchmark_version=args.benchmark_version,
    )
    failure_details.update(
        {key: (FAILURE_INVALID_CASE, "trajectory_setup", error) for key, error in trajectory_errors.items()}
    )
    _raise_if_fail_fast(trajectory_errors, fail_fast=fail_fast)

    retry_site_selection_inputs = []
    retry_pair_contexts = {}
    for pair_info, user_trajectory, injection_trajectory in failed_pairs_with_trajectories:
        custom_id = f"{pair_info.user_task_id}|{pair_info.injection_task_id}"
        try:
            nearest_success_examples = find_nearest_success_examples(
                suite_name=suite.name,
                agent_model_name=resolved_model_name,
                benchmark_version=args.benchmark_version,
                current_user_task_id=pair_info.user_task_id,
                current_injection_task_id=pair_info.injection_task_id,
                attack_method=resolve_attack_method_dir(args.attack_method, args.defense),
                attack_model_name=args.attack_model_name,
                rewrite_strategy=args.rewrite_strategy,
                output_subdir=args.out_dir,
                top_k=2,
                prioritize_by=("user_task", "injection_task"),
            )
            observed_sites = list(
                dict.fromkeys(site for step in user_trajectory for site in step["tool_output"]["inj_cands"])
            )
            prompt = bootstrap_form_select_site_prompt(
                user_trajectory=user_trajectory,
                injection_trajectory=injection_trajectory,
                user_task_id=pair_info.user_task_id,
                injection_task_id=pair_info.injection_task_id,
                suite_name=suite.name,
                benchmark_version=args.benchmark_version,
                nearest_success_examples=nearest_success_examples,
                strategy_mode=args.rewrite_strategy,
            )
            schema = get_select_site_schema(
                suite.name,
                observed_sites=observed_sites,
                strategy_mode=args.rewrite_strategy,
            )
            retry_site_selection_inputs.append((pair_info, prompt, schema))
            retry_pair_contexts[custom_id] = {
                "pair_info": pair_info,
                "user_trajectory": user_trajectory,
                "injection_trajectory": injection_trajectory,
            }
        except Exception as e:
            print(f"GENERATION ERROR rr={round_num} ut={pair_info.user_task_id} it={pair_info.injection_task_id}: {e}")
            failure_details[custom_id] = (
                FAILURE_GENERATION,
                "site_selection_prompt",
                StageError(type(e).__name__, str(e)),
            )
            if fail_fast:
                raise

    if not retry_site_selection_inputs:
        print(f"No bootstrap site-selection retries could be built for round {round_num}.")
        return _complete_round_results(
            failed_pair_infos, [], failure_details, args=args, round_num=round_num,
            pair_to_snippet_name=pair_to_snippet_name,
        )

    retry_results, batch_time = batch_generate(
        retry_site_selection_inputs,
        attack_client,
        args.attack_model_name,
        max_concurrent=args.generation_max_concurrent,
    )
    print(f"Bootstrap site selection completed in {batch_time:.1f}s")

    site_prompt_map = {
        f"{pair.user_task_id}|{pair.injection_task_id}": prompt
        for pair, prompt, _ in retry_site_selection_inputs
    }
    saved_site_selections, selection_errors = save_site_selection_stage(
        [pair_info for pair_info, _, _ in retry_site_selection_inputs],
        retry_results,
        site_prompt_map,
        round_num,
        fail_fast=fail_fast,
    )
    failure_details.update(
        {key: (FAILURE_GENERATION, "site_selection", error) for key, error in selection_errors.items()}
    )
    saved_site_selection_map = {
        f"{pair_info.user_task_id}|{pair_info.injection_task_id}": site_selection_info
        for pair_info, site_selection_info in saved_site_selections
    }

    successful_site_selection_inputs = []
    for pair_info, _, _ in retry_site_selection_inputs:
        custom_id = f"{pair_info.user_task_id}|{pair_info.injection_task_id}"
        site_selection_info = saved_site_selection_map.get(custom_id)
        if site_selection_info is None:
            continue
        ctx = retry_pair_contexts[custom_id]
        successful_site_selection_inputs.append(
            (
                pair_info,
                ctx["user_trajectory"],
                ctx["injection_trajectory"],
                site_selection_info,
            )
        )

    if not successful_site_selection_inputs:
        print(f"No bootstrap rewrite inputs could be built for round {round_num}.")
        return _complete_round_results(
            failed_pair_infos, [], failure_details, args=args, round_num=round_num,
            pair_to_snippet_name=pair_to_snippet_name,
        )

    retry_site_rewrite_inputs = []
    for pair_info, user_trajectory, injection_trajectory, site_selection_info in successful_site_selection_inputs:
        custom_id = f"{pair_info.user_task_id}|{pair_info.injection_task_id}"
        try:
            selected_sites = [
                site_plan.site.value if hasattr(site_plan.site, "value") else str(site_plan.site)
                for site_plan in site_selection_info.site_plans
            ]
            if not selected_sites:
                raise ValueError("No selected sites returned from bootstrap site selection.")

            nearest_success_examples = find_nearest_success_examples(
                suite_name=suite.name,
                agent_model_name=resolved_model_name,
                benchmark_version=args.benchmark_version,
                current_user_task_id=pair_info.user_task_id,
                current_injection_task_id=pair_info.injection_task_id,
                attack_method=resolve_attack_method_dir(args.attack_method, args.defense),
                attack_model_name=args.attack_model_name,
                rewrite_strategy=args.rewrite_strategy,
                output_subdir=args.out_dir,
                top_k=1,
                prioritize_by=("user_task", "injection_task"),
            )
            prompt = bootstrap_form_create_injection_prompt(
                site_selection_info=site_selection_info,
                user_trajectory=user_trajectory,
                injection_trajectory=injection_trajectory,
                user_task_id=pair_info.user_task_id,
                injection_task_id=pair_info.injection_task_id,
                suite_name=suite.name,
                benchmark_version=args.benchmark_version,
                nearest_success_examples=nearest_success_examples,
                strategy_mode=args.rewrite_strategy,
            )
            schema = get_create_injection_schema(selected_sites=selected_sites, suite_name=suite.name)
            retry_site_rewrite_inputs.append((pair_info, prompt, schema))
        except Exception as e:
            print(f"GENERATION ERROR rr={round_num} ut={pair_info.user_task_id} it={pair_info.injection_task_id}: {e}")
            failure_details[custom_id] = (
                FAILURE_GENERATION,
                "rewrite_prompt",
                StageError(type(e).__name__, str(e)),
            )
            if fail_fast:
                raise

    if not retry_site_rewrite_inputs:
        print(f"No bootstrap rewrite prompts could be built for round {round_num}.")
        return _complete_round_results(
            failed_pair_infos, [], failure_details, args=args, round_num=round_num,
            pair_to_snippet_name=pair_to_snippet_name,
        )

    retry_rewrite_results, rewrite_batch_time = batch_generate(
        retry_site_rewrite_inputs,
        attack_client,
        args.attack_model_name,
        max_concurrent=args.generation_max_concurrent,
    )
    print(f"Bootstrap rewrite completed in {rewrite_batch_time:.1f}s")

    rewrite_prompt_map = {
        f"{pair.user_task_id}|{pair.injection_task_id}": prompt
        for pair, prompt, _ in retry_site_rewrite_inputs
    }
    retry_ready_pairs, rewrite_errors = save_rewrite_stage(
        [pair_info for pair_info, _, _ in retry_site_rewrite_inputs],
        retry_rewrite_results,
        rewrite_prompt_map,
        round_num,
        pair_to_snippet_name,
        fail_fast=fail_fast,
    )
    failure_details.update(
        {key: (FAILURE_GENERATION, "rewrite_generation", error) for key, error in rewrite_errors.items()}
    )

    if not retry_ready_pairs:
        print(f"No pairs ready for bootstrap benchmarking in round {round_num}.")
        return _complete_round_results(
            failed_pair_infos, [], failure_details, args=args, round_num=round_num,
            pair_to_snippet_name=pair_to_snippet_name,
        )

    print(f">> \nBenchmarking {len(retry_ready_pairs)} bootstrap pairs...")
    benchmark_results = run_all_pairs(
        retry_ready_pairs,
        args,
        round_num=round_num,
        pair_to_snippet_name=pair_to_snippet_name,
        parallel=parallel,
        fail_fast=fail_fast,
    )
    return _complete_round_results(
        failed_pair_infos,
        benchmark_results,
        failure_details,
        args=args,
        round_num=round_num,
        pair_to_snippet_name=pair_to_snippet_name,
    )


def run_pipeline(args) -> tuple[list[PairResult], list[PairInfo]]:
    """Main pipeline orchestrator with round-based loop."""
    print(">> Set up attacker model client")
    openrouter_attack_model_markers = ("z-ai", "glm", "deepseek", "qwen", "google", "gemma")
    if args.attack_model_name.startswith("gpt"):
        attack_client = OpenAI()
    elif any(marker in args.attack_model_name for marker in openrouter_attack_model_markers):
        attack_client = OpenAI(
            api_key=os.getenv("OPENROUTER_API_KEY"),
            base_url="https://openrouter.ai/api/v1",
        )
    else:
        attack_client = OpenAI(base_url="http://localhost:8000/v1", api_key="EMPTY")
    repo_root = _find_repo_root()
    suite = get_suite(args.benchmark_version, args.suite)

    resolved_model_name = args.agent_model_name
    if "vllm_parsed" in args.agent_model_name or args.agent_model_name == "local":
        print(">> (VLLM  Only) Obtain agent model name")
        resolved_model_name = get_resolved_agent_model_name(args.agent_model_name)

    print(">>Initializing all savepath and determine cases (it, ut) to run/skip.")
    all_pairs, skipped_pairs = initialize_pairs(
        args,
        repo_root=repo_root,
        resolved_model_name=resolved_model_name,
    )

    if skipped_pairs:
        print(f"\nSkipping {len(skipped_pairs)} pairs with previous attack success:")
        for p in skipped_pairs:
            print(f"  - {p.user_task_id}/{p.injection_task_id}")

    if not all_pairs:
        print("\nNo pairs to process. All pairs already succeeded.")
        return [], skipped_pairs

    active_pairs = all_pairs.copy()
    total_pairs = len(active_pairs) + len(skipped_pairs)
    pair_to_snippet_name: dict[str, str] = {}
    all_results: list[PairResult] = []

    r0_start = time.time()
    print(f"\n{'='*60}")
    print(f"Round 0: Initial generation for {len(active_pairs)}/{total_pairs} pending pairs")
    print(f"{'='*60}")

    print(">> Round 0 injection creation building")
    round_results = round_0_exec(
        active_pairs,
        args=args,
        suite=suite,
        attack_client=attack_client,
        pair_to_snippet_name=pair_to_snippet_name,
        parallel=args.run_parallel,
        fail_fast=args.fail_fast,
    )
    all_results.extend(round_results)
    print(f"Round 0 time: {time.time() - r0_start:.1f}s")

    for round_num in range(1, args.max_rewrites + 1):
        rn_start = time.time()
        failed_pair_infos = collect_round_failures(round_results, active_pairs)
        if not failed_pair_infos:
            if all(result.security_passed for result in round_results):
                print(f"\nAll pairs succeeded! Stopping early at round {round_num - 1}.")
            else:
                print(
                    f"\nNo failures are eligible for another rewrite after round {round_num - 1}; "
                    "quarantined cases are listed in the final diagnostics."
                )
            break

        print(f"\n{'='*60}")
        print(f"Round {round_num}: Improvement generation for {len(failed_pair_infos)}/{total_pairs} remaining pairs")
        print(f"{'='*60}")

        round_results = round_n_exec(
            failed_pair_infos,
            round_num=round_num,
            args=args,
            suite=suite,
            attack_client=attack_client,
            resolved_model_name=resolved_model_name,
            pair_to_snippet_name=pair_to_snippet_name,
            parallel=args.run_parallel,
            fail_fast=args.fail_fast,
        )
        if not round_results:
            print(f"No bootstrap results produced in round {round_num}.")
            break
        all_results.extend(round_results)
        print(f"Round {round_num} total time: {time.time() - rn_start:.1f}s")

    return all_results, skipped_pairs


def print_summary(all_results: list[PairResult], skipped_pairs: list[PairInfo] | None = None) -> str:
    """Print final summary of utility and attack success rates. Returns summary string."""
    skipped_pairs = skipped_pairs or []
    num_skipped = len(skipped_pairs)
    latest_results: dict[str, PairResult] = {}
    attempts_by_round: dict[int, set[str]] = {}
    new_successes_by_round: dict[int, set[str]] = {}

    for result in all_results:
        key = f"{result.user_task_id}|{result.injection_task_id}"
        if key not in latest_results or result.round_num > latest_results[key].round_num:
            latest_results[key] = result
        attempts_by_round.setdefault(result.round_num, set()).add(key)
        if result.security_passed:
            new_successes_by_round.setdefault(result.round_num, set()).add(key)

    quarantined_categories = {
        "evaluator_error",
        "invalid_benchmark_case",
        "infrastructure_error",
    }
    valid_results = {
        key: result
        for key, result in latest_results.items()
        if result.failure is None or result.failure.category not in quarantined_categories
    }
    planned_pairs = len(latest_results) + num_skipped
    total_pairs = len(valid_results) + num_skipped
    security_passed = sum(1 for r in valid_results.values() if r.security_passed) + num_skipped
    skipped_utility = sum(
        1 for pair in skipped_pairs
        if pair.previous_result and pair.previous_result.get("utility_passed", False)
    )
    utility_passed = sum(1 for r in valid_results.values() if r.utility_passed) + skipped_utility

    attack_success_rate = (security_passed / total_pairs * 100) if total_pairs > 0 else 0
    utility_rate = (utility_passed / total_pairs * 100) if total_pairs > 0 else 0

    lines = []
    lines.append("=" * 60)
    lines.append("FINAL SUMMARY")
    lines.append("=" * 60)
    lines.append(f"Total pairs:          {total_pairs}")
    lines.append(f"Planned pairs:        {planned_pairs}")
    lines.append(f"Skipped (prev success): {num_skipped}")
    lines.append(f"Attack success rate:  {security_passed}/{total_pairs} ({attack_success_rate:.1f}%)")
    lines.append(f"Utility rate:         {utility_passed}/{total_pairs} ({utility_rate:.1f}%)")
    lines.append("")
    lines.append("Per-round progress:")
    if num_skipped > 0:
        lines.append(
            f"  skipped: newly succeeded {num_skipped}/{planned_pairs}, cumulative {num_skipped}/{planned_pairs}, remaining {planned_pairs - num_skipped}/{planned_pairs}"
        )

    cumulative_successes = num_skipped
    for rnd in sorted(attempts_by_round.keys()):
        attempted = len(attempts_by_round[rnd])
        newly_succeeded = len(new_successes_by_round.get(rnd, set()))
        cumulative_successes += newly_succeeded
        remaining = planned_pairs - cumulative_successes
        lines.append(
            f"  round {rnd}: attempted {attempted}/{planned_pairs}, newly succeeded {newly_succeeded}/{planned_pairs}, cumulative {cumulative_successes}/{planned_pairs}, remaining {remaining}/{planned_pairs}"
        )
    lines.append("")

    success_pairs = [f"{r.user_task_id}/{r.injection_task_id}" for r in valid_results.values() if r.security_passed]
    lines.append(f"Successful pairs ({len(success_pairs) + num_skipped}):")
    if num_skipped > 0:
        lines.append(f"  (+ {num_skipped} skipped pairs from previous runs)")
    for p in sorted(success_pairs):
        lines.append(f"  {p}")
    lines.append("")

    failed_pairs = [f"{r.user_task_id}/{r.injection_task_id}" for r in valid_results.values() if not r.security_passed]
    lines.append(f"Failed pairs ({len(failed_pairs)}):")
    for p in sorted(failed_pairs):
        lines.append(f"  {p}")

    category_labels = (
        ("generation_failure", "Generation failures"),
        ("evaluator_error", "Evaluator errors"),
        ("invalid_benchmark_case", "Invalid benchmark cases"),
        ("infrastructure_error", "Infrastructure errors"),
    )
    category_counts: dict[str, int] = {}
    for category, label in category_labels:
        category_results = [
            result for result in latest_results.values()
            if result.failure is not None and result.failure.category == category
        ]
        category_counts[category] = len(category_results)
        lines.append("")
        lines.append(f"{label} ({len(category_results)}):")
        for result in sorted(category_results, key=lambda r: (r.user_task_id, r.injection_task_id)):
            failure = result.failure
            lines.append(
                f"  {result.user_task_id}/{result.injection_task_id}: "
                f"stage={failure.stage} {failure.error_type}: {failure.error}"
            )

    lines.append("")
    lines.append("=" * 60)
    lines.append(f"ASR: {security_passed}/{total_pairs} ({attack_success_rate:.1f}%)")
    lines.append(f"U@A: {utility_passed}/{total_pairs} ({utility_rate:.1f}%)")
    lines.append(f"Generation failures: {category_counts['generation_failure']}")
    lines.append(f"Evaluator errors: {category_counts['evaluator_error']}")
    lines.append(f"Invalid benchmark cases: {category_counts['invalid_benchmark_case']}")
    lines.append(f"Infrastructure errors: {category_counts['infrastructure_error']}")
    lines.append(f"Planned / valid evaluated: {planned_pairs} / {total_pairs}")

    summary = "\n".join(lines)
    print(f"\n{summary}")
    return summary


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Search attack pipeline with batch API support.")
    p.add_argument("--suite", default="workspace")
    p.add_argument("--benchmark-version", default="v0.2.1")

    p.add_argument("--user-task-id", nargs='+', default=["user_task_0"])
    p.add_argument("--injection-task-id", nargs='+', default=["injection_task_1"])
    p.add_argument("--attack-model-name", default="gpt-5-mini")
    p.add_argument("--agent-model-name", default="gpt-4o-mini-2024-07-18")

    p.add_argument("--attack-method", default="generated_trajectory", help="Attack method.")
    p.add_argument("--rewrite-strategy", default="auto", choices=ALLOWED_STRATEGY_MODES, help="Rewrite strategy combination to use during generation.")
    p.add_argument("--max-rewrites", default=1, type=int, help="Maximum number of rewrites to attempt.")
    p.add_argument("--max-workers", default=4, type=int, help="Maximum number of parallel benchmark workers.")
    p.add_argument("--generation-max-concurrent", default=4, type=int, help="Maximum concurrent attacker-model generation requests.")

    p.add_argument("--defense", default=None, choices=[None, "tool_filter", "transformers_pi_detector", "spotlighting_with_delimiting", "repeat_user_prompt", "promptarmor"], help="Defense method.")

    p.add_argument("--out-dir", default=Path("res"), help="Output directory (relative to repo root unless absolute).")
    p.add_argument("--agent-logdir", default=Path("runs"), help="Output directory (relative to repo root unless absolute).")
    p.add_argument("--results-output", default=None, help="Path to save results JSONL file.")

    p.add_argument("--run-parallel", action="store_true", default=False, help="Run benchmark cases in parallel.")
    p.add_argument("--fail-fast", action="store_true", default=False, help="Abort immediately on generation/benchmark handling failures.")
    p.add_argument("--force-run", action="store_true", default=False, help="Force run even if pair already succeeded.")
    return p


def main():
    parser = _build_arg_parser()
    args = parser.parse_args()

    for k, v in vars(args).items():
        print(f"=={k}: {v}==")

    all_results, skipped_pairs = run_pipeline(args)
    summary = print_summary(all_results, skipped_pairs)

    if args.results_output:
        output_path = Path(args.results_output)
        save_results_jsonl(all_results, output_path)

    else:
        resolved_model_name = get_resolved_agent_model_name(args.agent_model_name)
        repo_root = _find_repo_root()
        attack_folder = resolve_attack_method_dir(args.attack_method, args.defense)
        rewrite_strategy_dir = resolve_rewrite_strategy_dir(args.rewrite_strategy)
        output_path = build_traj_suite_dir(
            repo_root=repo_root,
            output_subdir=args.out_dir,
            attack_method=attack_folder,
            rewrite_strategy=rewrite_strategy_dir,
            attack_model_name=args.attack_model_name,
            agent_model_name=resolved_model_name,
            suite_name=args.suite,
            benchmark_version=args.benchmark_version,
        ) / "pipeline_results.jsonl"
        save_results_jsonl(all_results, output_path)

    latest_results: dict[str, PairResult] = {}
    for result in all_results:
        key = f"{result.user_task_id}|{result.injection_task_id}"
        if key not in latest_results or result.round_num >= latest_results[key].round_num:
            latest_results[key] = result
    unresolved_failures = [
        result.failure for result in latest_results.values() if result.failure is not None
    ]
    save_failure_records_jsonl(unresolved_failures, output_path.parent / "failed_cases.jsonl")

    summary_path = output_path.parent / "summary_results.txt"
    with open(summary_path, "w") as f:
        f.write(summary + "\n")
    print(f"Summary saved to {summary_path}")


if __name__ == "__main__":
    filepath = Path(__file__).resolve()
    print(f"{filepath=}")
    main()
