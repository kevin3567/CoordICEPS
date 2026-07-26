from __future__ import annotations

from pathlib import Path


def resolve_rewrite_strategy_dir(rewrite_strategy: str | None) -> str:
    return rewrite_strategy or "auto"


def resolve_attack_method_dir(attack_method: str, defense: str | None = None) -> str:
    return f"{attack_method}-{defense}" if defense else attack_method


def _model_path_parts(*, attack_model_name: str | None, agent_model_name: str) -> list[str]:
    if attack_model_name is None:
        return [agent_model_name]
    return ["attack_model", attack_model_name, "agent_model", agent_model_name]


def build_traj_case_dir(
    *,
    repo_root: Path,
    output_subdir: str | Path,
    attack_method: str,
    rewrite_strategy: str | None,
    agent_model_name: str,
    attack_model_name: str | None = None,
    suite_name: str,
    benchmark_version: str,
    user_task_id: str,
    injection_task_id: str,
) -> Path:
    return (
        repo_root
        / Path(output_subdir)
        / attack_method
        / resolve_rewrite_strategy_dir(rewrite_strategy)
        / Path(*_model_path_parts(attack_model_name=attack_model_name, agent_model_name=agent_model_name))
        / suite_name
        / benchmark_version
        / f"{user_task_id}_{injection_task_id}"
    )


def build_traj_suite_dir(
    *,
    repo_root: Path,
    output_subdir: str | Path,
    attack_method: str,
    rewrite_strategy: str | None,
    agent_model_name: str,
    attack_model_name: str | None = None,
    suite_name: str,
    benchmark_version: str,
) -> Path:
    return (
        repo_root
        / Path(output_subdir)
        / attack_method
        / resolve_rewrite_strategy_dir(rewrite_strategy)
        / Path(*_model_path_parts(attack_model_name=attack_model_name, agent_model_name=agent_model_name))
        / suite_name
        / benchmark_version
    )


def build_traj_run_root(
    *,
    logdir: str | Path,
    attack_method: str,
    rewrite_strategy: str | None,
    agent_model_name: str,
    attack_model_name: str | None = None,
    suite_name: str,
    benchmark_version: str,
) -> Path:
    # AgentDojo appends pipeline_name/suite/user_task/attack/injection_task.json
    # below this root, so do not include agent_model_name or suite_name here.
    return (
        Path(logdir)
        / attack_method
        / resolve_rewrite_strategy_dir(rewrite_strategy)
        / "attack_model"
        / (attack_model_name or "unknown")
        / benchmark_version
    )


def build_traj_run_json_path(
    *,
    logdir: str | Path,
    attack_method: str,
    rewrite_strategy: str | None,
    agent_model_name: str,
    attack_model_name: str | None = None,
    suite_name: str,
    benchmark_version: str,
    user_task_id: str,
    injection_task_id: str,
) -> Path:
    return (
        build_traj_run_root(
            logdir=logdir,
            attack_method=attack_method,
            rewrite_strategy=rewrite_strategy,
            attack_model_name=attack_model_name,
            agent_model_name=agent_model_name,
            suite_name=suite_name,
            benchmark_version=benchmark_version,
        )
        / user_task_id
        / f"{injection_task_id}.json"
    )
