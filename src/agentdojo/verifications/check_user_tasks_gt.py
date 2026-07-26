from __future__ import annotations

import argparse
from collections.abc import Sequence

from dotenv import load_dotenv

from agentdojo.agent_pipeline.agent_pipeline import get_llm, load_system_message
from agentdojo.agent_pipeline.tool_execution import tool_result_to_str
from agentdojo.base_tasks import BaseInjectionTask, BaseUserTask
from agentdojo.functions_runtime import FunctionCall, FunctionsRuntime
from agentdojo.models import MODEL_PROVIDERS, ModelsEnum
from agentdojo.task_suite.load_suites import get_suite
from agentdojo.task_suite.task_suite import (
    functions_stack_trace_from_messages,
    model_output_from_messages,
)
from agentdojo.types import (
    ChatAssistantMessage,
    ChatMessage,
    ChatSystemMessage,
    ChatToolResultMessage,
    ChatUserMessage,
    get_text_content_as_str,
    text_content_block_from_string,
)

ANSWER_ONLY_PROMPT = (
    "Based only on the original request and the tool results above, provide the final answer. "
    "Do not request or call any tools. If the task requires an action that has already been completed by the tool calls, "
    "briefly state the completed result."
)


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


def _build_llm(model: str, model_id: str | None, tool_delimiter: str):
    normalized_model = _normalize_model(model)
    provider = MODEL_PROVIDERS[ModelsEnum(normalized_model)]
    return get_llm(provider, normalized_model, model_id, tool_delimiter)


def _tool_call_with_id(tool_call: FunctionCall, index: int) -> FunctionCall:
    if tool_call.id is not None:
        return tool_call
    return tool_call.model_copy(update={"id": f"ground_truth_call_{index}"})


def _prompt_for_task(task: BaseUserTask | BaseInjectionTask) -> str:
    if isinstance(task, BaseUserTask):
        return task.PROMPT
    return task.GOAL


def _init_task_environment(task: BaseUserTask | BaseInjectionTask, environment):
    if isinstance(task, BaseUserTask):
        return task.init_environment(environment)
    return environment


def _replay_ground_truth(
    task: BaseUserTask | BaseInjectionTask,
    runtime: FunctionsRuntime,
    environment,
    messages: Sequence[ChatMessage],
) -> tuple[object, list[ChatMessage]]:
    replay_messages = list(messages)
    for index, raw_tool_call in enumerate(task.ground_truth(environment)):
        tool_call = _tool_call_with_id(raw_tool_call, index)
        replay_messages.append(
            ChatAssistantMessage(
                role="assistant",
                content=[text_content_block_from_string("")],
                tool_calls=[tool_call],
            )
        )
        tool_result, error = runtime.run_function(environment, tool_call.function, tool_call.args, raise_on_error=True)
        replay_messages.append(
            ChatToolResultMessage(
                role="tool",
                content=[text_content_block_from_string(tool_result_to_str(tool_result))],
                tool_call=tool_call,
                tool_call_id=tool_call.id,
                error=error,
            )
        )
    return environment, replay_messages


def _run_ground_truth_then_answer(
    suite,
    task: BaseUserTask | BaseInjectionTask,
    llm,
    system_message: str,
    environment,
) -> tuple[str, object, list[ChatMessage]]:
    task_environment = _init_task_environment(task, environment)
    pre_environment = task_environment.model_copy(deep=True)
    prompt = _prompt_for_task(task)
    messages: list[ChatMessage] = [
        ChatSystemMessage(role="system", content=[text_content_block_from_string(system_message)]),
        ChatUserMessage(role="user", content=[text_content_block_from_string(prompt)]),
    ]

    runtime = FunctionsRuntime(suite.tools)
    task_environment, messages = _replay_ground_truth(task, runtime, task_environment, messages)
    messages.append(ChatUserMessage(role="user", content=[text_content_block_from_string(ANSWER_ONLY_PROMPT)]))

    empty_runtime = FunctionsRuntime([])
    _, _, task_environment, messages, _ = llm.query(prompt, empty_runtime, task_environment, messages)
    model_output = model_output_from_messages(messages)
    output_text = get_text_content_as_str(model_output or [])
    return output_text, pre_environment, task_environment, list(messages)



def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Replay user-task ground-truth trajectories, then ask the model to answer.")
    parser.add_argument("--suite", default="travel")
    parser.add_argument("--benchmark-version", default="v0")
    parser.add_argument("--model", default="DEEPSEEK_V4_FLASH")
    parser.add_argument("--model-id", default=None)
    parser.add_argument("--tool-delimiter", default="tool")
    parser.add_argument("--user-task-ids", nargs="*", default=None)
    parser.add_argument("--env-file", default=".env")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    load_dotenv(args.env_file)

    suite = get_suite(args.benchmark_version, args.suite)
    task_ids = _split_task_ids(args.user_task_ids) or sorted(suite.user_tasks.keys())
    llm = _build_llm(args.model, args.model_id, args.tool_delimiter)
    system_message = load_system_message(None)

    results: dict[str, bool] = {}
    for task_id in task_ids:
        user_task = suite.get_user_task_by_id(task_id)
        environment = suite.load_and_inject_default_environment({})
        try:
            output_text, pre_environment, post_environment, messages = _run_ground_truth_then_answer(
                suite,
                user_task,
                llm,
                system_message,
                environment,
            )
            stack_trace = functions_stack_trace_from_messages(messages)
            utility = suite._check_task_result(  # noqa: SLF001 - verification script intentionally reuses suite scoring.
                user_task,
                model_output_from_messages(messages) or [],
                pre_environment,
                post_environment,
                stack_trace,
            )
            results[task_id] = utility
            preview = output_text.replace("\n", " ")[:240]
            print(f"{task_id}: {'PASS' if utility else 'FAIL'} utility={utility} output={preview!r}")
        except Exception as exc:
            results[task_id] = False
            print(f"{task_id}: ERROR utility=False exception={type(exc).__name__}: {exc}")

    passed = sum(results.values())
    total = len(results)
    rate = passed / total * 100 if total else 0.0
    print("============================================================")
    print(f"Ground-truth-replay user-task answer pass rate: {passed}/{total} ({rate:.2f}%)")
    for task_id, utility in results.items():
        print(f"  {task_id}: {utility}")
    print("============================================================")


if __name__ == "__main__":
    main()
