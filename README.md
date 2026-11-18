# CoordiCEPS

CoordiCEPS is a benchmark for evaluating indirect prompt-injection attacks
against tool-using LLM agents. It provides simulated **banking**, **slack**,
**travel**, and **workspace** environments, attack implementations, defenses,
and generated trajectory-attack cases.

## Setup

Create the pinned Conda environment, activate it, and expose the source tree
to Python:

```bash
conda env create -f environment.yml
conda activate agentdojo
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
```

Runs call the configured model providers. Set the credential for the model(s)
you intend to use before running an experiment. For example, the supplied
trajectory artifacts and launcher defaults use OpenRouter model names:

```bash
export OPENROUTER_API_KEY="..."
```

The default iterative configuration uses `z-ai/glm-5.2` as the agent model
and `deepseek/deepseek-v4-pro` as the attack-generation model. Do not commit
credentials or generated runtime logs.

## Repository layout

| Path | Purpose |
| --- | --- |
| `src/agentdojo/default_suites/v0/` | Simulated benchmark suites and tools. |
| `src/agentdojo/attacks/traj_attack/` | Trajectory-attack generation, application, storage, and reporting code. |
| `scripts/run_traj_attack.sh` | Launcher for iterative generation and evaluation. |
| `scripts/apply_traj_attack.sh` | Launcher for replaying existing generated attacks on an evaluation model. |
| `scripts/report_traj_attack_models.sh` | Aggregates trajectory-attack result artifacts. |
| `scripts/verify/` | Optional utility and injection-task verification scripts. |
| `res/generated_trajectory/` | Pre-generated trajectory-attack artifacts. |

## Generated benchmark data

`res/generated_trajectory/` contains **2,400 generated benchmark cases**:

- 4 suites × 10 user tasks × 10 injection tasks × 6 rewrite strategies.
- Each case has four files: the site-selection result and prompt, plus the
  injection-generation result and prompt (9,600 files total).
- The supplied cases were generated with attack model
  `deepseek/deepseek-v4-pro` against agent model `z-ai/glm-5.2`.

The six strategy directories are:

```text
induced_dependency__coord_by_{repeat,handoff,breakdown}
induced_priority__coord_by_{repeat,handoff,breakdown}
```

For example, one case is stored below:

```text
res/generated_trajectory/<strategy>/attack_model/<attack-model>/agent_model/<agent-model>/<suite>/v0/user_task_<n>_injection_task_<m>/
```

## Run trajectory attack generation

Use `run_traj_attack.sh` to run the iterative pipeline. For each selected
user-task/injection-task pair, it selects injection sites, generates a rewrite,
benchmarks it, and retries unsuccessful pairs up to `MAX_REWRITES` times.
Results and generated artifacts are written under `res/`; agent execution logs
are written under `runs/`.

Run a small, explicit experiment first:

```bash
SUITES="banking" \
USER_TASK_IDS="user_task_0" \
INJECTION_TASK_IDS="injection_task_0" \
REWRITE_STRATEGIES="induced_dependency__coord_by_repeat" \
MAX_REWRITES=1 MAX_WORKERS=1 MAX_JOBS=1 \
./scripts/run_traj_attack.sh
```

Useful overrides include `AGENT_MODEL`, `ATTACK_MODEL`, `DEFENSE`,
`GENERATION_MAX_CONCURRENT`, `OUT_DIR`, `AGENT_LOGDIR`, and `FORCE_RUN=1`.
Without explicit task IDs, the launcher enumerates every task pair in each
selected suite. Its defaults therefore launch every strategy listed in
`REWRITE_STRATEGIES`; constrain the variables above before starting a large
run.

## Apply static, pre-generated attacks

Use `apply_traj_attack.sh` when generation is not desired. This mode reads the
static rewrite artifacts in `res/generated_trajectory/` and evaluates them on
the selected victim model. It does not call an attacker model to create new
rewrites.

For example, replay one supplied banking case against an evaluation model:

```bash
SUITES="banking" \
AGENT_MODEL="deepseek/deepseek-v4-flash" \
ARTIFACT_AGENT_MODEL="z-ai/glm-5.2" \
ARTIFACT_ATTACK_MODEL="deepseek/deepseek-v4-pro" \
USER_TASK_IDS="user_task_0" \
INJECTION_TASK_IDS="injection_task_0" \
REWRITE_STRATEGIES="induced_dependency__coord_by_repeat" \
MAX_WORKERS=1 MAX_JOBS=1 \
./scripts/apply_traj_attack.sh
```

Applied-case summaries are written under `res/generated_trajectory_applied/`,
and execution logs under `runs/`. Set `FORCE_RUN=1` to rerun completed cases.

## Report results

To aggregate trajectory artifacts, run:

```bash
PYTHONPATH="$PWD/src" ./scripts/report_traj_attack_models.sh
```

Use environment variables such as `SUITES`, `MODELS`, `REWRITE_STRATEGY`, and
`OUTPUT_CSV` to limit the report or write CSV output.

## Optional verification

The verification scripts check ordinary user tasks and injection tasks with and
without ground-truth replay. For one suite:

```bash
./scripts/verify/run_verify.sh banking
```

This starts four background checks. Individual scripts in `scripts/verify/`
accept overrides such as `MODEL`, `MODEL_ID`, and `USER_TASK_IDS`.

