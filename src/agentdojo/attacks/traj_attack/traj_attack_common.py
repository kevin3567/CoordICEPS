from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from openai import OpenAI, omit
from pydantic import BaseModel, Field, create_model

from agentdojo.agent_pipeline.ground_truth_pipeline import GroundTruthPipeline
from agentdojo.functions_runtime import FunctionsRuntime
from agentdojo.task_suite.load_suites import get_suite
from agentdojo.types import get_text_content_as_str
from agentdojo.attacks.traj_attack.traj_attack_paths import (
    build_traj_case_dir,
    build_traj_run_json_path,
    build_traj_run_root,
    build_traj_suite_dir,
    resolve_rewrite_strategy_dir,
)
from agentdojo.attacks.traj_attack.traj_attack_strategy import (
    AUTO_STRATEGY_MODE,
    CoordinationStructure,
    ImpetusMode,
    combo_key,
    selectable_strategy_combos,
    split_combo,
)


class TrajectoryLoader:
    def __init__(self, suite_name: str, benchmark_version: str = "v0.2.1") -> None:
        self.suite_name = suite_name
        self.benchmark_version = benchmark_version
        self.task_suite = get_suite(benchmark_version, suite_name)
        injection_defaults = self.task_suite.get_injection_vector_defaults()
        self.canary_injections = {inj: f"<CANARY---{inj}--->" for inj in injection_defaults}

    def get_user_trajectory(self, user_task_id: str) -> list[dict[str, Any]]:
        user_task = self.task_suite.get_user_task_by_id(user_task_id)
        gt_pipeline = GroundTruthPipeline(user_task)
        runtime = FunctionsRuntime(self.task_suite.tools)
        environment = self.task_suite.load_and_inject_default_environment(self.canary_injections)
        _, _, _, responses, _ = gt_pipeline.query(user_task.PROMPT, runtime, environment)

        trajectory_steps: list[dict[str, Any]] = []
        for idx, response in enumerate(responses):
            if response["role"] != "tool" or response["content"] is None:
                continue

            response_text = get_text_content_as_str(response["content"])
            observed_sites = [
                slot
                for slot, canary in self.canary_injections.items()
                if canary in response_text
            ]

            rendered_output = response_text
            for slot in observed_sites:
                rendered_output = rendered_output.replace(
                    self.canary_injections[slot],
                    f"{{{slot}}}",
                )

            trajectory_steps.append(
                {
                    "message_index": idx,
                    "tool_call": response.get("tool_call"),
                    "tool_output": {
                        "content": rendered_output,
                        "inj_cands": observed_sites,
                    },
                }
            )

        return trajectory_steps

    def get_injection_trajectory(self, injection_task_id: str) -> list[dict[str, Any]]:
        injection_task = self.task_suite.get_injection_task_by_id(injection_task_id)
        environment = self.task_suite.load_and_inject_default_environment({})
        tool_calls = injection_task.ground_truth(environment)

        return [
            {
                "step_index": idx,
                "tool_call": tool_call,
            }
            for idx, tool_call in enumerate(tool_calls, start=1)
        ]


def _format_tool_call(tool_call: Any) -> str:
    if tool_call is None:
        return "None"

    function_name = getattr(tool_call, "function", None)
    args = getattr(tool_call, "args", None)
    if function_name is not None and args is not None:
        return f"function='{function_name}' args={args}"

    return str(tool_call)


def format_injection_trajectory(injection_trajectory: list[dict[str, Any]]) -> str:
    if not injection_trajectory:
        return "No target-side tool calls."

    rendered_steps = []
    for step in injection_trajectory:
        rendered_steps.append(
            f"TARGET STEP {step['step_index']}\nTOOL CALL: {_format_tool_call(step['tool_call'])}"
        )
    return "\n\n".join(rendered_steps)


def format_user_trajectory(
    user_trajectory: list[dict[str, Any]],
    selected_sites: set[str] | None = None,
) -> tuple[str, list[str]]:
    rendered_steps: list[str] = []
    observed_sites_in_order: list[str] = []

    for step_idx, step in enumerate(user_trajectory, start=1):
        tool_call = step.get("tool_call")
        tool_output = step.get("tool_output", {})
        content = tool_output.get("content", "")
        step_sites = tool_output.get("inj_cands", [])

        if not step_sites:
            continue

        for site in step_sites:
            if site not in observed_sites_in_order:
                observed_sites_in_order.append(site)

        if selected_sites is not None:
            if not any(site in selected_sites for site in step_sites):
                continue
            for site in step_sites:
                if site not in selected_sites:
                    content = content.replace(f"{{{site}}}", "")

        rendered_steps.append(
            f"OBSERVED STEP {step_idx}\n"
            f"TOOL CALL: {_format_tool_call(tool_call)}\n"
            f"TOOL OUTPUT:\n{content}"
        )

    rendered_block = "\n\n".join(rendered_steps) if rendered_steps else "No observed rewrite sites."
    return rendered_block, observed_sites_in_order


def _find_repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "res").is_dir():
            return parent
    raise FileNotFoundError("Could not locate repo root containing res/.")


def load_site_metadata(suite_name: str) -> dict[str, dict[str, Any]]:
    suite_data_dir = _find_repo_root() / "src" / "agentdojo" / "data" / "suites" / suite_name
    vectors_path = suite_data_dir / "injection_vectors.yaml"
    metadata: dict[str, dict[str, Any]] = {}

    if not vectors_path.exists():
        return metadata

    with open(vectors_path, "r", encoding="utf-8") as f:
        vectors = yaml.safe_load(f) or {}
    if not isinstance(vectors, dict):
        return metadata

    for site, info in vectors.items():
        if not isinstance(info, dict):
            continue
        metadata[site] = {
            "description": info.get("description", ""),
            "co_def": info.get("co_def", []) or [],
        }

    return metadata


def expand_with_codef(site_ids: list[str], site_metadata: dict[str, dict[str, Any]]) -> list[str]:
    expanded: list[str] = []
    queue = list(site_ids)

    while queue:
        site = queue.pop(0)
        if site in expanded:
            continue
        expanded.append(site)
        for companion in site_metadata.get(site, {}).get("co_def", []):
            if companion not in expanded and companion not in queue:
                queue.append(companion)

    return expanded


def format_site_metadata_block(site_ids: list[str], site_metadata: dict[str, dict[str, Any]]) -> str:
    lines: list[str] = []
    for site in site_ids:
        metadata = site_metadata.get(site)
        if not metadata:
            continue

        line_parts = [f"- {site}"]
        if metadata.get("co_def"):
            line_parts.append(f"also_define={', '.join(metadata['co_def'])}")
        if metadata.get("description"):
            line_parts.append(f"item_info={metadata['description']}")
        lines.append('; '.join(line_parts))

    return "\n".join(lines) if lines else "- NONE"


def format_selectable_sites_block(
    observed_sites: list[str],
    optional_sites: list[str],
    site_metadata: dict[str, dict[str, Any]],
) -> str:
    lines: list[str] = []

    for site in observed_sites:
        lines.append(f"- {site} (observed)")

    for site in optional_sites:
        metadata = site_metadata.get(site, {})
        line_parts = [f"- {site} (optional)"]
        if metadata.get("co_def"):
            line_parts.append(f"also_define={', '.join(metadata['co_def'])}")
        if metadata.get("description"):
            line_parts.append(f"item_info={metadata['description']}")
        lines.append('; '.join(line_parts))

    return "\n".join(lines) if lines else "- NONE"


def get_select_site_schema(
    suite_name: str,
    observed_sites: list[str],
    strategy_mode: str = AUTO_STRATEGY_MODE,
) -> type[BaseModel]:
    allowed_sites: list[str] = []
    optional_sites = list(load_site_metadata(suite_name).keys())
    for site in [*observed_sites, *optional_sites]:
        if site not in allowed_sites:
            allowed_sites.append(site)

    if not allowed_sites:
        raise ValueError("No allowed sites available for site-selection schema.")

    SiteEnum = Enum(
        f"{suite_name.title()}SiteSelectionEnum",
        {site.upper(): site for site in allowed_sites},
        type=str,
    )

    allowed_combos = selectable_strategy_combos(strategy_mode)
    allowed_impetus_modes = sorted({split_combo(combo)[0] for combo in allowed_combos})
    allowed_coordination_structures = sorted({split_combo(combo)[1] for combo in allowed_combos})

    ImpetusEnum = Enum(
        f"{suite_name.title()}ImpetusModeEnum",
        {mode.upper(): mode for mode in allowed_impetus_modes},
        type=str,
    )
    CoordinationEnum = Enum(
        f"{suite_name.title()}CoordinationStructureEnum",
        {structure.upper(): structure for structure in allowed_coordination_structures},
        type=str,
    )

    class SitePlan(BaseModel):
        site: SiteEnum = Field(..., description="The selected observed or optional site ID.")
        plan: str = Field(..., description="Short site hint: local role, next-step cue, blend target.")

    class SiteSelectionResponse(BaseModel):
        impetus_mode: ImpetusEnum = Field(
            ...,
            description="Why the target task should be treated as necessary now.",
        )
        coordination_structure: CoordinationEnum = Field(
            ...,
            description="How selected sites coordinate across the trajectory.",
        )
        overall_design: str = Field(
            ...,
            description="Brief rationale for the overall ordered design of the selected sites.",
        )
        site_plans: list[SitePlan] = Field(
            ...,
            description="Short site hints matching the selected sites.",
        )

    return SiteSelectionResponse


def run_site_selection_prompt(
    prompt: str,
    schema: type[BaseModel] | None,
    model_name: str = "gpt-5-mini",
    client: OpenAI | None = None,
) -> BaseModel:
    if client is None:
        client = OpenAI()

    response = client.responses.parse(
        model=model_name,
        input=[{"role": "user", "content": prompt}],
        text_format=schema if schema else omit,
    )
    return response.output_parsed


def format_schema_fields(schema: type[BaseModel]) -> str:
    rendered = {}
    for name, field_info in schema.model_fields.items():
        annotation = getattr(field_info.annotation, "__name__", str(field_info.annotation))
        rendered[name] = annotation
    return json.dumps(rendered, indent=2, ensure_ascii=False)


def sanitize_injection_text(value: str) -> str:
    """Keep generated injection text safe for JSON persistence and YAML templating."""
    safe = value
    # Model outputs can contain literal escape backslashes such as \\'.
    # They are valid JSON strings but can break YAML double-quoted fields after injection.
    safe = safe.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n").replace("\\t", "\t")
    safe = safe.replace("\\", "")
    safe = safe.replace('"', "'")
    safe = safe.replace("\r\n", "\n").replace("\r", "\n")
    safe = "".join(
        ch
        for ch in safe
        if ch in "\n\t" or (ord(ch) >= 32 and not 0x7F <= ord(ch) <= 0x9F)
    )
    # Avoid YAML document separators inside quoted scalars.
    safe = safe.replace("\n---", "\n--")
    safe = safe.replace("\n...", "\n..")
    if safe.startswith("---"):
        safe = "--" + safe[3:]
    if safe.startswith("..."):
        safe = ".." + safe[3:]
    return safe


def get_create_injection_schema(selected_sites: list[str], suite_name: str):
    if not selected_sites:
        raise ValueError("No selected sites available for create-injection schema.")
    expanded_selected_sites = expand_with_codef(selected_sites, load_site_metadata(suite_name))

    class InjectionSiteText(BaseModel):
        title: str = Field(..., description="Human-readable label only; this value is not injected.")
        text: str = Field(..., description="Exact value injected into this site.")

    fields = {
        site: (
            InjectionSiteText,
            Field(..., description=f"Generated text for site {site}."),
        )
        for site in expanded_selected_sites
    }

    return create_model(
        "CreateInjectionResponse",
        __base__=BaseModel,
        **fields,
    )


def _load_json_if_exists(path: Path) -> dict[str, Any] | list[Any] | None:
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _iter_pair_case_dirs(
    *,
    repo_root: Path,
    attack_method: str,
    agent_model_name: str,
    attack_model_name: str | None = None,
    suite_name: str,
    benchmark_version: str,
    rewrite_strategy: str = "auto",
    output_subdir: str | Path = "res",
) -> list[Path]:
    base_dir = build_traj_suite_dir(
        repo_root=repo_root,
        output_subdir=output_subdir,
        attack_method=attack_method,
        rewrite_strategy=rewrite_strategy,
        attack_model_name=attack_model_name,
        agent_model_name=agent_model_name,
        suite_name=suite_name,
        benchmark_version=benchmark_version,
    )
    if not base_dir.exists():
        return []
    return sorted(
        d for d in base_dir.iterdir()
        if d.is_dir() and d.name.startswith("user_task_") and "_injection_task_" in d.name
    )


def _parse_pair_ids(case_dir: Path) -> tuple[str, str]:
    user_task_id, injection_task_suffix = case_dir.name.split("_injection_task_", maxsplit=1)
    return user_task_id, f"injection_task_{injection_task_suffix}"


def _latest_success_record(case_dir: Path) -> dict[str, Any] | None:
    results_file = case_dir / "results.jsonl"
    if not results_file.exists():
        return None

    latest_success = None
    with open(results_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record.get("security_passed", False):
                latest_success = record
    return latest_success


def find_nearest_success_examples(
    *,
    suite_name: str,
    agent_model_name: str,
    benchmark_version: str,
    current_user_task_id: str,
    current_injection_task_id: str,
    attack_method: str = "generated_trajectory",
    attack_model_name: str | None = None,
    rewrite_strategy: str = "auto",
    output_subdir: str | Path = "res",
    top_k: int = 2,
    prioritize_by: tuple[str, ...] = ("user_task", "injection_task"),
) -> list[dict[str, Any]]:
    repo_root = _find_repo_root()
    case_dirs = _iter_pair_case_dirs(
        repo_root=repo_root,
        attack_method=attack_method,
        attack_model_name=attack_model_name,
        agent_model_name=agent_model_name,
        suite_name=suite_name,
        benchmark_version=benchmark_version,
        rewrite_strategy=rewrite_strategy,
        output_subdir=output_subdir,
    )

    def priority_key(user_task_id: str, injection_task_id: str, strategy: str | None) -> tuple[int, ...]:
        same_user = int(user_task_id == current_user_task_id)
        same_injection = int(injection_task_id == current_injection_task_id)
        dimensions = {
            "user_task": 0 if same_user else 1,
            "injection_task": 0 if same_injection else 1,
            "strategy": 1 if strategy is None else 0,
        }
        return tuple(dimensions.get(dim, 1) for dim in prioritize_by) + (
            0 if same_user and same_injection else 1,
            user_task_id,
            injection_task_id,
        )

    examples: list[dict[str, Any]] = []
    for case_dir in case_dirs:
        user_task_id, injection_task_id = _parse_pair_ids(case_dir)
        if user_task_id == current_user_task_id and injection_task_id == current_injection_task_id:
            continue

        success_record = _latest_success_record(case_dir)
        if success_record is None:
            continue

        snippet_name = success_record.get("snippet_name")
        if not isinstance(snippet_name, str) or Path(snippet_name).name != snippet_name:
            continue
        version = snippet_name.removeprefix("ij_creation_").removesuffix(".json")
        if snippet_name != f"ij_creation_{version}.json" or not version[1:].isdigit():
            continue

        site_selection = _load_json_if_exists(case_dir / f"selected_sites_{version}.json")
        rewrite_payload = _load_json_if_exists(case_dir / snippet_name)
        if site_selection is None or rewrite_payload is None:
            continue
        strategy = None
        if isinstance(site_selection, dict):
            strategy = site_selection.get("strategy")
            impetus_mode = site_selection.get("impetus_mode")
            coordination_structure = site_selection.get("coordination_structure")
            if strategy is None and impetus_mode and coordination_structure:
                strategy = combo_key(impetus_mode, coordination_structure)

        examples.append(
            {
                "user_task_id": user_task_id,
                "injection_task_id": injection_task_id,
                "strategy": strategy,
                "site_selection": site_selection,
                "rewrite_payload": rewrite_payload,
                "result_record": success_record,
                "case_dir": str(case_dir),
            }
        )

    examples.sort(
        key=lambda ex: priority_key(
            ex["user_task_id"],
            ex["injection_task_id"],
            ex.get("strategy"),
        )
    )
    return examples[:top_k]
