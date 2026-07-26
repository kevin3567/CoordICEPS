from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentdojo.attacks.traj_attack.traj_attack_common import _find_repo_root


ALL = "all"
ERROR_CATEGORIES = (
    "generation",
    "evaluator",
    "infrastructure",
    "invalid_benchmark",
)


@dataclass(frozen=True)
class ExperimentKey:
    mode: str
    suite: str
    benchmark_version: str
    rewrite_strategy: str
    attack_method: str
    artifact_defense: str
    attack_model: str
    artifact_agent_model: str
    eval_attack_method: str
    eval_defense: str
    eval_agent_model: str


@dataclass
class Metrics:
    key: ExperimentKey
    total: int
    valid: int
    utility_true: int
    security_true: int
    sts_true: int
    generation_errors: int
    evaluator_errors: int
    infrastructure_errors: int
    invalid_benchmark_cases: int
    source_path: Path

    @property
    def asr(self) -> float:
        return self.security_true / self.valid if self.valid else 0.0

    @property
    def u_at_a(self) -> float:
        return self.utility_true / self.valid if self.valid else 0.0

    @property
    def sts(self) -> float:
        return self.sts_true / self.valid if self.valid else 0.0


def _matches(value: str, wanted: str) -> bool:
    return wanted == ALL or value == wanted


def _join_parts(parts: list[str]) -> str:
    return "/".join(parts) if parts else ""


def _parts_between(parts: list[str], start_marker: str, end_marker: str) -> list[str]:
    start = parts.index(start_marker) + 1
    end = parts.index(end_marker, start)
    return parts[start:end]


def _defense_from_attack_method(attack_method: str) -> str:
    prefix = "generated_trajectory-"
    if attack_method.startswith(prefix):
        return attack_method[len(prefix) :]
    return "nodefense"


def _parse_generated_path(path: Path, res_root: Path) -> ExperimentKey | None:
    attack_method = None
    rel = None
    for candidate in ("generated_trajectory", "generated_trajectory-promptarmor"):
        try:
            rel = path.relative_to(res_root / candidate)
            attack_method = candidate
            break
        except ValueError:
            continue
    if rel is None or attack_method is None:
        return None

    parts = list(rel.parts)
    if len(parts) < 8 or parts[-1] != "pipeline_results.jsonl":
        return None
    if "attack_model" not in parts or "agent_model" not in parts:
        return None

    rewrite_strategy = parts[0]
    attack_model = _join_parts(_parts_between(parts, "attack_model", "agent_model"))
    agent_start = parts.index("agent_model") + 1
    suite = parts[-3]
    benchmark_version = parts[-2]
    artifact_agent_model = _join_parts(parts[agent_start:-3])

    return ExperimentKey(
        mode="generated",
        suite=suite,
        benchmark_version=benchmark_version,
        rewrite_strategy=rewrite_strategy,
        attack_method=attack_method,
        artifact_defense=_defense_from_attack_method(attack_method),
        attack_model=attack_model,
        artifact_agent_model=artifact_agent_model,
        eval_attack_method=attack_method,
        eval_defense=_defense_from_attack_method(attack_method),
        eval_agent_model=artifact_agent_model,
    )


def _parse_applied_path(path: Path, res_root: Path) -> ExperimentKey | None:
    try:
        rel = path.relative_to(res_root / "generated_trajectory_applied")
    except ValueError:
        return None

    parts = list(rel.parts)
    if len(parts) < 16 or parts[-1] != "pipeline_results.jsonl":
        return None
    required = ("artifact_attack", "eval_attack", "attack_model", "artifact_agent_model", "eval_agent_model")
    if not all(marker in parts for marker in required):
        return None

    rewrite_strategy = parts[0]
    attack_method = _join_parts(_parts_between(parts, "artifact_attack", "eval_attack"))
    eval_attack_method = _join_parts(_parts_between(parts, "eval_attack", "attack_model"))
    attack_model = _join_parts(_parts_between(parts, "attack_model", "artifact_agent_model"))
    artifact_agent_model = _join_parts(_parts_between(parts, "artifact_agent_model", "eval_agent_model"))
    eval_start = parts.index("eval_agent_model") + 1
    suite = parts[-3]
    benchmark_version = parts[-2]
    eval_agent_model = _join_parts(parts[eval_start:-3])

    return ExperimentKey(
        mode="applied",
        suite=suite,
        benchmark_version=benchmark_version,
        rewrite_strategy=rewrite_strategy,
        attack_method=attack_method,
        artifact_defense=_defense_from_attack_method(attack_method),
        attack_model=attack_model,
        artifact_agent_model=artifact_agent_model,
        eval_attack_method=eval_attack_method,
        eval_defense=_defense_from_attack_method(eval_attack_method),
        eval_agent_model=eval_agent_model,
    )


def _load_latest_pair_records(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            record = json.loads(line)
            user_task_id = record.get("user_task_id")
            injection_task_id = record.get("injection_task_id")
            if not user_task_id or not injection_task_id:
                continue
            latest[(user_task_id, injection_task_id)] = record
    return latest


def _failure_category(record: dict[str, Any]) -> str | None:
    failure = record.get("failure")
    if isinstance(failure, dict):
        category = failure.get("category")
        if isinstance(category, str):
            return category
    return None


def _compute_metrics(key: ExperimentKey, path: Path) -> Metrics:
    records = _load_latest_pair_records(path)
    error_counts = {category: 0 for category in ERROR_CATEGORIES}
    valid = 0
    utility_true = 0
    security_true = 0
    sts_true = 0

    for record in records.values():
        category = _failure_category(record)
        if category in error_counts:
            error_counts[category] += 1
            continue
        if record.get("failure") or record.get("error"):
            error_counts["infrastructure"] += 1
            continue

        valid += 1
        utility = bool(record.get("utility_passed"))
        security = bool(record.get("security_passed"))
        utility_true += int(utility)
        security_true += int(security)
        sts_true += int(utility and not security)

    return Metrics(
        key=key,
        total=len(records),
        valid=valid,
        utility_true=utility_true,
        security_true=security_true,
        sts_true=sts_true,
        generation_errors=error_counts["generation"],
        evaluator_errors=error_counts["evaluator"],
        infrastructure_errors=error_counts["infrastructure"],
        invalid_benchmark_cases=error_counts["invalid_benchmark"],
        source_path=path,
    )


def discover_metrics(args: argparse.Namespace) -> list[Metrics]:
    repo_root = Path(args.root_dir).resolve()
    res_root = repo_root / args.res_dir
    paths: list[Path] = []

    if args.mode in ("generated", "both"):
        paths.extend((res_root / "generated_trajectory").glob("**/pipeline_results.jsonl"))
        paths.extend((res_root / "generated_trajectory-promptarmor").glob("**/pipeline_results.jsonl"))
    if args.mode in ("applied", "both"):
        paths.extend((res_root / "generated_trajectory_applied").glob("**/pipeline_results.jsonl"))

    metrics: list[Metrics] = []
    for path in sorted(paths):
        key = (
            _parse_generated_path(path, res_root)
            if "generated_trajectory_applied" not in path.parts
            else _parse_applied_path(path, res_root)
        )
        if key is None:
            continue
        if not _matches(key.suite, args.suite):
            continue
        if not _matches(key.benchmark_version, args.benchmark_version):
            continue
        if not _matches(key.rewrite_strategy, args.rewrite_strategy):
            continue
        if not _matches(key.attack_model, args.attack_model):
            continue
        if not _matches(key.artifact_agent_model, args.artifact_agent_model):
            continue
        if not _matches(key.eval_agent_model, args.eval_agent_model):
            continue
        if not _matches(key.attack_method, args.attack_method):
            continue
        if not _matches(key.eval_attack_method, args.eval_attack_method):
            continue
        if not _matches(key.artifact_defense, args.artifact_defense):
            continue
        if not _matches(key.eval_defense, args.defense):
            continue
        metrics.append(_compute_metrics(key, path))

    return metrics


def _pct(value: float) -> str:
    return f"{100 * value:.1f}%"


def _error_total(metric: Metrics) -> int:
    return (
        metric.generation_errors
        + metric.evaluator_errors
        + metric.infrastructure_errors
        + metric.invalid_benchmark_cases
    )


def print_metrics(metrics: list[Metrics]) -> None:
    if not metrics:
        print("No matching traj-attack result files found.")
        return

    metrics_by_defense: dict[tuple[str, str], list[Metrics]] = {}
    for metric in metrics:
        key = (metric.key.artifact_defense, metric.key.eval_defense)
        metrics_by_defense.setdefault(key, []).append(metric)

    headers = [
        "mode",
        "suite",
        "strategy",
        "eval_model",
        "valid/total",
        "ASR",
        "U@A",
        "STS",
        "errors",
    ]
    for defense_key in sorted(metrics_by_defense):
        artifact_defense, eval_defense = defense_key
        rows = []
        for metric in sorted(
            metrics_by_defense[defense_key],
            key=lambda item: (
                item.key.mode,
                item.key.suite,
                item.key.eval_agent_model,
                item.key.rewrite_strategy,
            ),
        ):
            rows.append(
                [
                    metric.key.mode,
                    metric.key.suite,
                    metric.key.rewrite_strategy,
                    metric.key.eval_agent_model,
                    f"{metric.valid}/{metric.total}",
                    _pct(metric.asr),
                    _pct(metric.u_at_a),
                    _pct(metric.sts),
                    str(_error_total(metric)),
                ]
            )

        widths = [len(header) for header in headers]
        for row in rows:
            widths = [max(width, len(value)) for width, value in zip(widths, row)]

        print(f"artifact_defense={artifact_defense} eval_defense={eval_defense}")
        print("  ".join(header.ljust(width) for header, width in zip(headers, widths)))
        print("  ".join("-" * width for width in widths))
        for row in rows:
            print("  ".join(value.ljust(width) for value, width in zip(row, widths)))
        print()


def _csv_row(metric: Metrics) -> dict[str, Any]:
    return {
        "mode": metric.key.mode,
        "suite": metric.key.suite,
        "benchmark_version": metric.key.benchmark_version,
        "rewrite_strategy": metric.key.rewrite_strategy,
        "attack_method": metric.key.attack_method,
        "artifact_defense": metric.key.artifact_defense,
        "attack_model": metric.key.attack_model,
        "artifact_agent_model": metric.key.artifact_agent_model,
        "eval_attack_method": metric.key.eval_attack_method,
        "eval_defense": metric.key.eval_defense,
        "eval_agent_model": metric.key.eval_agent_model,
        "total": metric.total,
        "valid": metric.valid,
        "security_true": metric.security_true,
        "utility_true": metric.utility_true,
        "sts_true": metric.sts_true,
        "asr": metric.asr,
        "u_at_a": metric.u_at_a,
        "sts": metric.sts,
        "generation_errors": metric.generation_errors,
        "evaluator_errors": metric.evaluator_errors,
        "infrastructure_errors": metric.infrastructure_errors,
        "invalid_benchmark_cases": metric.invalid_benchmark_cases,
        "source_path": str(metric.source_path),
    }


def write_csv(metrics: list[Metrics], output_csv: Path) -> None:
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    rows = [_csv_row(metric) for metric in metrics]
    if not rows:
        output_csv.write_text("", encoding="utf-8")
        return
    with output_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"CSV written to {output_csv}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Report ASR, U@A, and STS for traj-attack experiment artifacts.")
    parser.add_argument("--root-dir", default=None, help="AgentDojo repo root. Defaults to detected repo root.")
    parser.add_argument("--res-dir", default="res", help="Result artifact directory relative to root.")
    parser.add_argument("--mode", choices=["generated", "applied", "both"], default="both")
    parser.add_argument("--suite", default=ALL)
    parser.add_argument("--benchmark-version", default=ALL)
    parser.add_argument("--rewrite-strategy", default=ALL)
    parser.add_argument("--attack-method", default=ALL)
    parser.add_argument("--eval-attack-method", default=ALL)
    parser.add_argument("--artifact-defense", default=ALL)
    parser.add_argument("--defense", default=ALL)
    parser.add_argument("--attack-model", default=ALL)
    parser.add_argument("--artifact-agent-model", default=ALL)
    parser.add_argument("--eval-agent-model", default=ALL)
    parser.add_argument("--output-csv", default=None)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.root_dir is None:
        args.root_dir = str(_find_repo_root())

    metrics = discover_metrics(args)
    print_metrics(metrics)
    if args.output_csv:
        write_csv(metrics, Path(args.output_csv))


if __name__ == "__main__":
    main()
